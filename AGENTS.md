# AGENTS.md

Guidance for AI coding agents working in `hero8152/Infinite-Canvas`.

## Project Overview

Infinite Canvas is a local web application for AI image, video, workflow, canvas, chat, and asset-library workflows. The GitHub remote is `https://github.com/hero8152/Infinite-Canvas.git`.

The app is intentionally simple to run:

- Backend: one large FastAPI app in `main.py`.
- Frontend: static HTML/CSS/JavaScript in `static/`, served directly by FastAPI.
- Runtime data: JSON state and user files under `data/`, `assets/`, `output/`, and `history.json`.
- Workflows: ComfyUI/RunningHub workflow JSON and optional `.config.json` files under `workflows/`.
- Bundled runtime: Windows packages, wheels, and a portable `python/` directory may exist for non-developer users.

There is no Node build step, package manager config, or formal test suite in the current project.

## Important Paths

- `main.py`: FastAPI app, API routes, provider integrations, update flow, ComfyUI/RunningHub/Jimeng/Codex/Gemini helpers, canvas persistence, asset libraries, prompt libraries, and startup migrations.
- `static/*.html`: top-level pages served by `/` or linked from the UI.
- `static/js/*.js`: page logic. The largest files are `canvas.js` and `smart-canvas.js`.
- `static/css/*.css`: page styles plus shared theme CSS.
- `static/js/i18n/*.js` and `static/js/i18n-core.js`: translation bundles and runtime.
- `static/vendor/`: local mirrors of Tailwind CDN, Lucide, Three.js, and fonts. Keep the app offline/CDN-independent.
- `workflows/`: built-in and custom workflow definitions. Built-ins are protected by backend delete checks.
- `static/runninghub/`: static RunningHub provider metadata and thumbnails.
- `API/.env`: local API/provider secrets and user config. Treat as private runtime state.
- `data/`: user/runtime JSON stores, including canvases, conversations, provider overlays, prompt libraries, asset libraries, update backups, and staging.
- `assets/`: uploaded/imported media and asset-library files.
- `output/`: generated output files.
- `CLI/` and `tools/`: helper installers and login scripts for Codex CLI, Gemini CLI, Jimeng CLI, and browser import tooling.
- `packages/` and `python/`: bundled/offline dependency/runtime artifacts. Do not reformat or casually modify them.

## Run Commands

From the repository root:

```powershell
.\启动服务.bat
```

or:

```powershell
.\run.bat
```

Both scripts prefer `python\python.exe` if present and fall back to system `python`. The app listens on:

```text
http://127.0.0.1:3000/
```

Manual backend run:

```powershell
python main.py
```

If dependencies are missing on a source checkout, use:

```powershell
.\安装依赖.bat
```

The installer prefers local wheels in `packages/` and may fall back to network installation.

## Validation

There is no configured automated test suite. For most changes, validate by:

1. Start the server with `python main.py` or `.\启动服务.bat`.
2. Confirm `http://127.0.0.1:3000/` loads.
3. Exercise the affected page and API route.
4. Watch terminal output for FastAPI validation errors, upstream provider errors, and traceback logs.

Useful smoke endpoints/pages:

- `/api/app-info`
- `/api/config`
- `/api/providers`
- `/api/comfyui/instances`
- `/api/workflows`
- `/api/canvases`
- `/api/asset-library`
- `/api/prompt-libraries`
- `/static/canvas.html`
- `/static/smart-canvas.html`
- `/static/api-settings.html`
- `/static/asset-manager.html`

For Python syntax-only checks, run:

```powershell
python -m py_compile main.py
```

For frontend changes, use browser smoke testing. There is no transpiler or bundler to catch syntax errors.

## Backend Conventions

- Keep the single-file `main.py` structure unless the user explicitly asks for a larger refactor.
- Prefer adding small helper functions near related logic instead of introducing broad abstractions.
- API request/response models are Pydantic `BaseModel` classes grouped before route sections.
- Paths must be resolved under `BASE_DIR`, `DATA_DIR`, `ASSETS_DIR`, `OUTPUT_DIR`, `STATIC_DIR`, or `WORKFLOW_DIR` as appropriate.
- Be careful with file path sanitization. Existing helpers such as `sanitize_export_filename`, `normalize_local_image_path`, `safe_update_target`, and workflow-name validation exist for a reason.
- Long-running or blocking provider work is often wrapped with async helpers, polling loops, or background task dictionaries. Preserve the UI polling contracts.
- Several providers have special protocol handling: `openai`, `apimart`, `gemini`, `gemini-cli`, `volcengine`, `runninghub`, `jimeng`, and `codex`.
- WebSocket `/ws/stats` broadcasts canvas, online-count, and asset-library updates. If changing persistence flows, consider whether a broadcast is expected.
- Startup runs migrations for asset-library folders and media extension cleanup. Avoid adding expensive startup work unless needed.

## Frontend Conventions

- This project uses direct browser JavaScript. Do not add Node, bundlers, package-lock files, or CDN dependencies unless the user asks.
- Keep third-party assets local under `static/vendor/`.
- Pages load shared scripts such as `theme.js`, `i18n.js`, `touch-mouse.js`, `image-preview.js`, and page-specific JS files.
- Many pages use direct `fetch('/api/...')` calls. Keep endpoint paths stable unless you update every caller.
- Use existing CSS files for each page instead of inline style sprawl.
- If text is user-visible, update the relevant `static/js/i18n/*.js` bundle when the surrounding UI is localized.
- Preserve compatibility with local/LAN usage. Avoid assumptions that the app is deployed behind a public origin.

## Runtime Data And Generated Files

Be conservative with these paths:

- `API/.env`
- `data/`
- `assets/`
- `output/`
- `history.json`
- `logs/`
- `_self_restart.log`
- `python/`
- `packages/`

They often contain user data, local secrets, generated media, logs, bundled dependencies, or update backups. Do not delete, reset, or reformat them unless the user specifically asks.

## Workflow Files

Workflow JSON files live in `workflows/`. Some workflows have companion config files named like:

```text
WorkflowName.config.json
```

When adding or changing workflow support:

- Keep workflow names safe for URL/path usage.
- Keep JSON valid and readable.
- Update config files when UI-exposed fields change.
- Remember that built-in workflow deletion is blocked by backend checks.

## Secrets And External Calls

- Do not print, commit, or expose API keys from `API/.env`, provider configs, browser local storage, or runtime JSON.
- Network calls may hit OpenAI-compatible APIs, ModelScope, RunningHub, Volcengine, Jimeng CLI, Gemini CLI, Codex CLI, GitHub, or ModelScope update sources.
- When changing provider code, preserve helpful Chinese error messages; the UI and docs are primarily Chinese-first.

## Git And Working Tree Notes

This repository may have many local runtime/generated files and user edits. Before editing, check:

```powershell
git status --short
```

Only modify files needed for the task. Never revert unrelated user changes. Avoid touching bundled runtime folders, generated assets, and logs unless the task is explicitly about them.

## Style Notes

- Existing Python style is pragmatic, single-file, and helper-function oriented.
- Existing frontend style is plain JS with global state and page-local functions.
- Keep comments concise and useful.
- Prefer UTF-8 for existing Chinese docs and UI files.
- Keep app behavior friendly for non-developer users who launch by double-clicking `.bat` or `.command` files.
