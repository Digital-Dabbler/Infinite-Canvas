# Infinite Canvas 协作指南

本文档供在本仓库中工作的开发者和自动化代理使用。修改代码前先阅读本文件，并以仓库当前实现为准；不要把它当成通用 Python/Web 项目处理。

## 项目定位

Infinite Canvas 是一个本地优先的 AI 创作工作台：单个 FastAPI 服务同时提供静态页面、画布与素材数据持久化、媒体代理、生成队列、WebSocket 通知，以及多种本地或云端 AI 后端的适配。

主要能力包括：

- 普通无限画布与智能画布，支持图片、视频、音频、文本、LLM、循环和工作流节点。
- 项目/画布管理、回收站、对话记录、生成历史、素材库、提示词库和共享文件夹。
- OpenAI 兼容同步/异步接口、ModelScope、RunningHub、火山引擎、即梦 CLI、本地 ComfyUI、Codex/GPT Image 辅助程序和 Gemini CLI。
- Z-Image、Flux Klein、视角控制、在线生成、GPT 对话等独立工具页面。
- Chrome 本地素材采集扩展和 Photoshop UXP 资产库面板。
- 局域网账号登录、管理员后台、用量审计、告警和用户配额。
- Windows 自带 Python 运行时，同时提供 macOS 启动脚本和多平台第三方 CLI 安装脚本。

项目没有前端打包器、`package.json`、数据库或迁移框架；主要状态以 JSON 文件和媒体文件保存在项目目录中。

## 目录与职责

| 路径 | 职责 |
| --- | --- |
| `main.py` | FastAPI 应用、Pydantic 请求模型、全部 HTTP/WS 路由、文件持久化、生成队列和第三方后端适配。当前是大型单文件，修改时优先沿用相邻 helper 和现有返回结构。 |
| `static/` | 无构建步骤的页面和静态资源；根页面为 `index.html`。 |
| `static/login.html` | 登录与注册入口；允许未登录访问，注册后默认普通用户。 |
| `static/admin.html` | 管理员后台，展示用户、用量、告警和策略。所有接口必须保持管理员鉴权。 |
| `static/js/canvas.js` | 普通画布的状态、节点、连线、生成、保存、项目入口等核心逻辑。 |
| `static/smart-canvas.html` | 智能画布页面骨架，包含画布、节点创建菜单、图片工具栏、生成参数 composer、素材/日志/大纲面板、工作流导入导出和图片编辑弹窗。 |
| `static/css/smart-canvas.css` | 智能画布全部节点、连线、浮动工具栏、生成结果、组织框、便签、面板和图片编辑器样式。 |
| `static/js/smart-canvas.js` | 智能画布的状态归一化、节点与连线渲染、选择/拖拽/缩放、素材输入、图片编辑、提示词、生成任务、级联/循环、组织框、大纲、日志、保存和工作流导入导出。当前是大型单文件，修改时先定位相邻 helper，不要另起一套平行状态。 |
| `static/js/i18n/smart-canvas.js` | 智能画布中英文词条。页面仍有少量历史硬编码中文；新增或调整文本优先补齐这里的双语词条。 |
| `static/js/api-settings.js` | API 平台、模型、RunningHub 工作流和 CLI 状态设置。 |
| `static/js/asset-manager.js` | 素材库、提示词库、本地素材、画布资产及共享文件夹。 |
| `static/js/canvas-list.js` | 项目工作台、画布卡片、回收站与导入导出。 |
| `static/js/comfyui-settings.js` | ComfyUI 实例、自定义 API 工作流和字段映射编辑器。 |
| `static/js/i18n/` | 页面分区的中英文词条；`i18n.js`/`i18n-core.js` 负责装载和语言切换。 |
| `static/vendor/` | 随项目分发的 Tailwind、Lucide、Three.js 和字体；不要随意改为在线 CDN。 |
| `workflows/` | 内置 ComfyUI API 工作流及相邻的 `.config.json` 字段配置；用户导入写入 `workflows/custom/`。 |
| `data/` | 运行时 JSON 状态，如画布、对话、素材库、提示词库、项目、平台、RunningHub 工作流配置、账号、会话、审计和告警。 |
| `assets/` | 输入、输出、素材库和本地上传文件；由服务自动创建子目录。 |
| `output/` | 兼容旧生成页面的输出目录和静态挂载点。 |
| `API/.env` | API Key、平台 URL、ComfyUI 地址、超时等本机配置。它可能包含真实密钥，始终按敏感文件处理。 |
| `python/` | Windows 内置 CPython 3.10 运行时。根目录 `packages/` 是离线 wheel 缓存。 |
| `CLI/` | Codex、Gemini、即梦 CLI 的 Windows/macOS/Linux 安装或启动脚本。 |
| `tools/chrome-local-asset-importer/` | Chrome Manifest V3 素材采集扩展。 |
| `tools/photoshop-asset-connector/` | Photoshop 24+ UXP 面板，使用全局 `DX` 命名空间和按顺序加载的脚本。 |
| `VERSION` | 当前发布版本，也是静态资源缓存戳和更新检查的主要版本来源。 |

