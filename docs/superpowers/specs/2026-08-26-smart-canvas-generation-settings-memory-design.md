# Smart-canvas generation settings memory

## Goal

New smart-canvas generation nodes restore the most recently used compatible settings for the same generation scenario in the same canvas. The first use of an image scenario defaults to 2K only when the selected model supports 2K. Video defaults remain unchanged.

## Scope

- Applies to new image and video generation nodes in the smart canvas Composer.
- Persists only in the current canvas, so a refresh or reopening that canvas retains its remembered settings.
- Separates settings by generation kind, execution engine/provider, and whether the node currently has image reference input.
- Does not copy prompt text, node links, input media, task state, result media, or historical node `runSettings`.

## Data model

Add a versioned canvas-owned settings-memory map. Each key identifies:

1. image or video generation;
2. engine and provider;
3. text-only or image-reference mode.

Each value is a sanitized snapshot of generation controls for that context. It excludes transient task fields, prompt and reference fields, and unsupported capability fields.

Existing `canvas.settings`, browser-local recent settings, and each node's `runSettings` remain readable for compatibility. The new map is the only source used to seed a newly created node under the new behavior. Existing nodes always retain their own settings.

## Resolution and capability handling

For a context without a saved snapshot:

- Image generation chooses 2K if the selected model's advertised resolution options include 2K.
- If 2K is unavailable or capability discovery has a narrower legal default, use that legal default instead.
- Video generation uses the current video defaults without a 2K override.

When a saved snapshot references a disabled provider, removed model, or unsupported option, existing model/capability normalization selects the first valid compatible value. Generation must not be blocked by an obsolete saved choice.

## Scenario transitions

Opening a new node starts from the context determined by its current inputs. Adding an image input changes it from text mode to reference mode and restores that context's snapshot; removing the final image input restores text mode. The existing model filtering rules remain authoritative, so text-only models never appear as a selected reference-model and vice versa.

## Persistence and isolation

The memory map is serialized with the canvas through the existing authenticated canvas save/load path. It is therefore scoped to canvas ownership rather than browser-global local storage. It must never be used to grant provider/model access; server-side configuration-group validation remains unchanged.

## Compatibility and safety

- No migration rewrites old canvas settings or existing node settings, including historic 4K choices.
- A canvas with no new map behaves as a fresh context for this feature, preventing an old global 4K setting from becoming an implicit new-node default.
- Dedicated operations such as angle generation, outpainting, background removal, workflows, loop outputs, and regenerated historical nodes keep their explicit `runSettings` and bypass generic new-node seeding.

## Verification

Automated coverage will assert context isolation, first-use defaults, valid fallback, canvas reload persistence, and no mutation of existing node settings. Manual smart-canvas checks will cover text-to-image and image-to-image transitions, video defaults, refresh/reopen persistence, and an existing canvas with prior 4K nodes.
