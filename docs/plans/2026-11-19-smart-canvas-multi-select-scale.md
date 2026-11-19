# 智能画布「多选整体等比缩放」改动计划书

日期：2026-11-19
状态：4 项决策点已由用户批注确认（2026-11-19，见第 5 节），仍未动工
需求来源：用户 m00003 + 参考图 m00001（PureRef 式选区外框：多选节点外围出现带操作杆的缩放框，拖动操作杆所有选中节点整体等比缩放）
分支基线：`fix/canvas-node-field-deletion-sync`（领先 origin 5 个提交；拆图/合并功能刚落地 f9244be）

## 0. 一句话说明

框选或 Shift 点选多个节点后，选区外接矩形四周显示 8 个操作杆（4 角 + 4 边中点），**全部是等比缩放**：角杆以对角顶点为不动点，边杆以对边中点为不动点；按住 Alt 改为以选区中心缩放。拖动时所有选中节点位置、大小一起按比例走；松手后一步撤销可整体还原，改动按现有窄操作自动保存并广播。

## 1. 现状调研结论（代码锚点）

以下行号以 2026-11-19 当前分支 HEAD 为准，全部在 `static/js/smart-canvas.js`，除非注明。

### 1.1 选中模型

- 选中状态是 `selectedId`（单选）+ `selectedIds`（多选），统一入口 `selectedNodeIds()`（:2083），刷新 UI 走 `syncSelectionUi()`（:2054）。
- 框选：`selectionState` + 屏幕坐标的 `#selectionBox`（:27193-27219），松手后按 `nodeRect` 相交收集 id。
- 多选 ≥2 时 `world` 加 `smart-multi-selected` 类（:2060），并浮现排版工具栏 `layoutToolbar` 与合并药丸 `mergeToolbar`（:12286、:12322）。

### 1.2 现有缩放（单节点）

- 每个节点右下角 `.node-resize-handle`（样式 `static/css/smart-canvas.css:970`）mousedown 建立 `resizeState` 快照（:16795-16825），window mousemove 实时写 `node.w/h`（:27817-27909），mouseup `commitPendingUndo + render + scheduleSave`（:28057-28068）。
- 各类型已有特例，可直接借鉴：
  - 便签等比缩放：框与字号同因子，`sizeMode='fixed'`（:27880-27905）；
  - 旧 smart-group 分组缩放：手势开始记录成员快照，按相对比例整体缩放+重排（:27833-27878）——**这就是本需求要的数学模型，只是作用范围从"组内"扩到"选区"**；
  - 最小尺寸表（:27822-27823）：prompt 260×170、loop 252×132、smart-group SMART_GROUP_MIN_*、组织框 300×180、便签 NOTE_MIN_*、其余 48。

### 1.3 节点尺寸模型（关键前提）

- `nodeRect(node)`（:2864）：组织框/便签读显式 `x/y/w/h`；内容节点走 `imageLayout(images, nodeScale, node)`（:2765）。
- `imageLayout` 各分支都遵守同一条规则：**节点带显式 `w/h`（>24）时以显式值为准，否则按图片自然比例推导**（:2455-2509、:2786-2793）。卡片内图片预览是百分比贴合，卡片变大图片跟着变大，不会失真裁切。
- 结论：整体缩放只需给每个节点写显式 `w/h`（再按类型补 `fontSize` 等），不需要新字段、不需要动服务端。

### 1.4 持久化与同步（复用现有链路，零服务端改动）

- 保存链路：`scheduleSave()`（:8823）→ `saveCanvasOperations()`（:9068）对比 `canvasOperationBase` 逐节点 diff 字段 → 每个节点一条 `node_fields` 窄操作 POST `/api/canvases/{id}/operations` → 服务端广播、其他端 `handleCanvasOperationMessage`（:8932）应用。
- 拖动过程中不保存（现有 resize/drag 都只在 mouseup 调 `scheduleSave`），松手一次批量提交，符合 AGENTS.md「细粒度、可校验、幂等操作」边界。
- 撤销：手势开始 `capturePendingUndo()`（:272），结束 `commitPendingUndo()/discardPendingUndo()`，一步撤销整组变化。

### 1.5 可复用的"原子单元"逻辑

- `layoutSelectionUnits()`（:2990）：把 workflow 组织框的成员折叠成框本身，避免框和成员被当成两个对象重复处理。
- `smartArrangeAtomicIds()`（:2897）：把旧 smart-group 成员折叠进组本身。
- 整体缩放必须沿用这两套归并，否则会出现"框缩放一次、框内成员又被缩放一次"的重复缩放。

## 2. 方案设计

### 2.1 交互与 UI

