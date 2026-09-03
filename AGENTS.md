# Infinite Canvas 协作指南

本文件定义本仓库的开发约束。修改前必须先阅读它，并以当前 `main.py`、页面和测试为最终事实来源；本项目不是通用的单用户 FastAPI 或前端项目。

## 项目定位与首要边界

Infinite Canvas 是一个局域网、多用户、本地优先的 AI 创作工作台。单个 FastAPI 服务同时提供认证、权限、智能画布、素材与项目持久化、媒体代理、生成队列、WebSocket、用量治理，以及多种本地或云端 AI 后端适配。

当前产品只保留**智能画布**。普通画布已移除，不得恢复 `canvas.html`、`canvas.js`、`canvas.css`，也不得为新能力维护普通画布兼容分支。

最重要的服务端隔离链路是：

```text
已认证用户 → 部门 → API 配置组 → 平台 / 模型 / 凭据 → 用量事件 / 配额 / 审计
```

- 用户的 `api_profile_id` 是其可用付费平台、模型与服务端凭据的权限边界，不是前端展示状态。
- 部门是人员归属和用量分析维度；配置组是上游配置、凭据、模型清单、并发和月度预算的边界。两者不能互相替代或由显示名称推断。
- 平台、模型、上游密钥、RunningHub 钱包凭据和火山引擎凭据必须按配置组解析；同一个 provider ID 在不同配置组中可以对应不同密钥和可用模型。
- 新注册的普通用户默认没有 API 配置组；无组、组不存在或组已停用的用户不能进行需要上游配置或可能产生费用的调用。
- 管理员可以管理所有配置组，并仅可在明确的管理接口上通过 `api_profile_id` 查询参数选择目标组；普通用户绝不能通过请求参数越权选择其他组。

## 项目结构

| 路径 | 职责 |
| --- | --- |
| `main.py` | 大型 FastAPI 单文件：认证、权限、配置组、数据读写、队列、生成、媒体、HTTP/WS 路由和第三方适配。修改时优先复用相邻 helper 与既有返回结构。 |
| `static/index.html` + `static/js/studio-shell.js` | Studio 外壳、工作台 iframe、账户信息及设置面板导航。账户信息必须显示当前用户和配置组，而不是缓存的全局配置。 |
| `static/login.html` | 登录、注册与找回密码入口。未登录时唯一允许进入的页面入口之一。 |
| `static/admin.html` + `static/js/admin-dashboard-v2.js` | 管理员后台：部门、用户、配置组、平台余额、用量、告警和策略。所有数据与写操作必须保留管理员鉴权。 |
| `static/api-settings.html` + `static/js/api-settings.js` | 管理员管理 API 配置组内的平台、模型、工作流与凭据状态的界面。页面选择的 `api_profile_id` 只能用于管理员管理目标组。 |
| `static/canvas-list.html` + `static/js/canvas-list.js` | 智能画布项目工作台、画布卡片、回收站与导入导出。 |
| `static/smart-canvas.html` | 智能画布骨架、节点菜单、Composer、素材/日志/大纲面板、工作流导入导出与图片编辑弹窗。 |
| `static/js/smart-canvas.js` | 智能画布状态归一化、节点/连线、生成、级联、循环、保存、导入导出和配置组感知的模型选择。 |
| `static/css/smart-canvas.css`、`static/css/smart-canvas-shell.css` | 智能画布节点、连接、Composer、组织框、面板及响应式样式。 |
| `static/js/i18n/` | 分区中英文词条；`i18n.js` / `i18n-core.js` 负责装载。智能画布仍使用的通用 `canvas.*` 词条在 `i18n/smart-canvas.js` 中维护。 |
| `static/js/asset-library.js`、`static/js/prompt-library.js`、`static/js/asset-manager.js` | 素材、提示词、个人空间、共享资源与所有权 UI。 |
| `tools/chrome-local-asset-importer/` | Chrome Manifest V3 本地素材采集扩展。 |
| `tools/photoshop-asset-connector/` | Photoshop 24+ UXP 素材面板；全局 `DX` 命名空间，脚本加载顺序以 `index.html` 为准。 |
| `tools/photoshop-canvas-bridge/` | Photoshop 与智能画布之间的桥接客户端。 |
| `workflows/` | 内置 ComfyUI API 工作流与相邻 `.config.json` 字段映射；用户工作流放在 `workflows/custom/`。 |
| `data/` | JSON 运行时状态。除任务明确要求外，不得将用户数据、会话、审计、生成任务或本机配置纳入提交。 |
| `assets/`、`output/` | 上传、素材、生成结果和媒体缓存。均为运行时文件。 |
| `API/.env` | 部署级密钥、CLI 与本机服务配置；按敏感文件处理。 |
| `python/` | Windows 内置 CPython 3.10；`packages/` 是离线 wheel 缓存。 |
| `VERSION` | 发布版本与静态缓存戳的基础。 |

