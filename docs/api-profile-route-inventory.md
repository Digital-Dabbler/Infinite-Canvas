# 部门独立 API 配置组：平台与生成入口清单

> 文档状态：持续盘点与实施跟踪
> 用途：防止平台、密钥、异步任务或扩展入口在改造中遗漏
> 当前状态说明：本表只记录已发现入口；实施阶段仍需通过全仓搜索持续补全
> 最后更新：2026-08-02

## 0. 当前实施摘要

- 兼容数据模型、用户绑定、配置组管理 API和管理员界面：已改造并通过自动测试；
- `GET/PUT /api/providers`、`GET /api/models`、`GET /api/config`：已接入配置组能力，待页面手工验收；
- 普通 API Key、RunningHub钱包 Key和火山 AK/SK的配置组环境变量命名：已改造；
- 在线图片、画布图片任务、画布视频、画布 LLM、普通聊天、流式聊天和 Agent聊天：首批调用链已接入，待假上游完整验证；
- 已建立统一 `UpstreamContext`；主要上游、异步任务持久化、任务所有者、RunningHub组内工作流和扩展身份契约已接入；真实 Chrome/Photoshop与付费上游手工冒烟仍待处理；
- 清单中的“待确认”行不能因为核心 helper 已实现而自动视为关闭，仍需逐入口达到“已验证”。

## 1. 使用方法

每个入口必须经过以下状态：

- `待确认`：已发现，但尚未确认完整调用链；
- `已定位`：已定位前端、路由、helper、凭据和异步行为；
- `已设计`：已确定配置组接入方式；
- `已改造`：代码已接入配置组解析；
- `已验证`：自动测试和必要手工验证通过；
- `不适用`：确认不涉及外部凭据或计费，并写明原因。

任何入口只有达到“已验证”或有充分依据标记“不适用”，才能从迁移清单中关闭。

## 2. 当前全局配置与解析点

| 组件 | 当前职责 | 配置组改造要求 | 状态 |
| --- | --- | --- | --- |
| `data/api_providers.json` | 全局平台非敏感配置 | 迁移为配置组作用域或平台定义加组内连接 | 待确认 |
| `API/.env` | 全局 API Key和隐藏配置 | 建立配置组与平台的复合凭据索引；制定脱离 Git 的安全方案 | 待确认 |
| `load_api_providers()` | 加载全局平台列表 | 接收或解析明确配置组 | 待确认 |
| `save_api_providers()` | 保存全局平台列表 | 只保存管理员选定的配置组 | 待确认 |
| `public_api_providers()` | 返回平台和密钥状态 | 普通用户仅返回本组非敏感能力；管理员按组查看 | 待确认 |
| `get_api_provider()` | 全局平台解析并包含回退 | 替换为用户/任务作用域解析；禁止跨组回退 | 待确认 |
| `get_api_provider_exact()` | 全局精确解析 | 增加配置组作用域 | 待确认 |
| `provider_env_key_value()` | 按平台 ID 读取全局 Key | 改为按配置组和平台读取 | 待确认 |
| `runninghub_wallet_key_value()` | 读取全局钱包 Key | 改为配置组作用域 | 待确认 |
| `volcengine_access_key_value()` | 读取全局火山 AK | 改为配置组作用域 | 待确认 |
| `volcengine_secret_key_value()` | 读取全局火山 SK | 改为配置组作用域 | 待确认 |
| `api_headers()` | 构造上游鉴权头 | 只接受已解析的运行时 provider/credential context | 待确认 |
| `resolve_chat_provider()` | 解析 LLM 平台、地址和请求头 | 增加用户或任务作用域 | 待确认 |
| `api_key_from_payload()` | 测试/拉取模型时从表单或全局取 Key | 管理员必须明确配置组；普通用户不能借此探测其他组 | 待确认 |

## 3. 平台管理与模型发现