## 启动与依赖

### Windows

首选使用项目内置解释器：

```powershell
.\python\python.exe main.py
```

面向普通用户的一键入口是 `run.bat`。它优先执行 `python\python.exe main.py`，缺失时才回退系统 `python`，并在约 3 秒后打开 `http://127.0.0.1:3000/`。自动化调试时不要调用 `run.bat`，因为它会打开浏览器并在退出后 `pause`。

依赖安装入口是 `安装依赖.bat`：先尝试 `packages/` 离线安装，失败后联网执行 `pip install -r requirements.txt`，最后补装 `uvicorn[standard]`。当前内置解释器是 Python 3.10；`packages/` 中部分 `cp314` wheel 与它不兼容，因此离线失败不一定代表代码或网络有问题。

```powershell
.\python\python.exe -m pip install -r requirements.txt
.\python\python.exe -m pip install "uvicorn[standard]"
```

不要把系统 Python、项目内置 Python和任意虚拟环境混用。排查 `ModuleNotFoundError` 时先打印实际解释器路径和版本，再对同一个解释器执行 `-m pip`。

### macOS

使用系统 `python3` 3.10+：

```bash
./mac-安装依赖.sh
./mac-启动服务.sh
```

也可按 `MAC-使用说明.md` 使用 `.command` 启动器。macOS 脚本会选择局域网 IP 并打开 3000 端口。

### 服务行为

- `main.py` 直接运行时绑定 `0.0.0.0:3000`，局域网客户端可访问。
- `/static`、`/assets`、`/output` 分别挂载对应目录，根路由 `/` 返回 `static/index.html`。
- 除登录、注册和必要的登录静态资源外，网页、API、媒体目录和 WebSocket 都要求认证。未登录访问 HTML 页面应跳转到 `static/login.html`，API 和媒体访问应返回认证错误。
- 首个管理员不由注册产生；必须在 `API/.env` 设置 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 后重启服务，由启动逻辑创建或更新管理员初始账号。首次登录管理员需要改密。
- Uvicorn 协议级 WebSocket ping 被禁用，因为 Photoshop UXP 客户端不会可靠响应；客户端使用自己的心跳和重连。不要在未验证 UXP 面板前恢复默认 ping。
- CORS 当前允许所有来源，这是浏览器扩展和局域网插件的既有契约。收紧它会影响 Chrome/Photoshop 集成，必须做端到端验证。

## 启动时会写入文件

导入或启动应用不是纯只读操作：

1. 模块导入会创建 `API/`、`data/`、`assets/`、`output/`、画布和对话等目录。
2. startup 事件会整理素材库目录、修复双扩展名素材，并纠正图片内容与扩展名不一致的问题。
3. `sync_static_html_versions()` 会遍历 `static/*.html`，把本地 `/static/...` 引用的 `?v=` 改成 `VERSION.文件修改时间`。

因此仅启动一次服务就可能让多份 HTML 和素材文件出现变更。分析 Git diff 时先区分业务改动与缓存戳改写；不要机械提交或回滚用户已有变更。若只需检查 Python 语法，使用 AST 解析，不要导入 `main.py`。

## 后端修改约定

### 账号、鉴权与用量审计