项目没有前端打包器、`package.json`、数据库或迁移框架。前端使用经典全局 `<script>`，状态主要持久化为 JSON 与媒体文件。

## 启动与运行时副作用

### Windows

调试使用项目内置解释器：

```powershell
.\python\python.exe main.py
```

普通用户入口为 `run.bat`；它会启动服务、打开浏览器并在退出时 `pause`，自动化调试不要调用它。依赖安装必须对同一个解释器使用 `-m pip`：

```powershell
.\python\python.exe -m pip install -r requirements.txt
.\python\python.exe -m pip install "uvicorn[standard]"
```

macOS 使用 `./mac-安装依赖.sh` 与 `./mac-启动服务.sh`。不要混用系统 Python、项目 Python 与虚拟环境。

### 服务行为

- `main.py` 绑定 `0.0.0.0:3000`，挂载 `/static`、`/assets`、`/output`；根路由返回 Studio 外壳。
- 认证中间件保护 HTML、API、媒体与 WebSocket。未登录 HTML 请求重定向到 `static/login.html`，其他请求返回 401。
- 浏览器使用 HttpOnly 会话 Cookie；扩展使用可撤销的 Bearer token。UXP WebSocket 因环境限制可在握手时使用短期可撤销 `access_token`，不得将上游密钥置入 URL。
- 首个管理员来自 `API/.env` 中的 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，启动后会创建或更新该账户；首登必须改密。
- 协议级 WebSocket ping 关闭，以兼容 Photoshop UXP；保持应用层心跳、断线重连和既有广播模式。
- CORS 当前为 Chrome/Photoshop 与局域网集成而宽松。收紧 CORS、认证或媒体访问前必须完成扩展端到端验证。

### 启动写入

导入或启动不是只读操作：会创建目录、整理素材、修复扩展名/内容不一致，并由 `sync_static_html_versions()` 改写本地静态引用的 `?v=`。只做语法检查时不要导入 `main.py`：

```powershell
.\python\python.exe -c "import ast, pathlib; ast.parse(pathlib.Path('main.py').read_text(encoding='utf-8')); print('main.py syntax OK')"
```

## 身份、部门与 API 配置组

### 持久化与迁移

| 数据 | 文件 | 规则 |
| --- | --- | --- |
| 用户、密码散列、角色、部门、配置组分配与个人配额 | `data/auth_users.json` | 密码使用每用户独立 salt 的 `scrypt`；只通过 `public_user()` 对外返回安全字段。 |
| 会话与扩展令牌散列 | `data/auth_sessions.json` | 修改密码、退出、禁用/删除用户或撤销令牌时必须使旧会话失效。 |
| 部门 | `data/departments.json` | 使用稳定 `department_id`；不能以显示名称、大小写或历史脏值做宽松跨部门匹配。 |
| API 配置组（非敏感） | `data/api_profiles.json` | schema version 为 1；包含 `id`、`name`、启用状态、平台、模型、计费范围和组级用量策略。 |
| 旧全局平台兼容源 | `data/api_providers.json` | 只服务 `legacy-shared` 兼容组同步，不应再作为新逻辑唯一配置来源。 |
| 部署级凭据 | `API/.env` | 按配置组命名并仅在服务端读取，绝不写回上述 JSON。 |
| 用量审计与告警 | `data/usage_audit/*.jsonl`、`data/usage_policy.json`、`data/usage_alerts.json` | 运行时数据，默认不提交。 |

