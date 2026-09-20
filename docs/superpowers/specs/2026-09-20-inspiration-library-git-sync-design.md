# Inspiration Library Git Sync

## Problem

The 灵感库 of both the prompt library and the workflow library does not travel reliably with a
Git checkout. Three independent defects, plus one unrelated serving defect that makes the first
two look like a browser fault.

1. **Workflow publications live entirely outside the tracked tree.** `save_workflow_library()`
   writes `data/workflow_library.json`, and `workflow_archive_copy()` / `workflow_cover_copy()`
   write into `assets/workflow_library/public/`. `.gitignore` excludes both `data/*` and
   `assets/`, so a fresh clone shows an empty workflow inspiration tab.

2. **Prompt publications sync a manifest without their covers.**
   `save_versioned_published_prompts()` writes the tracked
   `static/data/prompt-library-published.json`, but `prompt_public_cover_copy()` copies covers
   into `static/images/prompt-library/published/`, which no code path ever stages. After a pull
   the manifest is present while every `cover_url` points at a file that does not exist. The one
   commit that did carry covers (`708a683`) is on the unmerged branch
   `codex/pre-rollback-20260904-1730` and is deliberately not recovered.

3. **Reads merge the tracked catalog with a machine-local runtime copy.**
   `merged_published_prompt_snapshots()` unions the tracked manifest with
   `data/prompt_libraries.json["published"]`. Two writers for one logical record mean a
   withdrawal on one machine is undone by a stale runtime copy on another. The workflow library
   has no tracked layer at all, so it has no merge; it simply never syncs.

4. **`.webp` is served as `text/plain`.** `app.mount("/static"|"/assets"|"/output",
   StaticFiles(...))` resolves media types through Starlette's `FileResponse`, which computes
   `mimetypes.guess_type(path)[0] or "text/plain"`. The bundled CPython 3.10 has no `.webp`
   mapping (the `HKCR\.webp` key exists on the reference machine but carries no `Content Type`
   value), so a cover request answers `Content-Type: text/plain; charset=utf-8`. Verified against
   the running server:

   ```
   HEAD /static/images/prompt-library/style_ue5.webp  ->  200, Content-Type: text/plain; charset=utf-8
   ```

   `content_type_for_path()` maps `.webp` by hand, but no static mount routes through it. Machines
   whose Python build or registry does know `.webp` behave correctly, which is why the fault looks
   machine-dependent and intermittent.

## Design

### 1. One publication, one tracked file

Every published artifact moves under `static/`, split into one file per snapshot:

```
static/data/prompt-library-published/<snapshot_id>.json
static/data/prompt-library-withdrawn/<snapshot_id>.json
static/images/prompt-library/published/<snapshot_id>_cover.webp

static/data/workflow-library-published/<snapshot_id>.json
static/data/workflow-library-withdrawn/<snapshot_id>.json
static/images/workflow-library/published/<snapshot_id>_cover.webp
static/workflow-library/published/<snapshot_id>.zip
```

Several machines publish independently, so the manifest must not be a single JSON array: two
machines appending to the same array conflict on every pull. With one file per snapshot, two
independent publications are two new files and Git merges them automatically; a withdrawal is a
file deletion plus one new tombstone file, which also merges automatically.

The tombstone file is deterministic — `{"id": "<snapshot_id>"}` and nothing else, no timestamp.
Two machines withdrawing the same snapshot then write byte-identical files. The time of the
withdrawal is recoverable from `git log`.

`static/data/prompt-library-system.json` and `static/images/prompt-library/*.webp` are unchanged.
The old single manifest `static/data/prompt-library-published.json` is removed (§7).

Personal drafts (`data/prompt_libraries.json["libraries"]`, `data/workflow_library.json["workflows"]`),
favourites, and trash stay machine-local and are not synced, per `AGENTS.md`. Their files keep
their current locations: private workflow packages stay in `assets/workflow_library/private/`
served as `/assets/...`, and personal prompt covers stay wherever the owner put them.

One `LIBRARY_TRACKED_PATHS` constant lists the seven paths above. It is the single source for the
read scan, the integrity self-check, and the Git pathspec, so the three cannot drift apart.

`PROMPT_LIBRARY_PUBLIC_DIR` (`assets/prompt_library/public`) has no reference anywhere in
`main.py` and is deleted.

### 2. Reading

`published_prompt_snapshots(data)` and `published_workflow_snapshots(data)`:

