# Workflow Library Unpublish

## Problem

The workflow library modal (`#workflows-library-overlay` in `static/smart-canvas.html`)
declares its fourth tab as `data-tab="myPublished"`, but `static/js/workflow-library.js`
compares `this.tab` against the literal `'my\u53d1\u5e03ed'` (`my发布ed`). The tab key never
matches, so:

- 「我的发布」 falls through to the inspiration branch and renders every user's published
  snapshots instead of the viewer's own publications.
- `card()` classifies each render as "someone else's item", so published cards only offer
  应用 / 收藏 and never 撤回.
- The existing backend withdraw endpoints and the frontend `withdraw` action are
  unreachable from the UI.

The backend already supports unpublishing: `DELETE /api/workflow-library/published/{snapshot_id}`
removes the public snapshot and its files, and `PATCH /api/workflow-library/{workflow_id}/publish`
with `{"published": false}` removes the snapshot for the owning workflow. Both only accept the
owner.

## Design

Align the workflow library with the prompt library (`static/js/prompt-library.js`), which
already implements publish/withdraw for prompts.

1. **Tab key fix.** `WorkflowLibrary.items()` and `card()` compare against `myPublished`.
   `myWorkflows`, `favorites`, and `inspiration` keep their current meaning.

2. **我的发布 cards.** The card renders a `danger` styled `取消发布` button (lucide
   `rotate-ccw`) in a management row that stays visible without hovering, mirroring
   `prompt-card-published-manage`. The button confirms through
   `library.unpublishWorkflowConfirm`, calls
   `DELETE /api/workflow-library/published/{snapshot_id}` with the snapshot id, surfaces
   `library.withdrawFailed` on failure, reuses the returned `library` payload to re-render,
   and toasts `library.workflowUnpublishedToast`. Cards keep `应用` (the apply endpoint
   already accepts published snapshot ids) and show the publish date.

3. **我的工作流 cards.** When the viewer has a snapshot whose `source_workflow_id` matches
   the workflow, the card shows an `is-published` `已发布` button instead of `发布`; clicking
   it switches to 我的发布 and focuses the matching `取消发布` button. Otherwise the card
   keeps `发布` / `删除`. This mirrors `library.published` / `data-pl-show-publication`.

4. **Empty states.** Each tab gets a specific empty hint; 我的发布 explains that published
   workflows are managed there.

5. **i18n.** New user-facing text is added to `static/js/i18n/library.js` (zh/en), and the
   workflow library reads every label through `t()` with a Chinese fallback instead of
   hardcoded escapes.

6. **Styling.** Reuse `library-card-actions`, `.danger`, and `.is-published`; add only the
   minimal rule needed for the published manage row in `static/css/workflow-library.css`.

## Out of Scope

- Changing the snapshot data model (a snapshot stays an independent copy with its own id).
- Republishing from 我的发布, editing published metadata, or trash integration.
- Backend authorization changes; the existing owner checks already satisfy the contract.

## Verification

- `node --check static/js/workflow-library.js` plus the repo-wide JS syntax sweep, and
  `node static/js/i18n/validate-i18n.js`.
- New `tests/test_workflow_library_publication.py`: publish creates an independent snapshot
  visible to other users' inspiration but not their 我的发布; withdraw removes the snapshot
  and its files; another user cannot withdraw; `published: false` removes the owner's
  snapshot; the viewer's 我的发布 list contains only their own snapshots. A frontend contract
  test asserts the modal's `data-tab` values are the keys the script handles, so the mangled
  `myPublished` key cannot come back.
- Manual browser check in the smart-canvas 工作流库 modal: the 我的发布 tab lists only the
  current user's publications with a working 取消发布 action.
