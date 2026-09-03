# Workflow Create Dialog Event Binding Fix

## Problem

The smart-canvas workflow-create dialog is declared after the synchronous
`smart-canvas.js` script tag. Its direct event-listener registration therefore
runs before the close, cancel, confirm, backdrop, and name input elements
exist. The dialog can open, but none of those controls responds.

## Design

Register the dialog controls after DOM parsing completes. Preserve the current
dialog markup, payload construction, `/api/workflow-library` request, and
success/error behavior. The delayed binding covers close, cancel, backdrop
dismissal, character-count updates, and confirmation.

## Verification

Run JavaScript syntax validation and exercise each dismissal path plus a
successful create request in the browser. The change must not alter canvas
state or existing workflow-library records except when the user confirms a new
workflow.
