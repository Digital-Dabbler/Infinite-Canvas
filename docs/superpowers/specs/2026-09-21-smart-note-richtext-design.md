# Smart Note Rich Text (PureRef Note semantics)

## Problem

The canvas `smart-note` is a plain `<textarea>` inside a card. Its behaviour contradicts the
model the canvas is being moved to — PureRef's *Note*: a real canvas text object that wraps
inside a frame the user sizes, supports rich text, and disappears into the background when not
being edited. Concretely:

1. **The resize handle changes font size, not the frame.** `static/js/smart-canvas.js:23605-23611`
   computes `node.fontSize = clamp(round(startFontSize * sqrt(widthRatio * heightRatio) * 10) / 10,
   10, 48)` from the drag, so a frame the user sized by hand is not a frame at all. Even that is
   then thrown away: `fitSmartNoteToText()` (`static/js/smart-canvas.js:8637`) rewrites `node.w` /
   `node.h` on every keystroke (`static/js/smart-canvas.js:13441-13449`), so the next character
   typed re-measures the note to `targetWidth = clamp(longestLine + 28, 160, 640)`.
2. **Text is flat.** `node.text` is one string: no bold, no headings, no lists, no checkboxes, no
   links, no alignment. There is no rich text editor anywhere in the repo — the only
   `execCommand` uses are `document.execCommand('copy')` (`static/js/smart-canvas.js:479`,
   `static/js/smart-canvas.js:500`, `static/js/admin-dashboard-v2.js:465`), and there is no HTML
   sanitizer on either side of the wire.
3. **Long notes are silently clipped.** `targetHeight` in `fitSmartNoteToText()`
   (`static/js/smart-canvas.js:8637`) is capped at 720, while `.smart-note-text` is
   `overflow: hidden` with `resize: none` (`static/css/smart-canvas.css:2221-2238`). Past that
   height the text is unreachable.
4. **Pasting is all-or-nothing.** Clipboard HTML would land verbatim; the repo has no whitelist.
5. **A permanent toolbar costs canvas space.** `canvasOrganizerHtml()`
   (`static/js/smart-canvas.js:11918`) always renders `organizerColorButtons(node)` plus a copy
   button plus a delete button, whether or not the note is selected.

The canvas is shared (multiple editors, `apply_canvas_node_operation` in `main.py:7259`), so any
HTML rendered from a node field is attacker-controlled input. The design below never renders
stored HTML.

## Design

### 1. Data model

A `smart-note` node gains four fields; everything else is unchanged.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `richText` | string | absent | Whitelisted HTML subset (§5), at most 20000 characters. |
| `text` | string | existing | Plain-text mirror of `richText`, regenerated on every edit. Unchanged type and meaning. |
| `sizeMode` | `'auto'` \| `'fixed'` | `'auto'` | Whether the frame width follows the content. |
| `bgAlpha` | int 0–100 | 20 | Background opacity in percent; `0` is text on the bare canvas. |

`node.text` keeps being written, because every existing consumer reads it: the copy button
(`smartTextCopyButtonHtml('.smart-note-text')`), `fitSmartNoteToText()`, the minimap, node search
and export. Nothing outside the note node needs to learn about `richText`.

Old notes have `text` and no `richText` / `sizeMode` / `bgAlpha`. They render as plain text with
`sizeMode = 'auto'` and `bgAlpha = 20`, and are upgraded in place the first time the user edits
them. There is no migration script and no bulk rewrite: a note the user never touches keeps its
old fields forever, which is what makes this change safe on a shared canvas where other editors
may still be running the old frontend.

### 2. Editing

- `.smart-note-text` becomes a `<div class="smart-note-text" contenteditable="…">` instead of a
  `<textarea>`. The class name is kept so the CSS and the copy button keep working.
