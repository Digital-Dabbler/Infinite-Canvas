# 智能画布"回退"问题修复记录（2026-08-27）

> **状态：历史排障记录。** 本文描述的是节点操作同步改造前的整图快照保存与合并路径，保留用于理解旧回退症状和迁移背景，**不是当前实现标准**。当前长期约束以 [节点操作同步设计](superpowers/specs/2026-09-02-smart-canvas-node-operation-sync-design.md) 与 [公司内分享与协作授权设计](superpowers/specs/2026-09-03-smart-canvas-sharing-acl-design.md) 为准；不得据本文恢复整图实时拉取、整图合并或旧快照回写。

> 若再次出现"画布状态莫名回退到几秒前"、"生图时节点消失/任务状态消失"，可把本文用作症状对照，但排查和修复必须遵守现行节点操作协议与授权边界。涉及文件：`static/js/smart-canvas.js`、`main.py`。

## 1. 摘要

- **问题**：画布经常"自动回退"——用户刚做的编辑（拖节点、改文字、增删节点）在几秒内莫名消失，
  像被按了 Ctrl+Z；生成图片时还会出现"上游节点消失但任务还在，按撤销后节点回来但任务状态没了"。
- **根因**：画布存在 3 个写者、5 条"服务端→客户端"拉取路径，合并逻辑在**本地还有未保存改动**时
  仍会用服务端旧快照覆盖内存；另有合并启发式会杀掉本地更新的生成任务、删除墓碑生命周期断裂。
- **修复**：一个原则（本地未保存编辑优先，静止时接受远端，合并只增不减）+ 5 项改动，
  全部在唯一入口 `mergeReloadCanvasNow` 收口。共约 45 行 JS + 9 行 Python，无新子系统。

## 2. 修复前的问题现象

| 症状 | 用户描述 |
| --- | --- |
| 回退 | 编辑节点后几秒内，界面跳回几秒前的旧快照（位置/文本/节点消失） |
| 生图时节点消失 | 生成任务运行中，其上游连接节点突然从画布消失，但任务状态还在 |
| 撤销的副作用 | 按 Ctrl+Z 后节点回来，但当前生成任务状态被还原成旧快照，任务消失 |
| 可恢复性 | 回退的内容按 Ctrl+Z 能找回一部分，但伴随上述任务状态丢失 |

## 3. 根因分析（数据流全景）

### 3.1 同一份画布数据有 3 个写者

| 写者 | 时机 | 位置 |
| --- | --- | --- |
| ① 客户端防抖保存 | 每次操作后 450ms | `scheduleSave` → `saveCanvas`（PUT `/api/canvases/{id}`） |
| ② 服务端任务提交写入 | 任务提交时把 `pendingTasks` 写进节点 | `main.py: register_bound_canvas_task` |
| ③ 服务端任务完成写入 | 成功/失败时写结果、清 pending、写日志 | `main.py: finalize_bound_canvas_task` |

②③ 都会 bump `updated_at` 并广播 `canvas_updated`，且**广播不带 client_id**（自己的页面也会收到）。

### 3.2 5 条"服务端→客户端"拉取路径

| 路径 | 触发 | 位置（修复前） |
| --- | --- | --- |
| A. 广播合并重载 | 任意写者广播 | `handleCanvasUpdatedMessage` → `mergeReloadCanvasNow` |
| B. 8 秒 meta 轮询 | 周期轮询，`updated_at` 变新就拉全量合并 | `startCanvasMetaPoll` |
| C. 409 冲突回程 | 保存被拒时拉服务端副本合并后重存 | `saveCanvas` 409 分支 |
| D. 提交后主动合并 | 服务端接管任务后主动拉一次 | `runGeneration` 提交后 `await mergeReloadCanvasNow()` |
| E. 日志删除全量替换 | 删日志后用服务端返回整体替换 nodes | `deleteCanvasLogEntry` |

### 3.3 三个根因