1. Scan `static/data/*-published/*.json`, sorted by id so list order is stable across machines.
2. Drop every id present in `static/data/*-withdrawn/`.
3. Union in this machine's runtime `data/*.json["published"]` entries that are not tombstoned.
   This is what keeps content visible on a machine that has not run the sync action yet.
4. The tracked file wins when both layers hold the same id.
5. Any `cover_url` / `archive_url` whose file is absent on disk is downgraded to `""` at read
   time. The frontend already has a cover placeholder and an unavailable-resource branch, so a
   missing file renders as a placeholder instead of a broken reference.

Steps 2 and 3 exist as a pair. Step 3 alone would resurrect withdrawn publications from stale
runtime copies; step 2 alone would hide content that has not been migrated yet.

`workflow_public_view()` serves `published` (the viewer's own) and `inspiration` (everyone's) from
this merged view, matching `public_prompt_libraries_for_user()`.

### 3. Writing

- **Publish** writes the tracked paths directly: cover copy, workflow package copy, and a new
  `<snapshot_id>.json`. Nothing is written to the runtime `published` array any more, so a new
  publication has exactly one representation from the moment it exists.
- **Withdraw** deletes `<snapshot_id>.json` and the matching cover/package, then writes
  `static/data/*-withdrawn/<snapshot_id>.json`. Idempotent; withdrawing twice is not an error.
- Publish and withdraw only touch working-tree files. They never write the index and never invoke
  Git. Git participates only when the administrator runs the sync action.

### 4. Asset re-encoding

Covers are re-encoded at publish and migration time with the existing PIL dependency: RGB, longest
edge 1024, WebP quality 82. This keeps the tracked library the same order of magnitude as the
existing `static/images/prompt-library/*.webp` covers (25–330 KB) instead of the 1.6–2.7 MB PNGs
the old publish path produced.

Workflow packages are rebuilt on the public copy only: image resources inside the zip are
re-encoded to WebP (longest edge 2048, quality 90) and `workflow.json` is rewritten so that each
resource's `archive`, `name`, and `size` match the new file. `url` is deliberately left untouched,
because `import_canvas_workflow()` maps resources by `url` when rewriting node references and
locates files by `archive`. The import path therefore needs no change. The private archive is
never rewritten: it is what the owner's canvas references.

Measured on `published_2a4c42d165674f.zip`: 14.9 MB total, of which 14.3 MB is four PNG reference
images (3.37–3.84 MB each) and 22 KB is `workflow.json`. PNG is already compressed, so the zip
container adds ~2 %; re-encoding is the only lever.

Both re-encoders are best-effort. A source that PIL cannot open, or a resource that is not an
image, is copied through unchanged rather than failing the publish.

### 5. Admin sync action

A new panel in the `system` workspace of `static/admin.html`, registered in
`workspaceElements.system` in `static/js/admin-dashboard-v2.js` so workspace switching controls it.

`GET /api/admin/library-sync` (`require_admin`) reports:

- whether `git` is on PATH, whether the project root is a work tree, and the current branch;
- the number of uncommitted changes under the tracked library paths
  (`git status --porcelain -- <paths>`), which is the "you should press the button" signal;
- how many runtime publications have not been migrated yet, per library;
- an integrity self-check: tracked manifest entries whose cover or package file is missing from
  disk. This is the check that catches "the manifest was committed but the cover was not", which
  is exactly the half-synced state reported by users.

`POST /api/admin/library-sync` (`require_admin`):

1. **Migrate.** For every runtime publication that is not tombstoned, write it into the tracked
   layout and remove it from the runtime JSON. A publication whose `archive_url` or `cover_url` no
   longer resolves falls back to its `source_workflow_id`'s private package and cover — verified to
   work for `published_c3e06d05885c4f` and `published_64e09f5f7b8b49`, whose public files are gone
   from this machine while `workflow_d10c9a2f548e4b` and `workflow_256a80bd6f8348` remain. If no
   source survives, the field is written empty rather than dropping the record; the card then shows
   the unavailable-resource state.
2. **Purge.** Delete runtime publications whose id has a tombstone.
3. `git add -A -- <tracked library paths that exist>`.
4. `git commit -m "chore(library): sync inspiration library" -- <same paths>`.
   The pathspec is what keeps the action from committing unrelated staged work: with
   `git commit -- <paths>`, only those paths are committed and the rest of the index is untouched.

   The pathspec is filtered to paths that exist on disk, because Git treats a non-existent
   pathspec as an error and an empty directory cannot be tracked. A library with nothing published
   yet therefore syncs to `changed: false` instead of failing.