| HTTP 入口 | 用途 | 权限/作用域要求 | 特殊注意点 | 状态 |
| --- | --- | --- | --- | --- |
| `GET /api/providers` | 获取平台与模型 | 普通用户仅本组；管理员可明确选组 | 不向普通用户返回密钥预览或变量名 | 待确认 |
| `PUT /api/providers` | 保存平台配置 | 仅管理员；必须明确目标配置组 | 保存不得覆盖其他组 | 待确认 |
| `POST /api/providers/test-connection` | 测试连接 | 仅管理员；使用当前管理组 | 表单临时 Key不能写入日志 | 待确认 |
| `POST /api/providers/probe-async` | 探测异步协议 | 仅管理员；使用当前管理组 | 假任务 ID探测不能产生费用 | 待确认 |
| `POST /api/providers/fetch-models` | 用表单配置拉取模型 | 仅管理员；明确管理组 | 防止普通用户提交任意 Base URL/Key | 待确认 |
| `GET /api/providers/{provider_id}/fetch-models` | 从已保存平台拉取模型 | 管理员按选定组；如对用户开放则仅限本组 | RunningHub钱包 Key等特殊解析 | 待确认 |
| `GET /api/models` | 返回模型清单 | 应确认是否仍依赖全局模型 | 避免向用户展示其他组模型 | 待确认 |
| `GET /api/config` | 公开运行配置 | 仅返回当前用户必要的非敏感状态 | 检查全局模型和平台信息泄漏 | 待确认 |
| `GET /api/config/token` | ModelScope Token存在状态 | 当前用户作用域 | 只返回 configured，不返回真实 Token | 待确认 |

## 4. 图片生成与查询

| HTTP 入口 | 页面/调用方 | 上游类型 | 异步 | 配置组要求 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `POST /api/online-image` | 在线生图、智能画布等 | OpenAI兼容/即梦/其他图片平台 | 视平台而定 | 提交时按登录用户解析 | 待确认 |
| `POST /api/image-task-query` | 异步图片恢复与查询 | OpenAI兼容/RunningHub等 | 是 | 从已保存任务归属解析，不能信任客户端换组 | 已改造 |
| `POST /api/canvas-image-tasks` | 智能画布图片任务 | 多图片平台 | 是 | 任务保存 `user_id`、`api_profile_id`、`provider_id` | 已改造 |
| `GET /api/canvas-image-tasks/{task_id}` | 画布任务轮询 | 多图片平台 | 是 | 持久化；任务所有者或管理员；保持提交组 | 已验证 |
| `POST/GET /api/canvas-video-tasks/*` | 普通/智能画布视频任务 | 多视频平台 | 是 | 持久化任务和上游任务 ID；刷新恢复 | 已验证 |
| `POST /api/angle/generate` | 视角控制页 | ModelScope/相关图片平台 | 是/轮询 | 按用户配置组或明确共享策略 | 已改造 |
| `POST /api/angle/poll_status` | 视角任务轮询 | ModelScope | 是 | 与提交账户一致 | 已改造 |
| `POST /api/ms/generate` | ModelScope 生成 | ModelScope | 是/同步混合 | 明确部门凭据或共享策略 | 已改造 |
| `POST /generate` | 旧兼容生成页 | ComfyUI/旧生成路径 | 视工作流而定 | 确认是否付费、共享或本地 | 待确认 |
| `POST /api/generate` | ComfyUI与工作流生成 | 本地 ComfyUI | 是/阻塞任务 | 通常标记 local；仍需用户审计与配额 | 待确认 |
| `POST /api/canvas-comfy-tasks` | 智能画布 ComfyUI任务 | 本地 ComfyUI | 是 | `local/shared`，任务绑定用户与配置组快照 | 已验证 |
| `GET /api/canvas-comfy-tasks/{task_id}` | ComfyUI任务轮询 | 本地 ComfyUI | 是 | 持久化；任务所有者或管理员 | 已验证 |

需要继续追踪的内部 helper：

- 图片平台解析；
- 图片生成提交；
- 异步任务 URL 构造；
- 图片任务状态查询；
- 图片下载和结果落盘；
- 即梦待处理任务恢复；
- 失败重试和历史结果恢复。

## 5. 视频生成

| HTTP 入口 | 页面/调用方 | 上游类型 | 异步 | 配置组要求 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `POST /api/canvas-video` | 旧同步兼容视频入口 | OpenAI兼容、RunningHub、火山、即梦等 | 多数是 | 保留兼容；画布已迁移到 `/api/canvas-video-tasks` | 已改造 |
| 旧视频生成入口（待全仓确认） | 独立页面和历史代码 | 多平台 | 是 | 不得保留全局 Key路径 | 待确认 |

重点 helper 与特殊平台：

- 通用视频提交和任务 URL 候选；
- 通用视频轮询；
- RunningHub视频任务；
- 火山引擎视频任务；
- 即梦视频；
- Grok、Agnes、灵境、土豆、玉玉等协议适配；
- 视频结果下载和媒体代理；
- 失败重试、超时与恢复。

所有 helper 都应使用已经解析好的运行时平台上下文，不能仅凭 `provider_id` 重新读取全局凭据。

## 6. LLM、聊天与内容分析