- **根因 A（回退主因）**：多写者 + 合并无"本地未保存"门控。②③ 的广播（无 client_id）与 8 秒轮询
  会在本地 450ms 防抖窗口内触发合并，而合并规则是"非运行节点以服务端旧版本为准"
  （旧 `mergeSmartNode` 默认 `{...remote, images}`），刚做的编辑被旧快照覆盖，随后防抖保存固化。
- **根因 B（任务状态消失）**：旧 `mergeSmartNode` 分支 `remoteDone && localBusy && !localDone`
  无条件以服务端完成态为准——本地刚启动的新一轮生成（`runStartedAt` 更新）会被服务端**上一轮**
  的旧完成态冲掉，pendingTasks/运行状态被抹掉。
- **根因 C（节点消失且救不回）**：删除墓碑 `localDeletedNodeIds` 生命周期断裂。
  - 删除时 id 进墓碑，**撤销 `performUndo` 只恢复 nodes，不还原墓碑**（新建节点路径会清墓碑，撤销漏了）；
  - 下次合并 `mergeSmartNodeLists` 中 `localDeletedNodeIds.has(id) → return null` → 节点当场消失
    （生成中的节点因 in-flight 保留，所以"任务还在、节点没了"）；
  - 下次保存 `deleted_node_ids` 仍带该 id → 服务端 `update_canvas` 把 payload 中实际存在的这个节点
    过滤掉（服务端墓碑是只增并集、2000 条封顶）→ 服务端副本永久丢失；
  - 此时按撤销弹出的是生图前的旧快照 → 节点回来、任务状态回到旧快照。

## 4. 修复内容（2026-08-27 已实施）

**原则**：本地未保存的编辑优先；静止时接受远端；合并永远只增不减（图片/节点/连线/日志并集）。
"服务端状态进入客户端"收口到唯一入口 `mergeReloadCanvasNow`。

### 4.1 客户端 `static/js/smart-canvas.js`

| # | 改动 | 语义 | 锚点（当前行号） |
| --- | --- | --- | --- |
| 1 | 新增 `canvasDirty` / `pendingRemoteMerge` 两个状态 | 本地有未保存改动时禁止任何服务端快照覆盖内存 | `let canvasDirty` L6773 / `let pendingRemoteMerge` L6774 |
| 1a | `scheduleSave()` 置 `canvasDirty = true` | 所有改动的共同入口，天然覆盖"打字/拖拽/撤销"等全部场景 | `scheduleSave` L8136 |
| 1b | `saveCanvas` 成功：置 `canvasDirty = false`；若 `pendingRemoteMerge` 则补拉 | 保存成功即门控放行，协作同步不倒退 | `saveCanvas` L8141 |
| 1c | `mergeReloadCanvasNow` 入口：`canvasDirty` 时置 `pendingRemoteMerge` 并跳过 | **唯一闸门**，广播(A)/轮询(B)/主动合并(D) 全部被门控 | `mergeReloadCanvasNow` L7169 |
| 2 | 409 冲突合并改 `applyMergedServerCanvas(serverCanvas, {preferLocal:true})` | 冲突时本地是活动方，共享节点以本地为准；对方新增节点/连线/图片仍并集 | `saveCanvas` 409 分支（L8141 内） |
| 2a | `preferLocal` 贯穿 `mergeSmartNode` / `mergeSmartNodeLists` / `applyMergedServerCanvas` | 广播合并（静止）保持远端优先 → 协作不倒退；409 合并（活动）本地优先 | L7064 / L7099 / L7143 |
| 3 | `mergeSmartNode` 的 `remoteDone && localBusy` 分支时间化：`local.runStartedAt > remote.runFinishedAt` → 本地优先 | 本地新一轮运行不被服务端上一轮旧完成态冲掉；`runStartedAt` 缺失回退旧行为 | L7064 内 |
| 4 | `snapshotForUndo` 记录 `deletedIds`/`unsyncedIds`；`performUndo` 恢复两个 Set | 撤销删除后节点不再被下次合并/保存当成熟人丢掉 | `snapshotForUndo` L243 / `performUndo` L262 |
| 5 | 新增 `syncTombstonesFromServer`（双向墓碑收敛） | 服务端 `deleted_node_ids` 并入本地墓碑；服务端 nodes 中实际存在的 id 从本地墓碑移除（服务端有它=它活着） | `syncTombstonesFromServer` L7132 |
| 5a | `syncTombstonesFromServer` 接入 `applyMergedServerCanvas` / `loadCanvas` / `deleteCanvasLogEntry` | 所有从服务端取得全量画布的地方墓碑一致 | L7143 / `loadCanvas` / `deleteCanvasLogEntry` |

