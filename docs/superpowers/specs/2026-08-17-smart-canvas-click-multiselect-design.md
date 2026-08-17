# Smart Canvas click multi-select

## Goal

Let users build or reduce a node selection by holding Shift, Ctrl, or Cmd while clicking a node. This complements the existing box selection without replacing it.

## Interaction rules

- A plain node click keeps the current single-select behavior: the clicked node becomes the only selected node.
- Shift+click, Ctrl+click, and Cmd+click toggle the clicked node in the selection.
- Toggling an unselected node adds it. Toggling a selected node removes it.
- When the remaining selection contains one node, that node is represented by the existing single-selection state; an empty result clears the selection.
- Modifier-clicking clears any selected image thumbnail, since the selection now represents nodes rather than a specific image result.

## Compatibility boundaries

- Keep Shift-dragging on blank canvas as connection erasing.
- Keep Ctrl/Cmd-dragging on blank canvas as box selection.
- Do not change node dragging, Alt-drag duplication, ports, buttons, controls, text editing, or image thumbnail/preview interactions.
- Update the localized shortcut hint so it describes both modifier-click multi-select and the current box-selection gesture.

## Implementation and verification

- Centralize the toggle behavior in the existing node click handler in `static/js/smart-canvas.js`, reusing `selectedId`, `selectedIds`, and `syncSelectionUi()`.
- Update the `smart.shortcutBoxSelect` Chinese and English strings in `static/js/i18n/smart-canvas.js`.
- Verify with JavaScript syntax checking, i18n validation, and a focused browser check: add, remove, and plain-click reset selections; confirm Shift blank-canvas connection erasing and Ctrl/Cmd box selection still work.