- 本地账号、会话、审计、告警和策略保存在 `data/auth_users.json`、`data/auth_sessions.json`、`data/usage_audit/*.jsonl`、`data/usage_alerts.json` 和 `data/usage_policy.json`。这些都是运行时数据，默认不要提交。
- 密码使用独立 salt 的 `scrypt` 哈希；浏览器使用 HttpOnly 会话 Cookie，扩展使用可撤销 Bearer 令牌。修改密码、退出、停用用户或撤销令牌时，必须保证旧会话失效。
- 普通用户只能使用创作、素材、画布和允许的读取接口；平台密钥、API 配置写入、工作流/ComfyUI 管理、更新/回滚、用户管理、全局用量和告警后台必须保持管理员专用。
- 不要新增向客户端返回真实 API Key、Cookie、Bearer token 或上游密钥的接口。`/api/config/token` 只能返回是否已配置之类的状态信息，不能恢复明文 token。
- 所有可能产生费用或本地计算负载的入口都要绑定服务端认证身份并写入一条统一用量事件；异步提交、轮询和完成必须复用同一 `usage_event_id`，不能因轮询重复计数。
- 审计事件默认不保存提示词、参考图内容或密钥。可以保存用户 ID/姓名/部门、来源、功能、供应商、模型、非敏感参数摘要、状态、耗时、上游 usage/token、任务 ID、IP 和 User-Agent 摘要。
- 配额与并发限制应在真正向上游 API、CLI 或本地重任务提交前检查。默认红线只是生成管理员站内告警，不自动封禁用户，除非后台明确设置用户配额。
- WebSocket 和媒体预览同样需要认证。浏览器优先使用 Cookie 或 Authorization 头；Chrome/Photoshop 扩展如因运行环境限制不能设置媒体或 WS 头，只能使用服务端可撤销的访问机制，并避免把真实上游密钥放进 URL。

### 保持现有接口契约

前端、Chrome 扩展和 Photoshop 面板都直接依赖 `main.py` 的 JSON 形状和路径。修改路由时：

- 先搜索所有 `fetch()`、WebSocket 和扩展调用点，再调整路径、方法、字段或错误格式。
- 保留用户可读的 `HTTPException.detail`；前端通常会直接显示它。
- 请求体继续使用相邻的 Pydantic `BaseModel`，并沿用当前 `.dict()` 风格，除非一次性完成兼容迁移。
- 媒体返回值通常使用站内 URL（`/assets/...` 或 `/output/...`）；不要把本机绝对路径暴露给浏览器。
- 涉及文件名、用户 URL、共享目录或工作流路径时，复用现有清洗、白名单、`commonpath` 和安全拼接 helper，防止目录穿越或任意文件读取。

主要接口族包括：

- 应用更新与回滚：`/api/app-info`、`/api/check-update`、`/api/update-*`。
- 媒体上传/预览/下载：`/api/ai/upload`、`/api/media-preview`、`/api/download-output`。
- 平台和模型：`/api/providers`、`/api/config`、`/api/models`。
- 生成：`/api/online-image`、`/api/canvas-image-tasks`、`/api/canvas-video`、`/api/canvas-llm`、`/api/generate`、`/generate`。
- ComfyUI 与工作流：`/api/comfyui/instances`、`/api/workflows`、`/api/canvas-comfy-tasks`、`/api/canvas-workflows/*`。
- RunningHub、即梦、Codex、Gemini：各自的 `/api/runninghub/*`、`/api/jimeng/*`、`/api/codex/*`、`/api/gemini-cli/*`。
- 画布、项目和对话：`/api/canvases`、`/api/projects`、`/api/conversations`。
- 素材与提示词：`/api/asset-library/*`、`/api/local-assets/*`、`/api/prompt-libraries/*`、`/api/shared-folders/*`。
- 实时通知：`/ws/stats`，消息类型包括在线数、新生成结果、画布更新和素材库更新。

### 并发与阻塞 I/O

服务混合使用 `requests`、`urllib`、`httpx`、子进程和本地文件 I/O。新增逻辑时遵守以下原则：