| HTTP 入口 | 页面/调用方 | 上游类型 | 流式 | 配置组要求 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `POST /api/canvas-llm` | 画布 LLM节点 | OpenAI兼容、ModelScope、Codex、Gemini | 否 | 按用户配置组解析 | 待确认 |
| `POST /api/chat` | GPT 对话页 | 多 LLM平台，也可能调用图片平台 | 否 | 聊天和图片模式分别按本组解析 | 待确认 |
| `POST /api/chat/stream` | 流式聊天 | 多 LLM平台 | 是 | 流开始时固定配置组 | 待确认 |
| `POST /api/chat/agent` | Agent 对话 | 多 LLM/工具 | 否 | Agent内部二次调用不能绕过作用域 | 待确认 |
| `POST /api/chat/agent/stream` | 流式 Agent | 多 LLM/工具 | 是 | 整个流固定用户与配置组 | 待确认 |
| 素材自动分类/描述 | 素材管理器 | LLM或视觉模型 | 否/批量 | 必须纳入用户审计和配置组 | 待确认 |
| 数字人/真人认证相关接口 | 素材库 | APIMart等 | 是 | 注册、状态查询使用同一配置组 | 待确认 |

相关素材接口至少包括：

- `POST /api/asset-library/items/classify`
- `POST /api/asset-library/items/{item_id}/register-avatar`
- `POST /api/asset-library/items/{item_id}/avatar-status`

## 7. RunningHub

| HTTP 入口 | 用途 | 配置组要求 | 特殊注意点 | 状态 |
| --- | --- | --- | --- | --- |
| `GET /api/runninghub/app-info` | 应用信息 | 返回当前组可用信息 | 静态模板与组内覆盖的边界 | 已改造 |
| `POST /api/runninghub/submit` | 提交生成 | 当前用户配置组 | 纳入统一用量事件 | 已改造 |
| `POST /api/runninghub/workflow-submit` | 提交工作流 | 当前用户配置组 | 工作流与钱包类型一致 | 已改造 |
| `GET /api/runninghub/workflow-info` | 工作流信息 | 当前用户组或管理员选定组 | 不泄漏其他组隐藏配置 | 已改造 |
| `GET /api/runninghub/workflows` | 工作流列表 | 静态模板共享、组内配置隔离 | version 2存储按配置组分区 | 已改造 |
| `GET /api/runninghub/workflows/{workflow_id}` | 单工作流 | 同上 | 路径和权限校验 | 已改造 |
| `POST /api/runninghub/workflows/fetch` | 拉取工作流 | 仅管理员、选定组 | 使用正确账户 | 已改造 |
| `PUT /api/runninghub/workflows/{workflow_id}` | 保存工作流配置 | 仅管理员、选定组 | 防止覆盖其他组 | 已改造 |
| `DELETE /api/runninghub/workflows/{workflow_id}` | 删除组内配置 | 仅管理员、选定组 | 内置工作流通过组内隐藏记录处理 | 已改造 |
| `GET /api/runninghub/query` | 查询任务 | 固定提交配置组 | 查询阶段不得换钱包类型 | 已改造 |
| `POST /api/runninghub/upload-asset` | 上传素材 | 固定提交配置组 | 上传、提交、查询使用同一 `useWallet` | 已改造 |

必须保留的兼容约束：

- 企业共享钱包调用旧接口时的 Bearer-only回退；
- 上传、提交、查询使用同一钱包选择；
- 普通 Key和钱包 Key分别按配置组保存；
- 静态系统工作流可以共享，账户相关覆盖必须隔离；
- 运行相关测试时覆盖 `tests/test_runninghub_wallet_auth.py`（如文件存在或实施阶段补充）。

## 8. 火山引擎、ModelScope 与 CLI

| 平台/入口 | 当前凭据或状态 | 目标作用域 | 状态 |
| --- | --- | --- | --- |
| 火山引擎普通 API Key | 全局 | 配置组 | 待确认 |
| 火山 Access Key ID | 配置组环境变量 + `UpstreamContext` | 配置组 | 已改造 |
| 火山 Secret Access Key | 配置组环境变量 + `UpstreamContext` | 配置组 | 已改造 |
| ModelScope Token | 配置组环境变量 + `UpstreamContext` | 配置组或明确共享 | 已改造 |
| `GET /api/jimeng/status` | 本地 CLI/登录状态 | `local/shared`，不宣称部门隔离 | 已改造 |
| `GET /api/jimeng/credit` | 即梦余额 | 仅管理员 | 普通用户不可查看共享账户余额 | 已验证 |
| 即梦登录、登出与帮助接口 | 管理 CLI会话 | 仅管理员 | 本地会话是否能按组隔离需单独决策 | 待确认 |
| `GET /api/codex/status` | 本地 Codex状态 | `local/shared` | 用量与并发策略进入 M6 | 已改造 |
| `POST /api/codex/help` | Codex帮助/管理 | 仅管理员 | 不暴露认证文件 | 待确认 |
| `GET /api/gemini-cli/status` | 本地 Gemini状态 | `local/shared` | 用量与并发策略进入 M6 | 已改造 |
| `POST /api/gemini-cli/help` | Gemini帮助/管理 | 仅管理员 | 不暴露认证文件 | 待确认 |