启动时 `ensure_legacy_api_profile_migration()` 会把既有全局平台迁入 `legacy-shared`，并一次性为已有未分配账户绑定该兼容组。此迁移必须保持幂等；**之后新注册账户仍保持未分配**，等待管理员显式分组。

`api_profiles.json` 缺失、损坏或版本不兼容时，读取路径只返回只读的兼容视图；管理写入必须拒绝并提示恢复文件，不能悄悄覆盖损坏配置。

### 必须遵守的调用方式

1. 所有受保护路由先使用 `require_authenticated(request)`；管理员操作使用 `require_admin(request)`。
2. 所有需要平台、模型、密钥、余额或付费能力的请求，使用 `request_api_profile(request)` 解析用户所属组。
3. 发起上游调用时使用 `resolve_upstream_context()` 或 `upstream_context_for_profile()`，将 `UpstreamContext` 沿调用链传递。
4. `get_api_provider()` / `get_api_provider_exact()` 必须带明确 `api_profile_id`；缺失 profile 是服务端错误，不得回退到全局默认平台。
5. 普通用户不可从 body、query、localStorage 或节点数据指定其他 `api_profile_id`。只有管理员管理接口可使用 `allow_admin_selection=True`，并必须由 `request_api_profile()` 验证目标组。
6. 所有模型列表、能力查询和生成前校验均以当前组的启用平台和配置模型为准；不能只靠前端过滤。
7. 配置组停用时，普通用户的使用路径必须返回 403；管理员可读取并编辑停用组以完成治理。

### 凭据与平台管理

- 配置组 ID 必须匹配 `^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$`，它是持久化、环境变量和审计关联的稳定 ID，重命名只能改显示名称。
- 常规密钥使用 `API_PROFILE_<PROFILE>_PROVIDER_<PROVIDER>_KEY`。RunningHub 钱包和火山引擎 access/secret key 也使用配置组专属环境变量 helper。
- `legacy-shared` 保留旧环境变量名称以兼容现有部署；不要破坏此回退或将新组写入旧全局变量。
- `data/api_profiles.json` 只能存平台结构与非敏感设置，绝不能出现 `api_key`、token、cookie、authorization 或密钥预览值。
- `public_provider()` 的掩码/是否已配置字段仅供管理员管理页面使用；普通用户的 `/api/config` 和 `/api/providers` 响应必须移除密钥相关字段。
- API 设置页面的 `studio_admin_api_profile_id` 只是管理员编辑目标的 UI 状态。后端仍是唯一授权者。
- 复制配置组只能复制平台与策略，不复制凭据；删除配置组前必须确认无用户绑定，`legacy-shared` 不可删除、只能停用。

## 账号、资源与权限

- 普通用户可使用已授权的创作、画布、个人素材和允许的读取接口；平台写入、ComfyUI/工作流管理、更新回滚、全局数据、用户、部门、配置组、告警和策略为管理员专用。
- 注册必须校验用户名、密码和已启用部门；用户名只接受 3–40 位字母、数字、点、下划线或连字符，不得静默将邮箱或非法名称改写为另一个账号。
- 管理员对用户的部门、配置组、角色、启用状态和配额变更必须校验目标部门/组存在。无配置组是允许的治理状态，但不能使用上游能力。
- 删除用户前，按现有治理逻辑将其个人素材和提示词转为系统所有，保留资源 URL 与画布引用；同时撤销会话并取消/完成相关任务的用量状态。不要直接删除其引用资源。
- 画布、素材、提示词、文件夹和对话都可能含所有权字段。新增读写接口必须复用现有 `require_asset_manage`、所有者解析和路径清洗 helper，避免跨用户访问或目录穿越。
- 不得新增任何返回明文 API Key、会话 cookie、扩展 token 或上游凭据的接口。`/api/config/token` 只能报告是否配置。

