# ComfyUI execution-stage failover

## Goal

When the background-removal workflow has been accepted by a ComfyUI backend but then fails during execution because that backend is missing a required model or cannot execute a required node, retry the same task on every remaining configured backend in order. Stop at the first successful result. Do not retry generic empty-output or workflow-definition errors.

## Scope

- Reuse the existing `/api/generate` execution chain used by `/api/canvas-comfy-tasks`.
- Preserve the current submission-stage retry behavior for connection, gateway, and `prompt_outputs_failed_validation` compatibility errors.
- Add execution-stage inspection of the accepted backend's ComfyUI history record.
- Apply the behavior to all local ComfyUI workflows, including `system/抠图_api.json`; do not add a separate client path for background removal.

## Design

After a backend returns `prompt_id`, poll its history on that same backend as today. Before collecting files, inspect the history status and node error data. A retry is eligible only when ComfyUI explicitly reports either a missing-model failure or a node-execution failure. The server records that backend as temporarily incompatible for the workflow and proceeds through every remaining configured backend, synchronizing required input media before each attempt.

The retry order remains the selected best backend followed by every other configured backend. It is not capped at two addresses. Each accepted submission keeps its own history polling and output download on the backend that accepted it.

If no backend produces an output, return the most useful ComfyUI error collected across attempts. A generic empty result, absent history output, malformed workflow, or field-mapping problem does not trigger execution-stage retries unless ComfyUI explicitly classifies it as a missing-model or node-execution failure.

## Observability

Task timing metadata will retain the existing `submission_attempts` fields and add execution-stage retry attempts with backend, prompt ID, elapsed time, and sanitized ComfyUI error detail. The final task will retain the successful backend, or the aggregated failure detail if all configured backends fail.

## Validation

- Add focused unit coverage for a first backend that reports an eligible execution failure and a later backend that succeeds.
- Cover traversal across three configured addresses and confirm that retry stops on the first success.
- Cover generic empty output and malformed-workflow errors to confirm that they do not retry.
- Run the focused tests, Python syntax check, and `git diff --check`.