本地 CLI通常不能像普通 API Key一样自然复制四份。实施前必须明确：

- 作为公共本地资源共享并单独记账；
- 是否支持多登录会话；
- 若不支持多会话，普通用户不能把它误认为部门独立充值账户；
- CLI并发与本地计算配额如何限制。

## 9. ComfyUI 与工作流

| HTTP 入口 | 用途 | 建议计费性质 | 配置组要求 | 状态 |
| --- | --- | --- | --- | --- |
| `GET /api/comfyui/instances` | 实例列表 | local/shared | 普通用户仅获取可用能力 | 待确认 |
| `PUT /api/comfyui/instances` | 管理实例 | local/shared | 仅管理员 | 待确认 |
| `GET /api/workflows` | 工作流列表 | local/shared | 明确全局共享或组内可见性 | 待确认 |
| `GET /api/workflows/{name}` | 获取工作流 | local/shared | 保持路径安全 | 待确认 |
| `POST /api/workflows` | 新增工作流 | local/shared | 仅管理员 | 待确认 |
| `PUT /api/workflows/{name}/config` | 保存字段映射 | local/shared | 仅管理员 | 待确认 |
| `DELETE /api/workflows/{name}` | 删除工作流 | local/shared | 仅管理员；内置不可删 | 待确认 |
| `POST /api/workflows/{name}/run` | 测试工作流 | local/shared | 绑定测试发起人、审计和配额 | 待确认 |

即使 ComfyUI 实例继续全局共享，也要记录实际使用用户、部门和配置组快照，以便核算本地算力。

## 10. 智能画布工作流导入导出

以下入口通常不直接读取外部密钥，但导入的节点会保存平台 ID和运行设置，需要验证跨配置组兼容：

- `POST /api/canvas-workflows/export`
- `POST /api/canvas-workflows/export-to-library`
- `POST /api/asset-library/workflows/upload`
- `POST /api/canvas-workflows/import`

验证要求：

- 导入其他部门创建的工作流不能获得其配置组或凭据；
- 工作流只保存逻辑平台 ID、模型和非敏感参数；
- 在目标用户配置组不存在的平台必须提示重新选择；
- 不允许导入 `api_profile_id` 后改变用户归属；
- ZIP中不得携带凭据文件。

## 11. 前端页面与客户端

| 客户端 | 重点文件/页面 | 需要验证 | 状态 |
| --- | --- | --- | --- |
| API 设置页 | `static/api-settings.html`、`static/js/api-settings.js` | 管理员切换组、复制配置、保存不串组 | 待确认 |
| 普通画布 | `static/canvas.html`、`static/js/canvas.js` | 平台列表只来自当前组；旧 localStorage兼容 | 待确认 |
| 智能画布 | `static/smart-canvas.html`、`static/js/smart-canvas.js` | 节点设置、级联、循环、异步恢复 | 待确认 |
| 在线生图 | `static/online.html` | 平台、模型和任务查询 | 待确认 |
| GPT 对话 | `static/gpt-chat.html` | 聊天、流式、图片模式 | 待确认 |
| 视角控制 | `static/angle.html` | ModelScope归属和轮询 | 待确认 |
| 独立生成页 | `zimage.html`、`enhance.html`、`klein.html`等 | 旧直连接口和全局默认值 | 待确认 |
| 管理后台 | `static/admin.html` | 用户分组、配置组用量和告警 | 待确认 |
| Chrome扩展 | `tools/chrome-local-asset-importer/` | 登录换取可撤销 Bearer；平台读取、导入和素材分析绑定用户 | 已改造 |
| Photoshop面板 | `tools/photoshop-asset-connector/`及相关桥接工具 | Bearer、媒体 Token、WebSocket和用户任务过滤 | 已验证（自动契约） |

前端不得把 `api_profile_id` 当作普通用户可修改的生成参数。管理员页面使用配置组参数时，服务端仍需管理员鉴权。

## 12. WebSocket、媒体与共享状态