## 用量、限额与异步任务

可能产生费用或显著本机负载的入口必须在真正提交前调用 `begin_usage_event()`，它会：解析用户和配置组、检查用户日配额/并发与组级并发/硬预算、记录平台快照和计费范围。

- 分类固定为 `image`、`video`、`llm`；用量事件关联用户、部门、配置组、平台、模型、`billing_scope`、客户端来源和任务 ID。
- `billing_scope` 仅为 `department`、`shared`、`local`、`disabled`；共享成本标识来自配置组内 provider 快照，不从客户端相信。
- 组策略包括各类别并发、月度预算、告警百分比与硬限额。未启用硬限额时预算是告警阈值，不自动拦截。
- 审计默认不保存提示词、图片/视频内容、URL、密钥、Cookie、Authorization 或其他敏感请求字段。使用 `sanitize_usage_value()` / `sanitize_raw_usage()`，不要自行把原始上游响应写入 JSONL。
- 异步提交、轮询、恢复、取消、完成和失败必须使用同一个 `usage_event_id`，通过 `finish_usage_event_by_id()` / `finish_scoped_usage_event()` 收口；不能因轮询或重试重复计费。
- 中间件为少量旧直连生成路由兜底审计。新增入口不能只依赖这份白名单，必须在自己的任务生命周期中显式建账并正确结束。
- 管理员报表可按用户、部门、配置组、计费范围、平台、模型、状态和时间筛选；导出必须沿用 `public_usage_export_event()` 的脱敏字段集。

## 后端实现约定

### 接口与数据契约

- 修改路由前，搜索所有 `fetch()`、WebSocket、Chrome 扩展和 Photoshop 调用方。保持 JSON 字段、HTTP 方法和可读 `HTTPException.detail`，除非同步完成所有消费者迁移。
- 主要接口族：认证 `/api/auth/*`；管理 `/api/admin/*`；配置与模型 `/api/config`、`/api/providers`、`/api/models`；画布 `/api/canvases`、`/api/projects`；生成 `/api/online-image`、`/api/canvas-*`；素材 `/api/asset-library/*`、`/api/local-assets/*`、`/api/prompt-libraries/*`；实时 `/ws/stats`。
- 媒体响应使用站内 `/assets/...` 或 `/output/...` URL，不向浏览器暴露本机绝对路径。
- 文件名、媒体 URL、共享目录、工作流路径和 ZIP 成员必须复用已有白名单、`commonpath` 和安全拼接 helper。

### 并发与外部服务

- `async def` 中不直接做长时间同步网络、图像处理、文件 I/O 或子进程；使用 `asyncio.to_thread()` 或现有后台 helper。
- 继续使用现有锁：`AUTH_LOCK`、`DEPARTMENT_LOCK`、`GLOBAL_CONFIG_LOCK`、`CANVAS_LOCK`、`CONVERSATION_LOCK`、`QUEUE_LOCK`、`HISTORY_LOCK`、`RUNNINGHUB_WORKFLOW_LOCK`、用量和素材相关锁。不要新建未协调的平行缓存。
- 任务状态、上游任务 scope 与配置组必须一并持久化，恢复轮询时按原配置组解析凭据，绝不能切到当前登录用户或默认组。
- 大视频和远程媒体应流式代理；长任务保留超时、轮询间隔、状态和错误尾部，避免无限等待。
- 后台线程广播使用 `GLOBAL_LOOP` 与 `asyncio.run_coroutine_threadsafe()` 的现有模式。