- `async def` 路由中不要直接执行长时间同步网络、图像处理或子进程；使用 `asyncio.to_thread()`，或保持现有同步 helper 由线程调用。
- 共享 JSON/队列写入继续使用已有的 `QUEUE_LOCK`、`HISTORY_LOCK`、`CANVAS_LOCK`、`CONVERSATION_LOCK`、`RUNNINGHUB_WORKFLOW_LOCK` 等锁。
- 大视频和远程媒体应流式代理，不要整文件读入内存。
- 长任务要保留超时、轮询间隔、任务状态和错误尾部信息；不要用无限等待代替失败处理。
- 后台线程向 WebSocket 广播时使用已有 `GLOBAL_LOOP` 与 `asyncio.run_coroutine_threadsafe()` 模式。

### 平台与密钥

- 平台公开配置写入 `data/api_providers.json`；密钥及隐藏配置由 `update_env_values()` 写入 `API/.env`，保存后通过 `reload_env_globals()` 即时生效。
- 不要读取、打印、复制到日志或响应中，也不要在补丁、示例或测试夹具中写入真实 API Key、访问密钥、Cookie、令牌或 Codex 认证文件。
- 修改平台配置时保留“返回掩码/存在状态而非密钥原文”的设计。
- 外部生成接口可能收费。除非任务需要且用户已授权，不要用真实 Key 运行批量或高质量生成测试。
- Codex 图片路径优先解析 Windows 原生 `gpt-image-2-skill.exe`，避免 npm `.CMD` 包装器误解析复杂参数。Codex helper 的尺寸值使用 `auto`、`2K`、`4K` 或明确的 `WIDTHxHEIGHT`；不要传 `1K`。低成本冒烟测试使用 `size=auto`、`quality=low`。
- 即梦状态同时受 CLI 安装版本、登录会话和本地子进程影响；先调用状态接口再改生成逻辑。

## 前端修改约定

### 无构建步骤

前端是经典 `<script>` 全局作用域代码，不经过 npm、TypeScript 或 bundler。不要擅自引入需要编译的新框架。

- 页面共享主题、触摸鼠标适配、国际化和本地 vendor 文件。
- `canvas.js` 与 `smart-canvas.js` 很大，且大量函数依赖全局状态和 DOM ID。修改前搜索状态的全部读写和 HTML 中的对应元素。
- HTML 中有内联脚本，部分独立生成页面的逻辑不在 `static/js/`。排查功能时同时搜索对应 `.html`。
- 新增静态资源时使用 `/static/...` 绝对站内路径；缓存版本由后端自动补齐，不要手工批量更新所有 `?v=`。
- 保持现有本地 vendor 方案，保证离线和局域网环境可用。

### 画布状态与兼容

- 画布持久化到 `data/canvases/*.json`，前端还用 `localStorage` 保存视口、主题、排序、模型选择和临时收件箱等状态。
- 保存格式是兼容边界。新增节点字段应提供默认值，并确认旧画布能加载、新画布能再次保存。
- 普通画布和智能画布有相似但独立的实现；修复共享行为时检查两边是否都受影响。
- 画布列表、素材管理器和 Photoshop 面板会读取画布或素材状态；更新后按需广播 `canvas_updated` 或 `asset_library_updated`。
- 交互修改至少检查鼠标、触摸、小屏布局、缩放/平移、撤销、保存后重载和错误提示。

### 智能画布当前架构

智能画布在 2026 年 7 月完成了一轮节点和工作流重构。不要再按“所有媒体都是 `smart-image`、多图即普通分组”的旧模型实现新功能。

#### 节点类型与职责

- `smart-image-upload` 是单个上传素材入口。旧的无 `type`、`smart-image` 和 `smart-container` 只作为载入兼容态，由 `normalizeLegacySmartNode()` / `migrateLegacySmartCanvasNodes()` 迁移；不要继续产生这些旧类型。
- `smart-image-generation` 和 `smart-video-generation` 是可运行的生成节点。它们可以保存多次或多张结果，以 `images[]` 存媒体，以 `activeImageIndex` 表示当前主预览；生成结果 UI 是“主图/视频 + 计数与翻页 + 缩略图条”，不是把结果重新渲染成普通网格。
- `smart-prompt` 保存提示词、分段、模板和可选 LLM 设置；`smart-loop` 保存轮数、串行/并行模式、起始序号、批量大小、变量提示词及图片输入设置。
- `smart-group` 是智能内容分组，可引用或收纳成员节点；它不是生成结果多图容器。旧 `smart-group` 画布会在迁移时转换为生成节点。
- `smart-workflow-group` 是纯组织框，成员关系存在成员节点的 `workflowGroupId`；`smart-note` 是画布便签。两者属于 organizer，不参与生成拓扑、工作流运行输入或媒体选择。
- 历史结果节点仍属于媒体节点，通过 `historyFor` / `isHistoryGroup` 关联源生成节点。删除源节点时必须同步清理历史节点及相关连线。