### 4.2 服务端 `main.py`（+9 行）

| 改动 | 语义 | 锚点 |
| --- | --- | --- |
| `update_canvas`：计算 `incoming_ids`（本次 payload.nodes 中实际存在的 id），从 `deleted_node_ids` 中移除 | 墓碑只增不减会让撤销恢复的节点被永久误杀；移除后复活在所有客户端收敛 | `update_canvas`（PUT `/api/canvases/{id}`） |

> 注意：第 4 项客户端撤销墓碑修复**必须**配合本服务端改动才完整——
> 服务端旧墓碑是"并集只增"的，仅改客户端修不干净（详见第 3.3 节根因 C）。

## 5. 修复后的预期行为（对照基线）

| 场景 | 修复前 | 修复后（正确行为） |
| --- | --- | --- |
| 编辑节点后 8 秒内不做任何操作 | 几秒内跳回旧快照 | 保持编辑结果，防抖保存后稳定 |
| 生成任务完成（服务端写画布并广播） | 触发合并，可能覆盖本地未保存编辑 | 本地有未保存改动时跳过合并，保存成功后补拉 |
| 生图时删除上游节点 → 撤销 | 节点回来后任务状态被还原成旧快照 | 节点回来且任务状态保留 |
| 双标签页/多端协作 | 偶发互相覆盖 | 静止端接受远端（合并只增不减），活动端编辑不被覆盖 |
| 撤销一次删除 | 节点仍会被下次合并/保存再次删除 | 节点恢复并持久化，墓碑双向收敛 |

## 6. 已知取舍与残余风险（可接受）

1. **同节点并发编辑**：两人同时编辑同一节点时，后保存方覆盖先保存方（last-writer-wins）。
   409 用 preferLocal 后，"输家"从后保存方变为先保存方。仅发生在同画布同节点并发编辑时。
2. **保存失败 + 停止编辑**：`canvasDirty` 卡真会暂停拉取远端；下一次编辑重新触发保存即恢复。
3. **时钟偏差**：`runStartedAt`/`runFinishedAt` 为客户端时间戳，跨设备时钟偏差仅在极罕见的
   两端先后对同一节点发起不同轮任务时可能误判。
4. **节点级冲突精度**：无 per-node 版本号，合并按节点级 last-writer-wins 处理。

## 7. 验证记录（2026-08-27）

- ✅ `node --check static/js/smart-canvas.js`
- ✅ `main.py` AST 语法检查（`.\python\python.exe -c "import ast, pathlib; ast.parse(...)"`）
- ✅ `git diff --check`（无空白错误）
- ✅ `test_smart_canvas_only.py` 11/11、`test_photoshop_bridge.py` 26/26、`test_m6_usage_accounting.py` 10/10
- ⚠️ `test_canvas_log_cleanup.py` 1 失败 10 错误——**存量失配，与本次改动无关**（已用
  `git stash push -- main.py` 对照验证：HEAD 原始 main.py 下同样失败）。原因：该测试 fixture
  写入的画布缺 `kind:"smart"` 字段，而 `load_canvas` 的 kind 检查在修复前就存在。如需跑该测试，
  需先给 fixture 补 `"kind": "smart"`（不在本次改动范围内）。