### 第三方特殊约定

- RunningHub 企业共享钱包调用旧 `/task/openapi/*` 时，若携带 `apiKey` 返回 `ApiKey verification failed`，必须移除 body/form 的 `apiKey` 并使用 Bearer-only 回退。上传、提交和轮询始终使用同一 `useWallet` 与同一配置组；修改后运行 `tests/test_runninghub_wallet_auth.py`。
- Codex 图片优先解析 Windows 原生 `gpt-image-2-skill.exe`，避免 npm `.CMD` 参数解析问题；尺寸只能用 `auto`、`2K`、`4K` 或显式 `WIDTHxHEIGHT`，不要传 `1K`。低成本冒烟使用 `size=auto`、`quality=low`。
- 即梦 CLI、Gemini CLI 与 Codex CLI 受安装状态、登录会话和本地子进程共同影响；先查状态接口，再改生成流程。
- ComfyUI 工作流必须为 API 格式（顶层节点 ID 映射，节点含 `class_type`）。浏览器 UI 导出不能直接当 API workflow 使用。

## 智能画布与前端

### 通用前端约束

- 不引入需要构建步骤的新框架；保持本地 Tailwind、Lucide、Three.js 和字体分发，支持离线与局域网。
- 新增用户文本必须补齐中英文词条；动态插入图标后使用既有 `lucide.createIcons()` / `refreshIcons()` 模式。
- 静态资源使用 `/static/...` 绝对路径。缓存戳由服务端维护，不要手工批量改写所有 `?v=`。
- 登录用户、配置组和模型可见性必须每次从受保护接口获得；前端 localStorage 只能缓存展示偏好，不能作为权限、配置组或密钥来源。
- 智能画布会以 `user_id + api_profile_id` 隔离最近运行设置/模型选择缓存。修改此逻辑不得让不同用户或组串用模型、工作流或输入参数。

### 画布持久化与兼容

- 画布保存在 `data/canvases/*.json`，当前只接受 `kind: "smart"`；不再读取、列出或恢复普通画布。
- 新节点字段必须提供默认值，确保旧智能画布可加载、保存、刷新和再次加载。结构更新同时搜索归一化、撤销、复制粘贴、删除、导入导出与保存代码。
- 普通内容改动必须通过画布操作流持久化并广播；`canvas_updated` 仅限兼容、元数据或明确的受控恢复信号，不能作为节点、连线、设置、日志、媒体或任务状态的日常整图同步手段。素材变化继续广播 `asset_library_updated`。不要仅修改内存状态。
- 用户可撤销的结构/内容变化先 `pushUndo()`；批量内部操作遵循 `undoSuppressed`，完成后 `render()` 与 `scheduleSave()`。

### 协作同步与授权不变量

智能画布已彻底采用“节点操作同步 + 服务端授权”模型。修改画布、任务、实时频道或其调用方前，先阅读 [节点操作同步设计](docs/superpowers/specs/2026-09-02-smart-canvas-node-operation-sync-design.md) 与 [公司内分享与协作授权设计](docs/superpowers/specs/2026-09-03-smart-canvas-sharing-acl-design.md)。以下规则是强制边界：