| 能力 | 风险 | 检查要求 | 状态 |
| --- | --- | --- | --- |
| `/ws/stats` | 广播结果可能跨部门可见 | 完整生成结果只发任务所有者；共享画布/素材通知不含凭据和计费 | 已验证 |
| 生成历史 | 结果可能全局共享 | 本次是否调整可见性需明确，不与凭据隔离混淆 | 待确认 |
| `/assets`、`/output` | 媒体访问认证 | 不在 URL中加入真实上游凭据 | 待确认 |
| 画布保存 | 节点保存平台 ID | 不保存配置组或密钥；重载时按当前用户解析 | 待确认 |
| 浏览器 localStorage | 旧平台选择残留 | 本组无该平台时提示并清理，不跨组回退 | 待确认 |

本次核心目标是凭据和费用隔离。媒体、画布和历史是否进一步按部门隔离，应作为明确的独立产品决定，不能在本次改造中无意改变现有共享行为。

## 13. 异步与后台任务清单

每一类任务都要确认：

- 谁提交；
- 提交时配置组；
- 实际平台；
- 实际凭据类型；
- 后台 worker如何取得配置；
- 服务重启后如何恢复；
- 谁能轮询；
- 用户调组后的行为；
- 密钥轮换后的行为；
- 用量事件是否复用。

当前至少包括：

- 画布图片任务；
- 画布 ComfyUI任务；
- 通用异步图片任务；
- 通用视频任务；
- RunningHub任务；
- 即梦待处理任务；
- 火山任务；
- 数字人认证任务；
- 聊天流式任务；
- 智能画布级联与循环产生的多任务。

## 14. 审计与管理员接口

| 入口/数据 | 当前情况 | 改造要求 | 状态 |
| --- | --- | --- | --- |
| 用户记录 | 有 `department` | 增加管理员维护的 `api_profile_id` | 待确认 |
| `GET /api/admin/users` | 管理用户 | 返回非敏感配置组归属 | 待确认 |
| `PATCH /api/admin/users/{user_id}` | 更新角色、启停、配额 | 增加配置组分配；验证管理员权限 | 待确认 |
| 用量事件 | 已记录用户、部门、平台和模型 | 增加配置组 ID、名称快照、计费性质、凭据类型和上游任务 ID | 已改造 |
| 用量汇总 | 支持用户、部门、平台等维度 | 增加配置组、计费性质和凭据类型维度 | 已改造 |
| 用量策略 | 用户配额和全局告警 | 增加配置组并发、软预算和显式硬限额策略 | 已改造 |
| 告警 | 主要按用户与类别 | 增加配置组、计费性质和预算上下文 | 已改造 |
| `GET /api/admin/usage/export` | 无 | 按当前筛选导出脱敏 JSON | 已新增 |
| `GET /api/admin/usage/profiles` | 无 | 配置组用量、计费性质、凭据类型和预算汇总 | 已新增 |

## 15. 安全负面清单

实施和评审时必须搜索并拒绝：

- 客户端提交 `api_profile_id` 后直接生效；
- 通过部门名称拼接环境变量；
- 请求开始时修改全局 API Key；
- 深层 helper只接收 `provider_id` 并重新读取全局配置；
- 明确平台不存在时调用全局首选平台；
- 任务记录中保存真实密钥；
- 普通用户获得密钥尾号、变量名或其他组平台清单；
- 错误响应中拼接上游 Authorization、Cookie、请求体密钥；
- 导出画布或工作流时写入配置组凭据；
- 扩展使用真实上游 Key代替系统颁发的可撤销 Token；
- 共享账户在错误时自动兜底；
- 管理员保存一个组时覆盖整个全局平台文件。

## 16. 完整性检查方法

每个实施阶段结束前，至少重新搜索：

- 所有 `get_api_provider` 调用；
- 所有凭据读取 helper；
- 所有构造 Authorization、Bearer、AK/SK 的位置；
- 所有生成、提交、查询、轮询路由；
- 所有后台任务创建点；
- 所有平台 ID和模型列表返回接口；
- 所有前端 `fetch()` 和 WebSocket调用；
- Chrome、Photoshop扩展调用；
- 所有写入用量事件的位置；
- 所有把 provider ID写入画布、任务和历史的位置。

发现新入口后先补充本清单，再实施代码修改。

## 17. 清单关闭条件

整份清单关闭前必须满足：

- 所有全局凭据读取点已删除、封装或明确标记为兼容层；
- 所有付费入口均可追溯到统一配置组解析；
- 所有异步任务保存并使用提交配置组；
- 所有普通用户响应经过敏感字段检查；
- 所有扩展入口通过隔离验证；
- 本地/共享服务有明确计费性质；
- 前端旧平台选择不会导致跨组回退；
- 审计事件可以解释每一笔调用的经费归属；
- 未验证的外部平台有明确原因和后续责任人。