- **One mode at a time.** `canvasOrganizerHtml()` renders `contenteditable="false"` unless the
  node's id is in `noteEditingIds`, so a note at rest is a plain canvas item: its body drags the
  node, it scales like an image, and its text is not selectable. `beginSmartNoteEdit()` — called
  from the second press of a double click (the `mousedown` whose `detail >= 2`), from the `dblclick`
  handler, and from `createSmartNote()`, which therefore opens ready to type — flips the attribute
  to `"true"`, focuses the editor and drops the caret where the double-click landed
  (`document.caretRangeFromPoint`, falling back to the browser's own default). The second press is
  what drives this, rather than the `dblclick` event alone, because the selection re-render between
  the two clicks replaces the card element and the event is then not guaranteed to reach the
  current DOM. The drag guard skips `.smart-note-text[contenteditable="true"]` only and turns that
  second press into the editor instead of a drag, so a passive body click starts a drag while a
  double-click opens the editor. The body cursor is `move` at rest and `text` while editing.
- **Leaving edit mode**: blur, `Escape`, or a press anywhere outside the card. The canvas pan and
  box-select gestures call `preventDefault()` on `mousedown`, so the browser never moves focus by
  itself; a capture-phase `document` listener blurs the editor explicitly when a press lands
  outside the note. Presses on the floating bar (which preserves its own selection with
  `preventDefault()`) and presses on the card itself count as inside. The `blur` handler restores
  `contenteditable="false"` and drops the `textbox` role, because a blur alone does not re-render.
- **Editing chrome.** While the body is editable the card carries a focus ring, driven from the DOM
  state (`.smart-note-node:has(.smart-note-text[contenteditable="true"])`) rather than from a
  second class that would have to be kept in sync.
- **IME composition must not be interrupted.** `render()` rebuilds node DOM; for prompt nodes the
  repo already guards this with `promptTextEditingIds` (`static/js/smart-canvas.js:11944-11959`),
  which keeps a node's DOM alive while it is being edited. The note path gets the same treatment:
  a note whose id is in the note-editing set is skipped by the rebuild, so Chinese composition is
  not destroyed mid-character.
- **`input` handler.** Derive `richText` from the editor DOM and `text` from
  `editor.innerText`; recompute geometry only when `sizeMode === 'auto'` (§3); then
  `renderMinimap()`, `scheduleConnectionLayerRefresh()`, `scheduleSave()`. `scheduleSave()` keeps
  using the existing debounced `node_fields` operation, so the sync protocol is untouched:
  `apply_canvas_node_operation()` (`main.py:7259`) merges arbitrary fields
  (`updated = {**nodes[node_index], **fields}`, `main.py:7300-7315`) and is idempotent per
  `operation_id`, which is exactly the granularity a new string field needs.
- **Paste is filtered before insertion, not after.** `paste` calls `preventDefault()`, takes
  `text/html` (falling back to `text/plain`), runs it through the client whitelist (§5), inserts
  the result with `document.execCommand('insertHTML')`, then prunes the editor in place once more.
  `insertHTML` rather than the Range API so the paste stays on the browser's undo stack; the
  second prune catches whatever the parser restructured on the way in. Bold, italic,
  strikethrough, paragraphs, lists, checklists, heading levels and links survive; fonts, colours,
  images and tables do not.
- **Selection floating bar.** When the selection inside a note is non-empty, a small
  `position: fixed` bar appears over the note with: bold, italic, strikethrough, four size steps,
  alignment, bullet list, numbered list, checklist, link. It re-measures every frame from the
  selection's client rect, so panning, zooming and typing keep it attached; it disappears on blur,
  on a collapsed selection, or when the selection leaves the note. Size and alignment are
  paragraph-level (`data-fs` / `data-align` on the enclosing block, §5) and are not the node's
  base `fontSize`, which this bar never touches. Bold/italic/strikethrough run with
  `styleWithCSS=false`, so the browser emits `<b>`/`<i>`/`<strike>` and the whitelist normalises
  them to `<strong>`/`<em>`/`<s>` instead of storing inline styles.
- **The 20000-character cap is a `beforeinput` guard on the client, not just a server rule**, so a
  note can never hold content the server would reject on the next save.
- **Node menu.** Selecting a note shows a compact menu at its top-right corner with the six
  palette colours (the same `organizerColor` values), the `bgAlpha` slider, the
  auto/fixed size toggle, delete, and copy plain text. The permanent toolbar in
  `canvasOrganizerHtml()` (`static/js/smart-canvas.js:11918`) is removed; the existing
  `organizerColorButtons()` / `smartTextCopyButtonHtml()` markup is reused inside the menu.

### 3. Size semantics

- **`sizeMode = 'auto'`** (new notes): width is measured from the content, as today, and the height
  is measured from the *rendered* body. `measureNoteContentHeight()` sets the card to
  `height: auto`, reads the editor's `scrollHeight` and adds the two 12px paddings; the
  canvas-`measureText` estimate inside `fitSmartNoteToText()` is only the fallback for when there
  is no DOM to measure. The floor is `NOTE_MIN_HEIGHT = 44`, one body line plus padding. The old
  routine reserved a fixed `toolbarHeight = 39` row and a 110px floor, which is why a note always
  stood taller than its text.
- **Handle drag — uniform outside edit mode.** At rest, any drag on `.node-resize-handle` scales the
  whole note (width, height and `fontSize` by one factor), so a note resizes like an image and its
  text scales with it. While the note is being edited, a plain drag sets the width and leaves the
  height content-driven (grows downward, never clips) and only a modifier drag scales uniformly.
  The floor is applied to the *factor* —
  `minFactor = max(NOTE_MIN_WIDTH / startW, NOTE_MIN_HEIGHT / startH, 10 / startFontSize)` — rather
  than to width and height separately, so the aspect ratio survives a shrink all the way down to
  the smallest allowed font.
- **Any handle drag sets `sizeMode = 'fixed'`** — the equivalent of PureRef's "Auto size" going off
  once the user has resized by hand — otherwise the next auto measurement would snap the frame back
  around the text. A non-uniform drag then re-fits the height (grow-only,
  `fitNoteHeightToContent()`); a uniform drag needs no re-fit, because the font and the frame scale
  by the same factor.
- **Font size** is per run, as the floating bar's four steps (§2); the node's own base `fontSize`
  (clamped 10–48 by `smartNoteFontSize()`) changes only through a uniform drag, and drives
  `--note-font-size` for the whole note.
- **No clipping, ever.** The `overflow: hidden` in `static/css/smart-canvas.css` never hides text,
  because the height is at least the content height. The note floors are now 160×44
  (`NOTE_MIN_WIDTH` / `NOTE_MIN_HEIGHT`) instead of 160×110.

### 4. Visual

- Minimal card: weak border that firms up on hover, smaller radius, no heavy drop shadow. The
  background is driven by `--note-bg-alpha` from `bgAlpha` instead of the current flat
  `color-mix(var(--organizer-color) 22%, var(--panel))`; at `bgAlpha = 0` the note is bare text
  and the border and shadow fade out with it.
- The six colours no longer fill the card; they tint the border, the text selection colour and the
  minimap swatch.
- All of it is CSS variables on `.smart-note-node`, so the existing light/dark theme variables
  (`--panel` and friends) keep working.

### 5. Sanitisation

One whitelist, two implementations, one shared corpus.

- **Allowed tags:** `strong`, `em`, `s`, `ul`, `ol`, `li`, `h1`, `h2`, `h3`, `p`, `div`, `br`,
  `a`. `p` and `div` are in the list because they are the paragraphs of the editing model, not
  decoration: Chromium emits a new `<div>` for every Enter pressed inside a `contenteditable`
  note and pasted prose is usually `<p>`. Unwrapping them would silently merge every paragraph
  into one line on the next save, so both are kept as plain block containers that may carry the
  paragraph attributes below.
- **Allowed attributes:** `a[href]`, `li[data-checked="true"|"false"]`, and `data-fs` /
  `data-align` on block elements. `data-fs` holds one of four steps (`1`–`4`, rendered as
  0.85× / 1× / 1.25× / 1.6× of `--note-font-size`); `data-align` holds `left`, `center` or
  `right`. Every other attribute is dropped, **including all `style`** — size and alignment are
  stored as `data-*` and turned into semantic classes by the renderer. That removes the whole
  CSS injection surface (`position: fixed`, `url(...)`, overlay tricks) rather than trying to
  filter it.
- **Checklists** are `li[data-checked="true"|"false"]`, not `<input>`, so no form element is ever
  created from stored HTML.
- **Links:** `href` must be `http:`, `https:`, `mailto:`, or a same-origin relative path.
  `javascript:` and `data:` are dropped. An anchor stores nothing but `href` — `target`, `rel`,
  `class` and every `on*` handler are dropped like any other attribute. A link opens on
  Ctrl/Cmd+click: `http(s)` goes through `window.open(href, '_blank', 'noopener')` and anything
  else navigates the current tab, so the opener isolation comes from the opening call instead of
  from a stored attribute. A plain click stays inside the editor for caret placement.
- **Client rendering path.** `richText` is never assigned to `innerHTML`. A single
  `buildNoteFragment(html)` parses the string, prunes it, and creates DOM nodes, and *that* is the
  only path from stored data to the canvas — for local input, for a peer's broadcast, and for a
  server echo alike.
- **Server validation.** `validate_canvas_node_fields()` (`main.py:7193`) gets a `smart-note`
  special case next to the existing `directorScene` / `directorThumb` ones: `richText` runs
  through `sanitize_note_richtext()` (stdlib `html.parser`, ~80 lines, no new dependency — the
  repo must stay offline-installable and build-step free), longer than 20000 characters is a
  `400`, `sizeMode` must be one of the two literals, `bgAlpha` is clamped to 0–100. The sanitizer
  is the same whitelist as the client's; a shared case file keeps them honest.

### 6. Compatibility

- Prompt nodes and workflow groups do not take this path; their `contenteditable` handling and
  their rendering are untouched.
- The canvas outline no longer lists notes at all (`renderSmartOutline()`,
  `static/js/smart-canvas.js:11923`), so it is unaffected.
- **A stale frontend may edit a rich note.** An old client writes `text` and knows nothing about
  `richText`, and `node_fields` merges (`main.py:7300-7315`), so the two can disagree for a while.
  `richText` wins when present, and the first edit from a current client rebuilds `text` from the
  editor DOM, so the mismatch heals on its own instead of needing a schema version.
- The static cache stamp (`main.py:3206`, the maximum `st_mtime_ns` of the smart-canvas assets)
  picks the changed files up automatically; no version bump is needed.
- i18n: new labels go into `static/js/i18n/smart-canvas.js` (zh/en), used through `t()` with a
  Chinese fallback, and `node static/js/i18n/validate-i18n.js` must stay green.

## Out of Scope

- Parenting a note to a node or group, and moving a note with its parent.
- Collapsing or hiding notes.
- Showing checklist state in the canvas outline.
- Real-time co-editing of one note (cursors, character-level merge); the existing field-level,
  last-writer-wins `node_fields` operation is the synchronisation model.
- Images, tables, font families and text colours inside a note.
- Markdown import/export and any server-side rendering of note content.

## Verification

- `node --check static/js/smart-canvas.js`, `node --check static/js/i18n/smart-canvas.js`, and
  `node static/js/i18n/validate-i18n.js`.
- `python -m unittest` for the canvas modules. Note: the sandbox denies `mkdir` inside
  `tempfile.mkdtemp()` directories, which breaks `setUp` for `tests/test_canvas_field_deletion.py`
  and `tests/test_static_cache_stamp.py`; that is an environment limitation, not a product fault.
- Server-side validation of the note fields rides in the same file, through
  `validate_canvas_node_fields()`: `richText` over 20000 characters or of the wrong type is a
  `400`, `sizeMode` only accepts the two literals, and `bgAlpha` is clamped.
- `tests/test_smart_note_richtext.py` carries all of the above, server and client in one file
  (35 cases, the `tests/test_workflow_group_text_run.py` `extract_function()` harness): the shared
  corpus in `tests/fixtures/smart-note-richtext-cases.json` gives the same result on both sides;
  script tags, event-handler attributes, `style` attributes, `javascript:` / `data:` hrefs,
  `<iframe>`, `<img>`, tables and font tags are stripped while bold / italic / strike / lists /
  checklists / h1-h3 / `br` / safe links survive; oversized input is rejected and `sizeMode` /
  `bgAlpha` are validated; `node.richText` is never assigned to `innerHTML`; edit mode is entered
  only from the double-click path and left by blur, `Escape` and outside presses; a new note opens
  in edit mode; auto-sizing measures the rendered body and no longer carries the 110px or toolbar
  floor; the passive/editing CSS split is in place.
- Manual browser check in the smart canvas: double-click to edit, click the body to drag without
  entering edit mode, click empty canvas and press `Escape` to leave edit mode, drag the corner to
  scale a note proportionally (text scales with it), type Chinese with an IME mid-note, paste from
  a web page, size a frame by hand and keep typing, drag the background to `0`, and confirm a
  second browser session sees the same rich text.