- `canvas_id` 是内容、操作日志、WebSocket 订阅、presence、生成任务、任务恢复与副本的唯一隔离范围；一个画布的事件不得触发其他画布的读取、保存、渲染、任务或状态写入。
- 日常实时同步只提交和应用细粒度、可校验、幂等的操作（节点字段、连线、设置、日志、媒体和任务绑定）。初次加载、导入导出与明确的灾难恢复可读取完整快照；实时消息、定时轮询、冲突回程和任务完成回调不得拉取或回写整张画布，更不得以旧快照覆盖当前状态。
- 同一节点不同字段可并行保存；同一字段以服务端收到的最后一个有效操作为准并广播确认值。删除墓碑优先，迟到的更新、生成回调或旧请求都不能复活节点；撤销必须生成明确的新恢复操作，而非清除墓碑后依赖旧请求回放。
- 生成的提交、进度、完成、失败、取消和计时必须用 `canvas_id + node_id + task_id` 作用于绑定节点；一个节点可有多个并行任务。不得用前端冷却、旧预览或整图状态推断任务完成。
- 公司内登录用户默认可查看和复制；只有编辑者可写入、生成或发送编辑 presence；只有所有者可管理协作者和所有权。管理员只在显式治理模式使用替代权限，且不会因此成为画布所有者或编辑者。所有 HTTP、WebSocket、Photoshop 与任务恢复写路径均须复用统一画布访问校验。
- Presence 是非持久化提示：只广播同一画布内、具有编辑权的会话；界面只展示其他用户，不能把当前用户或其其他浏览器标签标为“正在编辑”。
- 复制必须生成独立的画布、节点/连线 ID、操作日志、墓碑、任务与协作名单；可复制素材引用，但源和副本绝不共享写入、实时频道或任务状态。

涉及上述边界时，至少验证不同画布隔离、双标签页不同节点并行、同字段冲突、删除后迟到请求、并行生成、权限变化与 presence；不得以旧“整图快照保存 + 冲突后整图合并”模型衡量或实现新改动。

### 节点与生成架构

- `smart-image-upload` 是上传入口；`smart-image-generation` 与 `smart-video-generation` 是可运行节点；`smart-prompt`、`smart-loop`、`smart-workflow-group`、`smart-note` 有各自持久化职责。
- 旧 `smart-image`、`smart-container`、无 type 节点只在载入时通过 `normalizeLegacySmartNode()` / `migrateLegacySmartCanvasNodes()` 迁移；不要再创建它们。
- `smart-group` 已退出产品能力；`smart-workflow-group` 仅为组织框，成员关系写在成员节点 `workflowGroupId`，不参与生成拓扑。
- 多结果生成节点使用 `images[]` 和 `activeImageIndex`，呈现主媒体、翻页与缩略图，不重新生成普通网格。删除当前结果走 `deleteNode()` 的统一清理逻辑；删到最后一项才删除节点、连线和历史关联。
- 生成输入可来自连线、手动引用、prompt mention、循环上下文和运行快照。修改规则时同步检查 `runInputRefs`、`runPromptRefs`、`manualInputRefs`、`blockedInputRefs`、`workflowInputImagesFor()` 与 `buildPromptRequest()`。
- 每次生成必须仅使用当前认证用户所属配置组的 provider/model/capability。前端过滤只是体验层，服务端仍要验证 provider 和 model 属于该配置组。
- 图片、视频、ComfyUI 和工作流异步任务必须持久化任务 ID 与配置组 scope；刷新后可恢复查询，不能依赖内存 Promise。
- 级联、分支和并行循环保持同轮 `smartLoopContext` / `roundOutputs` 隔离，停止、重试和失败不得重复提交或重复建账。

### 导入、导出与组织

- 工作流导出格式为 `infinite-smart-canvas-workflow` version 1，只导出选中的非 organizer 节点和内部连线；ZIP 资源由 `/api/canvas-workflows/export` 安全收集。
- 导入追加到当前画布，必须重建 ID、映射连线/组织关系/历史来源、清除瞬时运行状态并定位当前视口；绝不能覆盖画布。
- ZIP 解包继续使用服务端清洗，防止 Zip Slip 和任意文件读取。
- 自动整理把组织框和成员当作原子集合；大纲只包含工作流框和便签，修改相关行为时同步验证。

## 素材、扩展与 Photoshop

