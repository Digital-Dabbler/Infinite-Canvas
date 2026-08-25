# Angle control RunningHub NanoBanana 2 low-price model

## Goal

Make the smart-canvas "adjust subject angle" dialog's online mode always use
RunningHub's NanoBanana 2 Gemini 3.1 Flash image-to-image low-price model:

`nano-banana2-gemini31flash/image-to-image-channel-low-price`

## Scope

- Change only the fixed online model identifier returned by
  `angleSettingsForTarget()` in `static/js/smart-canvas.js`.
- Update the adjacent explanatory comment to name the new model.

## Non-goals

- Do not change the provider (`runninghub`), aspect ratio (`adaptive`),
  resolution (`2k`), capability (`model`), prompt construction, input-image
  wiring, or the local ComfyUI branch.
- Do not change API-profile model administration, credentials, model registry,
  or the standalone angle-control page.

## Data flow

When the user selects "online model" in the angle dialog and generates an
image, the new output node will retain the existing API settings but use the
new fixed model identifier. The normal RunningHub image-to-image request,
provider authorization, and result lifecycle remain unchanged.

## Validation

Run JavaScript syntax validation for `static/js/smart-canvas.js` and
`git diff --check`. Browser generation is not run unless an authenticated
session and a chargeable RunningHub test configuration are explicitly made
available.