- ⏳ **未做**：浏览器端到端验收（未启动服务，避免 `sync_static_html_versions()` 改写 HTML 的 `?v=` 缓存戳）。
  上线前建议实测：①编辑后 8 秒窗口不回退；②生成中删除→撤销；③双标签页协作。

## 8. 未来排查指引（旧病复发时）

### 8.1 复现步骤

1. 打开一张智能画布，拖一个节点，**不做任何操作等 8 秒**——若位置跳回，走检查点 C/D；
2. 删一个节点后按 Ctrl+Z，再等 8 秒——若节点再次消失，走检查点 B；
3. 生图进行中删除其上游节点 → 撤销——若任务状态丢失，走检查点 A/D。

### 8.2 检查点（按优先级）

| 检查点 | 查什么 | 判定 |
| --- | --- | --- |
| A. 合并启发式 | `mergeSmartNode` 的 `remoteDone && localBusy` 分支是否被改回无条件 remote 优先 | 若改回，新任务状态会被旧完成态冲掉（根因 B 复发） |
| B. 撤销墓碑 | `performUndo` 是否还恢复 `deletedIds`/`unsyncedIds`；`syncTombstonesFromServer` 是否还在 `applyMergedServerCanvas`/`loadCanvas` 调用 | 若缺失，撤销恢复的节点会被再次丢掉（根因 C 复发） |
| C. 脏标记门控 | `mergeReloadCanvasNow` 入口是否还有 `canvasDirty` 检查；`scheduleSave` 是否置脏、`saveCanvas` 成功是否清脏 | 若缺失，广播/轮询会在防抖窗口覆盖未保存编辑（根因 A 复发） |
| D. 409 preferLocal | `saveCanvas` 409 分支是否还传 `{preferLocal:true}` | 若改回默认，保存被拒时刚做的编辑被旧快照覆盖 |
| E. 服务端墓碑 | `update_canvas` 是否还有 `incoming_ids` 剪枝逻辑 | 若缺失，撤销恢复的节点被服务端永久误杀 |

### 8.3 新增改动时的红线

- **不要**新增绕过 `mergeReloadCanvasNow` 的"服务端→客户端"全量状态替换（如新的 fetch 后直接
  `nodes = data.canvas.nodes`），否则会绕过唯一闸门。
- **不要**把 `scheduleSave` 与"仅 render 不保存"的路径混用后忘记置脏/清脏。
- 修改合并/撤销/墓碑任一逻辑时，三处（`mergeSmartNode`、`performUndo`、`syncTombstonesFromServer`+
  服务端 `incoming_ids`）必须一起考虑，它们是同一根因的三条腿。
- 新节点字段必须提供默认值（AGENTS.md 画布兼容约束），确保旧画布可加载、保存、再加载。

### 8.4 关键代码锚点速查（2026-08-27 行号，会漂移，以函数名为准）

| 函数 | 文件 | 职责 |
| --- | --- | --- |
| `snapshotForUndo` / `performUndo` | smart-canvas.js L243 / L262 | 撤销快照含墓碑/未同步标记 |
| `canvasDirty` / `pendingRemoteMerge` | smart-canvas.js L6773 / L6774 | 脏标记门控状态 |
| `mergeSmartNode` / `mergeSmartNodeLists` | smart-canvas.js L7064 / L7099 | 节点合并（preferLocal + 时间化） |
| `syncTombstonesFromServer` | smart-canvas.js L7132 | 墓碑双向收敛 |
| `applyMergedServerCanvas` | smart-canvas.js L7143 | 合并入口（options.preferLocal） |
| `mergeReloadCanvasNow` | smart-canvas.js L7169 | **唯一闸门**（canvasDirty 检查） |
| `scheduleSave` / `saveCanvas` | smart-canvas.js L8136 / L8141 | 置脏 / 清脏 + 409 preferLocal |
| `update_canvas` | main.py PUT `/api/canvases/{id}` | 服务端墓碑剪枝（incoming_ids） |

## 9. 本次改动文件清单