- 素材与提示词有系统、个人和共享所有权。个人库须以当前服务端用户 ID 识别，不能信任前端 owner 字段。
- Chrome 扩展使用 `/api/local-assets/*` 导入普通 URL、data/blob、iframe、canvas 和媒体。调整本地素材接口时验证认证、跨域失败提示、智能分类和局域网地址。
- Photoshop UXP 依赖 `/api/asset-library`、`/api/ai/upload`、`/api/asset-library/items`、`/ws/stats` 和认证后的媒体访问。全局 `DX` 脚本顺序必须保持 `state → net → sources → ps → socket → app`，再加载其依赖模块。
- Photoshop 发送到画布仅允许智能画布；选择配置组相关平台或素材时，应由当前会话身份和后端数据过滤。
- Photoshop 导出使用合并副本，不能修改用户原 PSD 文档。

## Git、运行时数据与验证

开始工作时执行：

```powershell
git status --short --branch
git diff --stat
```

- 工作区可能已有用户改动。保持其原样，不执行 `git reset --hard`、`git checkout --`、无差别格式化或 `git add .`。
- 默认不提交 `API/.env`、`data/*.json`、`data/canvases/`、`data/conversations/`、`data/usage_audit/`、`history.json`、`global_config.json`、`assets/`、`output/`、`python/Lib/`、`python/Scripts/`、生成媒体和 HTML 缓存戳噪声。
- 除非任务明确要求更新缓存戳，HTML 中仅 `?v=` 静态资源参数变化不得暂存或提交。若同一 HTML 文件包含功能改动，必须按 hunk 精确暂存，仅保留功能部分。
- `data/asset_library.json` 可同时包含初始数据和用户状态，修改前判断其性质。不要为源码任务混入运行时内容。
- 精确暂存目标文件/补丁，检查 `git diff --cached --check` 后再提交。功能提交不得混入密钥、审计、pip 产物或静态缓存戳。

按风险选择验证：

```powershell
# Python 语法，不导入应用
.\python\python.exe -c "import ast, pathlib; ast.parse(pathlib.Path('main.py').read_text(encoding='utf-8')); print('main.py syntax OK')"

# JavaScript 语法
Get-ChildItem static\js -Recurse -Filter *.js | ForEach-Object {
    node --check $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "JS syntax failed: $($_.FullName)" }
}

# 国际化完整性
node static/js/i18n/validate-i18n.js

# 差异卫生
git diff --check
```

涉及身份、部门、配置组、上游解析、用量、限额或管理员界面时，至少运行相关测试：

```powershell
.\python\python.exe -m unittest discover -s tests -p 'test_api_profiles.py'
.\python\python.exe -m unittest discover -s tests -p 'test_departments.py'
.\python\python.exe -m unittest discover -s tests -p 'test_m6_usage_accounting.py'
.\python\python.exe -m unittest discover -s tests -p 'test_auth_username_validation.py'
```

涉及画布运行与 Photoshop 时补充 `test_smart_canvas_only.py`、`test_photoshop_bridge.py`；涉及 RunningHub 钱包时补充 `test_runninghub_wallet_auth.py`。需要 UI 变更验收时启动本地服务，在已登录会话中实测：用户配置组可见模型、工作台→智能画布→保存/重载、素材所有权、管理员跨组编辑与目标扩展路径。

## 交付标准

交付前确认：

1. 所有上游调用都由认证身份和服务器解析的配置组驱动；没有跨组越权、全局默认回退或明文凭据泄漏。
2. 普通用户、管理员、未分组用户、停用用户/配置组的行为与 HTTP 错误符合上述契约。
3. 异步任务、轮询、恢复、取消和完成保持同一用户、配置组与 `usage_event_id`。
4. 画布、素材、工作流和扩展接口仍保持所需兼容与所有权校验。
5. 改动集中在任务范围，未带入运行时数据、密钥、生成文件或缓存戳噪声。
6. 已完成相称的语法、定向测试与必要浏览器/扩展验收；受登录、付费 Key、网络或外部服务限制的项目须明确报告。