#### 状态、渲染与删除

- `nodes`、`canvas.connections`、`selectedId` / `selectedIds`、`selectedImage`、`settings` 和 composer 状态互相耦合。更改节点结构前先搜索归一化、保存、撤销、导入导出、复制粘贴、删除和渲染的所有使用点。
- `render()` 会重建大部分节点 DOM，同时尽量移植媒体元素以保留视频播放状态。交互中的局部移动/缩放优先沿用 `moveNodeElementsDuringDrag()`、`updateNodeElementDuringResize()` 和连接层刷新机制，避免在每个 pointermove 上全量渲染。
- 媒体自然尺寸与布局尺寸分开处理；复用 `mediaLayoutSize()`、`singleImageLayout()`、`imageLayout()` 和 `imageForDisplay()`。不要把媒体绝对路径或临时代理 URL当成持久化源地址。
- 生成节点的删除按钮删除当前 `activeImageIndex` 对应结果；删到最后一项时删除整个节点。不要同时提供“删除当前结果”和“删除节点”两个重叠垃圾桶。
- 删除节点必须经过 `deleteNode()` 的统一清理：维护 `localDeletedNodeIds`、组织框成员、智能分组成员、选择态、历史节点和所有入/出连线。不要只从 `nodes` 数组 `splice()`。
- 所有用户可撤销的结构或内容修改先 `pushUndo()`，批量内部操作用现有 `undoSuppressed` 模式避免产生多条无意义历史；完成后 `render()` 并 `scheduleSave()`。

#### 生成与级联执行

- 上传节点只负责输入；正常可运行节点是图片/视频生成节点和兼容用智能分组。直接生成入口为 `runGeneration()`，一键级联入口为 `runSmartCascade()`。
- 每个生成节点可保存独立 `runSettings`。当前设置按引擎/媒体模式缓存到 `smart_canvas_recent_run_settings_v1`，切换 API 图片、API 视频、ComfyUI、RunningHub、ModelScope 或火山引擎时不要串用不兼容字段。
- 输入引用可能来自连线、手动引用、提示词 mention、循环上下文和已有运行快照。修改输入规则时同时检查 `runInputRefs`、`runPromptRefs`、`manualInputRefs`、`blockedInputRefs` 以及 `workflowInputImagesFor()` / `buildPromptRequest()`。
- 异步图片任务保存在节点 `pendingTasks`，并通过 `/api/canvas-image-tasks` 轮询或恢复；ComfyUI 使用 `/api/canvas-comfy-tasks`。刷新页面后仍应能恢复查询，不能只依赖内存 Promise。
- 级联支持分支、循环串行/并行、并发上限、预创建输出槽、停止请求和连线运行态。修改时保留同一轮的 `smartLoopContext` / `roundOutputs` 隔离，避免并行轮次互相污染素材或提示词。
- 生成完成后使用现有 finalize/append helper 更新媒体、运行元数据、历史分组和日志。任务提交、轮询、恢复、完成继续复用同一服务端用量事件，前端不得通过重复提交模拟重试。

#### 工作流组织与导入导出