- `static/js/smart-canvas.js`：约 45 行净增（9 个改动点）
- `main.py`：+9 行（`update_canvas` 墓碑剪枝）
- 本文档：`docs/smart-canvas-sync-revert-fix-record.md`

## 10. 追加修复（2026-08-27 同日）：文本节点编辑被打断 / 生成面板自动关闭

> 第 1-9 节修复落地后，用户反馈另一症状：文本节点编辑中会**突然退出编辑状态**，
> 有时已输入内容（尤其中文输入法合成中）丢失；焦点落在文本节点时打开的生成面板
> **经常自己关闭**；表现类似"切走浏览器标签页再切回来"（焦点还在文本节点，面板却没开）。

### 10.1 与第 1-9 节修复的关系

**同一根引线（同步/合并机制调用全量 `render()`），但破坏机制独立，且有一个与同步无关的触发源。**

### 10.2 破坏机制（证据链）

1. `render()` 的 `reusableNodes` 只保留"有实时媒体"的节点（图片/视频）；
   **文本节点不在其中 → 每次 `render()` 都会销毁重建 textarea**。
2. textarea 被销毁 → 触发 blur → onblur（`bindTextNodeControls`，约 L11819）
   `promptTextEditingIds.delete` + `promptPanelClosedIds.add` → **编辑态与生成面板被永久关闭**，
   onblur 里又 `render()` 一次。
3. 已输入内容丢失 = **输入法合成会话被打断**（`node.text` 每次按键已同步，丢的是合成中未提交部分）。
   代码里 L11323 的注释早已知道"移动 DOM 会打断输入法合成"，但只保护了 `promptInput`，没保护文本节点。
4. 触发源清单：
   - 8 秒 meta 轮询合并、广播合并、409 合并（编辑停顿 dirty 变 false 后照常触发）——第 1-9 节的门控降低了频率但没消除伤害；
   - **第 1-9 节新增的"保存成功后补拉合并"**（pendingRemoteMerge → +200ms）是新增触发点；
   - **切标签页**：window focus → `scheduleSmartConfigRefresh(420)` → 配置签名变化时 `render()`（`refreshSmartConfigFromSettings`，约 L5932）——独立触发源，与同步无关。

### 10.3 修复内容（同日已实施）

| # | 改动 | 语义 | 锚点 |
| --- | --- | --- | --- |
| Fix A | `render()` 重建 DOM 时**保留正在编辑的文本节点元素**：`promptTextEditingIds` 中的节点不进销毁/重建流程（加入 `keepEls`、跳过 fresh 追加） | textarea/焦点/输入法合成状态原样保留，**所有**触发源同时失效，不再逐个堵 | `render()`（`editingNodeIds` 计算 + keepEls + 追加循环） |
| Fix C | `renderTextNodePanel` 面板内有焦点且面板仍属于当前选中节点时不重建面板 DOM，只重定位 | 面板自身输入框（如指令框）不被重建打断；选中切换时仍正常重建 | `renderTextNodePanel` |

两处约 20 行，无新状态、无行为分叉；与第 1-9 节完全兼容（门控防数据回退，DOM 保留防编辑中断，各管一层）。

### 10.4 未来排查（此症状复发时）

- 检查 `render()` 里 `editingNodeIds` 是否还在：若被移除，任何 render 调用都会再次打断文本编辑。
- 检查 `renderTextNodePanel` 的 `activeInsidePanel` 分支是否还在：若被移除，面板内输入会再次被打断。
- 新触发源上线前自问：**它会不会在用户编辑文本节点时调用 `render()`？** 会的话依赖 Fix A 兜底，不要把 render 调用点绕开 Fix A 的保护逻辑。

### 10.5 验证

- ✅ `node --check static/js/smart-canvas.js`、`git diff --check`
- ⏳ 浏览器实测（未启动服务）：①文本节点输入中文/英文，等待 8 秒轮询与保存补拉，确认编辑不退出、面板不关闭、内容不丢；②编辑中切走再切回标签页；③在面板指令框输入时等待轮询。
