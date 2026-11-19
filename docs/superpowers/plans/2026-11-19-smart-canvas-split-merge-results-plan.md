# 智能画布「多图结果 ⇄ 摊开单图」拆分/合并功能计划书

状态：**待用户确认，未动代码**。分支 `fix/canvas-node-field-deletion-sync`。日期 2026-11-19。

## 一、要解决的问题

现在一次生成的多张结果（如"28 个结果"）全部装在一个生成节点里，只能左右翻页逐张看。想做结果对比、把不同张分别连给其他节点、或批量取用时非常别扭——节点一次只显示一张，没法同时摊开。

目标：

1. **拆分**：把一个多图节点里的若干张图拆成独立节点，摊在画布上，可以各自连线、自由排版。
2. **可逆**：在画布上选中这些拆开的单图节点，能重新合并回一个可左右翻页的多图节点。
3. 拆分/合并全程走现有"节点操作同步"通道，刷新、重载、协作都不丢状态。

## 二、交互设计（两处按钮，均按你的要求定位）

### 1. 拆分入口 = 复用「N 个结果」徽章

现状：徽章是节点右上角的胶囊（`.generation-result-count`，`static/css/smart-canvas.css:713`），只在节点选中时淡入，目前 `pointer-events:none` 纯展示。

改法：

- 结果数 ≥2 且节点被选中时，徽章变为**可点击**：悬停高亮并显示提示"拆分结果"；点击弹出一个小面板（锚定在徽章下方）。
- 小面板内容：缩略图网格（该节点全部结果，当前显示的那张默认勾选）、三个快捷动作（全选 / 取消全选 / 只选当前）、主按钮「拆分所选 N 张」。
- 确认后执行拆分。结果数 <2 时徽章保持纯展示，不添乱。

### 2. 合并入口 = 排版工具栏右侧的独立按钮

现状：多选 ≥2 个节点时出现的浮动排版工具栏 `#layoutToolbar`（`static/smart-canvas.html:286`，胶囊形 `.layout-toolbar-floating`，min-height 52px）。

改法：

- 在工具栏胶囊**右外侧**并排放一个同高度的独立小胶囊按钮（带一条自己的边框/底色，视觉上与工具栏等高、有间隙、明显不坐在工具栏上）。
- 触发条件：当前选中的节点里有 **≥2 个"可合并"的单图节点**时才出现（判定见第三节）；不满足时按钮隐藏。
- 点击后把这些节点合并成一个多图节点：新节点放在原位置包围盒的中心；**原节点各自的连线全部迁移到新节点上**（去重）；保持图片顺序（按拆分时的序号，没有序号的按画布位置从左到右、从上到下）。
- 媒体类型不同的节点不能一起合（图片归图片、视频归视频）；选中混合类型时按钮**置灰 + 悬停提示原因**（已拍板，不自动只合多数类型）。

## 三、数据模型（保证可逆与兼容的核心）

不新增节点类型，完全复用现有结构：

- **拆分出的单图节点** = 普通上传型节点（`uploadNodeTypeForMediaKind()`，即 `smart-image-upload` 家族，`static/js/smart-canvas.js:1444`），`images` 只含一张。这样它天然支持连线、作为下游输入、拖拽排版，零特判。
- 每个拆分节点加两个新字段（都有默认值，旧画布不受影响）：
  - `splitFromNodeId`：来自哪个生成节点；
  - `splitIndex`：在原 `images[]` 里的序号（合并排序用）。
- **合并回生成节点**：目标节点恢复为 `smart-image-generation` 型（沿用原 `splitFromNodeId` 指向的原节点设置：prompt、runSettings、provider/model 等，保证还能继续调参重生成）；若原节点已删除，则以包围盒内第一个节点为基础建新生成节点，参数留空但保留图片。
- 所有字段变化都以细粒度 `node_fields` / `node_create` / `node_delete` / `connection_add|remove` 操作提交（同步机制现成：`handleCanvasOperationMessage`，`static/js/smart-canvas.js:8649`），符合 AGENTS.md"不回写整张画布"的铁律。
- 归一化：`normalizeSmartNode` 一带（`static/js/smart-canvas.js:1638` 附近）为新字段补默认值，保证旧版页面读到新字段不炸、新版读旧画布不缺字段。

## 四、边界规则（实现时逐条守住）

1. **一步撤销**：拆分、合并各包一次 `pushUndo()`（模式同 `deleteNode`，`static/js/smart-canvas.js:16769`），撤销后回到操作前的完整状态。
2. **部分拆分**：允许只拆子集。原生成节点保留剩余图片并正确收敛 `activeImageIndex`；被拆走的图从原节点 `images[]` 移除。
3. **任务进行中不可拆**：节点有 pending 任务（正在生成/排队）时，拆分按钮禁用并提示"生成完成后再拆分"。
4. **循环上下文节点（smart-loop 轮次产物）**：第一版**不参与**拆分/合并（拆散会破坏 roundOutputs 语义），徽章照常展示但点击提示暂不支持。
5. **历史分组节点**（`isHistoryGroupNode`）不提供拆分入口。
6. **删除安全**：拆分出的节点删除走统一 `deleteNode()` 清理，不影响原节点；`splitFromNodeId` 变成悬空引用时无害（只是合并时新建宿主）。
7. **并发/协作**：拆分与合并都是普通节点操作流，双人同时操作同一节点时按"同字段最后有效 + 删除墓碑优先"的既有规则收敛，无需新机制。
8. **导出/导入/复制**：工作流导出（`infinite-smart-canvas-workflow` v1）会把拆分节点当普通单图节点带上，`splitFrom*` 字段随剪贴板 ID 重映射逻辑一并 remap（`static/js/smart-canvas.js:9798` 已有同类 key 列表，加入这两个即可）。

