# Canvas server terminal-state sync repair

## Scope

Repair ordinary API image/video tasks, including RunningHub model mode. Direct
RunningHub workflows, ComfyUI, loop, and cascade completion flows are not
changed.

## Problem

The server can atomically attach a completed task, then a browser with an old
canvas snapshot can save stale `pendingTasks` and `logs`. This revives the
spinner and discards the server-created generation-history row while preserving
some image URLs.

## Design

1. In client-side canvas merging, server-managed pending tasks are authoritative
   from the server snapshot. Local-only pending records remain only for task
   families not managed by the server.
2. Canvas logs merge by stable log ID or `local_task_id`, newest first, instead
   of replacing server logs with a stale client list.
3. The save route removes incoming server-managed pending records whose durable
   task is terminal, so an old browser cannot revive them after completion.
4. A scoped reconciliation scans completed bound tasks for the current canvas:
   it restores a missing terminal result/log from the durable task result and
   removes the corresponding stale pending entry. It never creates a deleted
   node or submits an upstream task.

## Guarantees

- A task ID can create at most one terminal history row.
- A server-managed task cannot transition from terminal back to pending through
  a browser save.
- Existing local-completion task families retain their current merge behavior.
- Reconciliation is idempotent and only touches an existing bound node.

## Verification

- Unit tests cover stale-pending rejection, log union, idempotent
  reconciliation, and deleted-node safety.
- Run smart-canvas and RunningHub lifecycle tests plus JavaScript/Python syntax
  and diff checks.