- “智能分组”与“工作流组织框”是两套机制：前者影响内容集合，后者只影响视觉组织。拖动节点进出组织框时使用 `syncDraggedWorkflowMembership()` 一类现有逻辑，不要据几何位置临时推断并覆盖持久关系。
- 自动整理要把组织框和其成员视为原子集合，避免成员脱离外框；相关入口为 `smartArrangeAtomicIds()` 及相邻布局 helper。
- 大纲面板只列组织框和便签，点击后聚焦画布对象。修改组织节点标题、颜色、成员关系或删除行为时同步检查大纲。
- 选中工作流导出格式为 `infinite-smart-canvas-workflow` version 1，仅包含选中非 organizer 节点及它们之间的连线；JSON 在前端直接下载，带本地资源的 ZIP 走 `/api/canvas-workflows/export`。
- 导入支持 JSON/ZIP，经 `/api/canvas-workflows/import` 解包后追加到当前画布。导入时必须重新生成节点 ID、重映射连线、清除瞬时运行态，并把内容定位到当前视口；不可覆盖当前画布。
- 工作流资源包只收集可识别的站内本地资源。文件名、归档成员和解包路径继续使用后端现有清洗与安全拼接逻辑，防止 Zip Slip 和任意文件读取。

### 国际化与视觉一致性

- 新增用户可见文本时优先添加 `static/js/i18n/*.js` 的中英文词条，并通过现有 `tr`/`tf`/`StudioI18n` 调用。
- 保留中文作为默认体验，同时避免只更新一种语言造成按钮显示键名。
- 图标使用本地 Lucide；动态插入 DOM 后按现有模式调用 `lucide.createIcons()` 或 `refreshIcons()`。
- 沿用 `static/css/theme.css` 的主题变量和页面现有 CSS，不要用散落的硬编码颜色破坏明暗主题。

## ComfyUI 与工作流

- `COMFYUI_INSTANCES` 存在 `API/.env`，默认 `127.0.0.1:8188`；多实例负载由后端跟踪。
- 工作流文件必须是 ComfyUI API 格式：顶层节点 ID 映射到对象，节点对象包含 `class_type`。浏览器导出的 UI 工作流不能直接替代 API 工作流。
- 字段映射保存在同名 `.config.json` 中，核心字段是节点 ID、输入名、控件类型和可选项。
- 内置工作流不可通过 API 删除；用户工作流写入 `workflows/custom/`，同时兼容旧的 `workflows/自定义/`。
- 修改 JSON 时保持 UTF-8、合法 JSON 和稳定节点 ID。不要无意义重排大型工作流，避免产生难审查的全文件 diff。
- 工作流测试最终复用 `/api/generate`/`generate()` 路径，必须同时验证字段类型转换、媒体上传和目标 ComfyUI 节点输入。

## 浏览器与 Photoshop 扩展

Chrome 扩展会扫描普通 URL、data/blob、iframe、canvas 和部分媒体请求，再调用 `/api/local-assets/*` 导入到 `assets/uploads/`。修改本地素材接口时要验证：普通文件、base64、跨域下载失败提示、智能分类以及局域网服务地址。

Photoshop UXP 面板依赖：

- `GET /api/asset-library`
- `POST /api/ai/upload`
- `POST /api/asset-library/items`
- `WS /ws/stats`
- `/assets` 静态访问与宽松 CORS

其脚本使用全局 `DX` 命名空间，加载顺序以 `index.html` 为准。README 中的基础顺序为 `state → net → sources → ps → socket → app`，当前还可能包含 `agent`、`generate`、`ui` 等后续模块；添加依赖时必须同步 HTML 顺序。Photoshop 导出使用合并副本，不能改动用户原文档。

## 运行时数据与 Git 边界

开始工作前运行：

```powershell
git status --short --branch
git diff --stat
```

当前仓库没有可靠覆盖运行时文件的根 `.gitignore`，而且 `API/.env` 仍被 Git 跟踪。务必遵守：

- 不要执行无选择的 `git add .`。
- 不要覆盖或提交用户现有的 `API/.env`、`data/*.json`、`data/canvases/`、`data/conversations/`、`history.json`、`global_config.json`、`assets/`、`output/`、`python/Lib/`、`python/Scripts/` 或生成结果，除非任务明确要求其中某项。
- `data/asset_library.json` 是仓库中的初始/兼容数据，但运行后也可能包含用户状态；修改前先确认差异性质。
- `python/` 根部的嵌入式解释器是发布制品；不要把本地 pip 安装产生的数千个文件当作源码变更提交。
- 保留用户工作区已有修改。只编辑任务所需文件，不用 `git reset --hard`、`git checkout --` 或批量格式化清理现场。
- `static/*.html` 只改 `?v=` 时通常是启动噪声；功能提交应排除这类无关 diff。
- 源码和文档使用 UTF-8。避免全文件换行符转换；Windows 上 Git 可能提示 LF/CRLF 转换，提交前检查实际 diff。