## 五、涉及文件清单

| 文件 | 改动 |
| --- | --- |
| `static/js/smart-canvas.js` | 徽章点击绑定与拆分面板；`splitGenerationResults(nodeId, indexes)` 与 `mergeSelectedNodes()` 两个函数；归一化默认字段；工具栏按钮显隐逻辑（挨着 `updateLayoutToolbar`，`static/js/smart-canvas.js:11999` 区段） |
| `static/smart-canvas.html` | 拆分小面板模板；排版工具栏旁独立合并按钮（内联 SVG 图标，风格延续现有"基准线+色块"语言） |
| `static/css/smart-canvas.css` | 徽章可点态样式；拆分面板；`.merge-pill` 独立胶囊样式（与 `.layout-toolbar-floating` 等高、margin-left 间隙） |
| `static/js/i18n/smart-canvas.js` | 新词条（拆分/合并/提示语，中英成对） |
| `tests/test_smart_canvas_only.py` | 新增定向测试（见第七节） |

## 六、已拍板事项（2026-11-19 用户确认）

1. **拆分落点**：原节点右侧排成网格，间距沿用排版工具固定 40px 常量。
2. **合并范围**：任意单图节点均可合并，不限同一来源；无 `splitIndex` 时按画布位置排序。
3. **混合类型（图片+视频同时选中）**：合并按钮置灰 + 悬停说明原因，不自动挑多数类型。
4. 截图左侧绿色箭头是节点连线出入口的标注误读，与功能无关；翻页器保持现状不动。
5. **仍待确认**：拆出的单图节点合并回去后，这些单图节点本身消失（图收进宿主节点）——默认按此实现，如要保留副本请在开工前说一声。

## 七、验证计划（交付门槛）

```powershell
Get-ChildItem static\js -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
node static/js/i18n/validate-i18n.js
.\python\python.exe -m unittest tests\test_smart_canvas_only.py
git diff --check; git diff --cached --check
```

定向测试至少覆盖：拆分后节点数量与 images 归属、`splitFromNodeId/splitIndex` 写入、部分拆分后 `activeImageIndex` 收敛、合并后连线迁移去重、undo 一步还原、旧画布（无新字段）加载归一化不报错、pending 节点拒绝拆分。

浏览器实测（你在已登录会话验收，我用离屏端口冒烟后立刻释放，不占 3000）：双标签页验证拆分/合并实时同步；刷新重载后摊开状态不变。

## 八、实施顺序

1. 数据字段 + 归一化 + 拆分函数 + 单测（后端零改动，本功能纯前端 + 既有同步通道）。
2. 徽章交互与拆分面板。
3. 合并按钮与合并函数。
4. i18n、样式打磨、全量验证、提交（精确暂存，不带运行时噪声）。

## 九、修订（2026-11-19 验收后，用户拍板）：合并不再回原节点

- 删除"同源单宿主按 splitIndex 插回"规则；`splitFromNodeId/splitIndex` 保留仅作溯源记录，合并不使用。
- 可合并单元扩展为：单图上传节点 + 空闲的合并（生成）节点；正在生成的节点、历史分组、组织框不参与。单元数 ≥2 即出合并药丸。
- 宿主选择：按阅读顺序（先 y 后 x）——选中里有一个生成节点就并进它；多个生成节点以第一个为中心，其余生成节点的图全部并入；没有生成节点则在选区包围盒中心新建一个全新生成节点（`createMediaGenerationNode`，skipUndo/deferRender/deferSave），所有单图节点被吸收消失。
- 图片顺序：宿主自己的图保持原顺序在前，并进来的按阅读顺序追加；新建宿主时全部按阅读顺序。
- 混选图片+视频仍置灰；一步撤销、连线迁移去重、窄操作同步均不变；拆分侧不动。

## 十、修订二（2026-11-19 验收后，用户 m00426/m00461）：拆分自带生成信息 + 全拆删空壳

- 拆分时每张图按 `generationRunValuesFor(source, img)` 解析出真实那次运行的 prompt/settings/refs，写入拆出节点的 `runPrompt/runSettings/runInputRefs`（节点级），预览面板既有回退链即可显示来源、复制提示词、一键复刻，不再依赖台账与源节点存活。
- 预览查找顺序改为：台账 log → 图片自带（image.runPrompt/runSettings/runInputRefs）→ 节点最后运行。图片级优先于节点级，因为节点级只反映最后一次运行。
- 合并搬图用 `carryImage(node, img)`：先保留图自带的 run* 值，缺失时从来源节点级补 stamped 到图上，多源合并后每张图仍可逐图查来源与复刻。
- 全部拆完（source.images 为空）时，空生成节点随拆分一并删除：连带其历史分组节点、连线、workflowGroupId 与 inputNodeIds 引用清理；复用拆分已有的 pushUndo/render/scheduleSave，撤销仍是一步。拆出节点的 splitFromNodeId 悬空无害（信息已自带）。
- 已知边界（与现状一致，非本次新增）：台账 500 条上限只影响无自带信息的老图；跨画布复制粘贴不带走台账，但自带信息随节点 JSON 一起走。