5. Return the commit hash, the file count, and the tail of Git's output.

There is no commit when nothing changed: the response reports `changed: false`.

`subprocess.run` runs under `asyncio.to_thread`, with `cwd` pinned to the project root. Git's
stderr tail is returned verbatim on failure — a held `index.lock` or a missing `user.name` must be
visible, not collapsed into a 500.

**Push is not performed.** Remote credentials belong to the deployment, and pushing publishes the
whole repository rather than this one action.

### 6. Static media types

At import time in `main.py`, before the mounts are created:

```python
for _ext, _mime in ((".webp", "image/webp"), (".avif", "image/avif")):
    mimetypes.add_type(_mime, _ext)
```

`StaticFiles`/`FileResponse` call `mimetypes.guess_type` per request, so this fixes `/static`,
`/assets`, and `/output` at once. Verified: after `add_type`, `starlette.responses.guess_type('a.webp')`
returns `image/webp` where it previously returned `None`.

### 7. Migration of existing data

- The tracked single file `static/data/prompt-library-published.json` (four entries) is split into
  four `static/data/prompt-library-published/*.json` files and deleted by the implementation commit
  itself. It is a tracked file, so this cannot be left to a runtime migration. Those four entries
  have empty `cover_url` and no cover exists in any ref; they migrate cover-less and render the
  placeholder.
- Machine-local runtime publications are migrated by the sync action and are never lost before it
  runs, because the read path already merges them.
- Migration is idempotent: entries are removed from the runtime JSON as they are written.

Measured on the reference machine: one prompt publication
(`published_dd79aadc656b4f`「Playrix Township 风格转换」, 吴和蕊, 2026-08-17) whose cover is not
recoverable, and three workflow publications, two of which need the private-source fallback.

### 8. Frontend

- Covers in both inspiration grids get an `onerror` handler that swaps in the existing placeholder,
  so even a stale reference cannot render a broken image.
- The admin panel described in §5, with a busy state and a confirmation step, since the action
  creates a commit.
- New user-facing strings are added to `static/js/i18n/library.js` (zh and en) and read through
  `t()` with a Chinese fallback.
- `?v=` cache stamps are left alone; `sync_static_html_versions()` owns them.

## Out of Scope

- Recovering anything from `codex/pre-rollback-20260904-1730`. The five covered prompt
  publications and two covered workflow publications on that branch were removed deliberately and
  stay removed.
- Syncing personal drafts, favourites, trash, users, departments, or any other runtime state.
- `git push`, remote configuration, or credentials.
- Editing published metadata after publication, or republishing from 我的发布.
- Git LFS. Covers and packages are re-encoded instead.

## Risks

- Two machines publishing different snapshots: distinct new files, merges cleanly.
- Two machines withdrawing the same snapshot: identical tombstone bytes plus the same deletion,
  merges cleanly.
- A manual `git commit` racing the sync action: `index.lock` contention returns a readable error;
  the action does not retry, so the failure is not masked.
- WebP bytes are not guaranteed identical across PIL versions, but a given publication's cover is
  encoded once, by the machine that published it.
- A re-encoded workflow package contains lower-fidelity reference images than the private original.
  This is a deliberate trade for repository size and is recorded here as a known behaviour change.

## Verification

- `ast.parse` on `main.py` (not import, which writes to disk), the repo-wide `node --check` sweep,
  `node static/js/i18n/validate-i18n.js`, `git diff --check`.
- New `tests/test_library_git_sync.py`, running real `git` in a temporary repository and skipping
  when `git` is absent: migration writes the tracked layout, re-encodes covers, and empties the
  runtime array; a withdrawal tombstone removes the record from the read view and purges the stale
  runtime copy; a pathspec commit leaves unrelated staged content staged; no changes produces
  `changed: false` and no commit; a non-repository or missing `git` returns a readable error rather
  than a 500.
- New `tests/test_static_media_mime.py`: `.webp` resolves to `image/webp`.
- Updated `tests/test_prompt_library_publication.py` and
  `tests/test_workflow_library_publication.py` for the directory layout and tombstone semantics.
- Running-server check: `curl -I /static/images/prompt-library/style_ue5.webp` reports
  `Content-Type: image/webp`.
- Browser check in the smart-canvas 灵感库: opening both libraries shows no download prompts and no
  broken covers, and the workflow inspiration tab lists a pulled publication on a second clone.
  This step is performed by the user: the sandbox blocks Chrome's named-pipe IPC, so no headless
  browser can be launched to confirm it here.