| 项 | 方案 |
| --- | --- |
| 出现条件 | `selectedNodeIds()` 归并成单元后 ≥ 2（与排版工具栏同一门槛）；单选不显示（单选已有自己的右下角手柄） |
| 外框 | 在 `world` 图层内新增 `#selectionScaleFrame`（与节点同坐标系，随 `viewport` 平移缩放，:3136 处 transform 更新时同步刷新位置）；蓝色 1px 边框，纯视觉不拦截事件（`pointer-events:none`，仅手柄可点） |
| 操作杆 | **8 个全做，全部等比**（用户批注 1）：4 角手柄 + 4 边中点手柄。角杆拖动以对角顶点为不动点；边杆拖动以「对边中点」为不动点（例：拖右边杆，左边杆中心钉住不动，整体向右侧等比放缩）。**不做单轴拉伸**——边杆只是换了不动点的等比缩放 |
| 手柄尺寸 | 手柄用 `transform: scale(1/viewport.scale)` 反缩放，任何画布倍率下屏幕上保持 ~12px 恒定大小 |
| 光标/样式 | 复用 `nwse-resize` 等光标；手势期间 body 加 `smart-node-resize smart-canvas-interacting` 类（沿用 :16823 的做法，自动隐藏节点浮动菜单等） |
| 隐藏时机 | 节点拖动、单节点 resize、框选、平移、缩放画布时隐藏外框（照抄 `currentLayoutToolbarUnits` :12245 的 gating 模式）；手势结束后重新贴合 |
| 只读画布 | viewer 不显示手柄（与现有编辑入口一致；服务端统一画布访问校验仍是最终防线） |

### 2.2 手势数学

- **锚点**：角手柄 = 对角顶点；边手柄 = 对边中点（用户批注 1：拖右边杆时左边中点钉住）；按住 Alt 时两类手柄都改为以选区外接框中心为锚（用户批注 2）。Alt 只在缩放手势内生效，与节点上的 Alt+拖动复制（:16852）互不干扰——手柄 mousedown 已 `stopPropagation`，不会走到节点拖动链。
- **因子**：`factor = clamp(投影比, minFactor, ∞)`，算出的 factor **一律同时作用于 x/y/w/h**，边杆同样是严格等比，只是不动点不同。投影比按手柄类型取：角手柄 = 指针相对锚点位移在「锚点→起始角」对角线上的投影 / 起始对角线长（与 `editTextResize` :17543-17548 同一算法，天然等比、跟手）；左右边杆 = 横向位移 / 起始框宽；上下边杆 = 纵向位移 / 起始框高；Alt 中心模式 = 指针沿同方向到中心的距离比。
- **全局下限**：`minFactor = 所有单元里最大的 (自身最小宽/起始宽, 自身最小高/起始高, 便签字号下限换算)` ——即"任何一个节点撞到底限之前，整组都不许再缩"，保证严格等比不被个别节点破坏。这是与现有单节点 resize 最大的语义差别，必须实现。
- **应用**（每个单元，全部基于手势开始快照，避免累计漂移）：
  - `x = 锚点x + (startX − 锚点x) × factor`，`y` 同理；
  - `w/h = round(startW/startH × factor)`，写显式值；
  - 便签：`fontSize × factor`（夹 10–48），`sizeMode='fixed'`；
  - 旧 smart-group：走 :27833-27878 已有的成员缩放重排路径（组内成员由组代管，不再单独出现在单元列表）；
  - workflow 组织框：成员 `x/y/w/h` 按同一 factor 缩放后 `fitWorkflowOrganizerBounds`（:14641）收紧框；
  - 单图/生成/多图网格/psd/3D 导演：只写 `w/h`（预览百分比贴合自动跟随）。
- **实时反馈**：mousemove 中逐单元直接改 DOM（`updateNodeElementDuringResize` :10377 + 位置 style），连线层 `scheduleConnectionLayerRefresh`，**不做整图 render**；用 `requestAnimationFrame` 节流合并多次 move。
- **收尾**：mouseup → `commitPendingUndo()` → `render()` → `scheduleSave()`；位移/尺寸无变化（<1px）按现有惯例丢弃撤销快照。

### 2.3 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `static/js/smart-canvas.js` | 新增：外框元素创建/定位/显隐（挂在 `syncSelectionUi` 与 viewport 更新处）、`selectionScaleState` 手势三件套（mousedown/mousemove/mouseup 接入现有 window 处理器 :27751/:28037 一带）、`scaleSelectionUnits(factor)` 纯函数（单元归并 + 全局 minFactor + 分类型应用） |
| `static/css/smart-canvas.css` | `#selectionScaleFrame` 与 8 手柄（4 角 + 4 边中点）样式（含暗色主题） |
| `static/smart-canvas.html` | 外框 DOM（或由 JS 动态创建，倾向前者便于审查）；`?v=` 缓存戳交服务端 `sync_static_html_versions` 处理，不手改 |
| `static/js/i18n/smart-canvas.js` | 手柄 `title` 词条（中英双语），跑 `node static/js/i18n/validate-i18n.js` |
| `tests/` | `test_smart_canvas_only.py` 补静态断言（手柄存在、无新持久化字段）；核心几何函数尽量写成可独立测试的纯函数 |
| `main.py` | **不改**。无新字段、无新路由、无服务端逻辑变化 |

## 3. 风险评估（重点）