## 验证清单

仓库目前没有自动化测试套件。验证应与改动风险匹配，并报告未执行的外部测试。

### 只读/低副作用检查

Python 语法检查（不导入应用，也不触发目录创建和迁移）：

```powershell
.\python\python.exe -c "import ast, pathlib; ast.parse(pathlib.Path('main.py').read_text(encoding='utf-8')); print('main.py syntax OK')"
.\python\python.exe -m pip check
```

JavaScript 语法检查（需要 Node.js）：

```powershell
Get-ChildItem static\js -Recurse -Filter *.js | ForEach-Object {
    node --check $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "JS syntax failed: $($_.FullName)" }
}
```

工作流 JSON 检查可用 PowerShell `Get-Content -Raw <file> | ConvertFrom-Json`，并另外确认节点包含 `class_type`；仅能解析 JSON 不代表它是有效的 ComfyUI API 工作流。

### 服务冒烟

在前台启动：

```powershell
.\python\python.exe main.py
```

另开终端检查：

```powershell
Invoke-WebRequest http://127.0.0.1:3000/ -UseBasicParsing
Invoke-RestMethod http://127.0.0.1:3000/api/app-info
Invoke-RestMethod http://127.0.0.1:3000/api/config
```

启动前后都检查 `git status`，确认 startup 迁移和 HTML 缓存戳没有混入功能改动。

### 按改动范围手工验证

- 首页与导航：`/`。
- 项目/画布：`/static/canvas-list.html`、`/static/canvas.html`、`/static/smart-canvas.html`。
- 智能画布节点兼容：载入旧 `smart-image` / `smart-container` / 旧多图节点，确认迁移后可保存、刷新和再次载入，且 ID 与连线有效。
- 智能画布生成结果：分别检查竖图、横图、多结果图片和视频；切换主结果、缩略图、上一张/下一张、删除当前结果，并确认删除最后一项会连同节点和连线一起删除。
- 智能画布工作流：检查上传→图片生成→视频生成、提示词节点、串行/并行循环、分支级联、停止运行、刷新后恢复异步任务及失败重试提示。
- 智能画布组织：检查创建/解散工作流框、拖入拖出成员、移动/缩放、自动整理、便签、大纲聚焦以及删除组织框后成员仍保留。
- 智能画布导入导出：检查仅选中节点的 JSON、带资源 ZIP、导入后 ID/连线重映射、追加位置、旧格式兼容和恶意归档路径拒绝。
- 平台/工作流：`/static/api-settings.html`、`/static/comfyui-settings.html`。
- 素材：`/static/asset-manager.html`，同时检查 Chrome/Photoshop 消费的接口。
- 生成页面：`zimage.html`、`enhance.html`、`klein.html`、`angle.html`、`online.html`、`gpt-chat.html` 中受影响的页面。
- WebSocket：连接 `/ws/stats`，确认应用层 ping/pong、断线重连和相应广播。
- 保存兼容：创建或载入测试画布，保存、刷新、重新打开，并检查旧数据未丢失。
- 外部平台：先做状态/连接测试，再做单个低成本生成；不要默认测试所有付费平台。

## 完成标准

提交或交付前应满足：

1. 改动集中在任务范围，没有带入密钥、用户数据、pip 产物、生成媒体或缓存戳噪声。
2. 后端请求模型、返回 JSON、静态 URL 和扩展依赖的接口契约保持兼容，或已同步更新所有消费者。
3. 长耗时同步工作没有阻塞 asyncio 事件循环，共享状态仍受正确的锁保护。
4. 新旧画布/素材/工作流数据均能读取，路径与文件名经过安全校验。
5. 用户可见文本、明暗主题和必要的中英文词条已同步。
6. 至少完成相关语法检查和目标页面/接口冒烟；受登录、网络、付费 Key 或外部服务限制的验证要明确说明。
7. 最后再次检查 `git diff --check`、`git diff --stat` 和 `git status --short`，只交付预期文件。