| 风险 | 等级 | 说明与对策 |
| --- | --- | --- |
| 数据兼容/同步 | **低** | 只写已有的 `x/y/w/h/fontSize/sizeMode` 字段，旧画布照常加载；保存走现成 `node_fields` diff 链路，N 个节点 = N 条窄操作（与现有"多选拖动"完全同量级，服务端逐条幂等应用）。无新字段即无默认值兼容问题。 |
| 撤销/重做 | **低** | 一次手势一个 pendingUndo 快照，与现有 resize/drag 语义一致；批量内部改动不逐节点入栈。 |
| 几何保真 | **中** | ① 各类型最小尺寸、便签字号下限、网格缩略图 28px 底限、prompt/loop 内容驱动高度——若逐节点各自夹取会破坏"严格等比"，**对策：全局 minFactor，任何单元触底前整组停住**；② 逐节点 round 有 ±1px 舍入漂移，可接受；③ prompt/loop 缩放只改卡片不改内部字号，文字区域到内容最小高时卡片可能"缩不动"——属预期行为，需向用户说明。 |
| 交互冲突 | **中** | 新手柄与框选、节点拖动、端口连线、单节点手柄、排版工具栏、拆分/合并面板（刚合入的 f9244be 系列）都在同一区域；8 个手柄比 4 个多出边中点的热区，选区很扁时边杆会互相贴近（对策：手柄间距过小时临时隐藏边杆，只留角杆）。对策：手柄只在多选 ≥2 且空闲时出现、手势期间隐藏一切浮动 UI（复用现有 gating）；`z-index` 高于节点、低于弹窗；手柄 mousedown 必须 `stopPropagation` 防止误触发框选/拖动。回归测试清单见第 4 节。 |
| 重复缩放 | **中** | 组织框与其成员、smart-group 与其成员若同时出现在选中集，会被缩放两次。对策：单元归并强制复用 `layoutSelectionUnits` + `smartArrangeAtomicIds`，并加测试覆盖"框+成员混选"。 |
| 性能 | **低-中** | 拖动中逐单元直改 DOM 不整图 render（现有单节点 resize 同模式）；几百节点的大选区靠 rAF 节流。若实测仍卡，二期再做"拖动只画预览框、松手一次性应用"的降级方案。 |
| 多人协作 | **低** | 与现有缩放同语义：同字段最后写入生效。A 缩放、B 同时移动同一节点时最终态按服务端到达顺序收敛，不新增冲突面；缩放期间不发送中间态（只在 mouseup 批量提交），广播噪音反而比拖动小。 |
| 权限 | **低** | 只读 viewer 不显示手柄；即使伪造请求，operations 接口已有统一画布访问校验。 |
| 工作区状态 | 提示 | 本分支领先 origin 5 个提交且拆图/合并刚合入，实现前确认基于最新 HEAD；工作区现存 `?v=` 缓存戳与 `data/asset_library.json` 噪声按 AGENTS.md 不混入功能提交。 |

## 4. 实施步骤与验证

1. 纯函数先行：`scaleSelectionUnits(units, factor, anchor)` 与全局 `minFactor` 计算写成独立函数，浏览器外可验。
2. UI：外框 + 8 手柄（4 角 + 4 边中点）显隐/定位，接 `syncSelectionUi`、viewport 变换、render 后刷新。
3. 手势：接入现有 window mousemove/mouseup 分发链（与 `resizeState` 并列的独立状态，互斥检查齐全）。
4. 分类型应用规则（2.2 表）逐类实现，smart-group/组织框/便签复用既有路径。
5. 验证清单：
   - `node --check` 全量 JS；`node static/js/i18n/validate-i18n.js`；`git diff --check`；
   - `.\python\python.exe -m unittest discover -s tests -p 'test_smart_canvas_only.py'`；
   - 浏览器实测（用 3100 离屏端口起服，用完立即释放 3000——用户规则）：
     a. 框选 3 个图片节点拖角 → 等比、间距等比、连线跟随；
     a2. 拖边中点杆 → 对边中点钉住不动、整体严格等比；按住 Alt 拖任一手柄 → 选区中心不动、向四周等比扩张/收缩；
     b. 混选 便签+prompt+生成节点+组织框(含成员) → 各类型规则正确、无重复缩放；
     c. Ctrl+点选部分成员与框混选 → 归并行为正确；
     d. 缩小到某类节点触底 → 整组停住不破比例；
     e. 撤销一步整组还原；刷新重载尺寸位置保持；
     f. 双标签页：A 缩放 → B 实时收到；B 只读角色 → 无手柄；
     g. 回归：单选手柄、分组缩放、框选、排版工具栏、拆分/合并不受影响；
     h. 大缩略图/小缩略图（画布 zoom 0.2–2）下手柄屏幕尺寸恒定。
6. 独立 Git commit（不混运行时数据与缓存戳）。

## 5. 已确认决策（2026-11-19 用户批注 m00107）

1. **边中点手柄**：要做，共 8 个手柄；边杆同样是**等比缩放**，拖哪条边杆，其对边中点为不动的缩放中心（例：拖右边杆、左边杆中心不动）。不做单轴拉伸。
2. **Alt 中心缩放**：加。按住 Alt 时以选区外接框中心为锚。
3. **提示词/循环节点**：只缩放卡片，内部文字大小不变（与现状一致）。
4. **单选**：不显示外框，仍用节点自带手柄。
