# 便签富文本（PureRef Note 语义）实施计划

日期：2026-09-21
范围：`smart-note` 由纯文本 `<textarea>` 改造为受限 HTML + contenteditable 的富文本节点；拖手柄改框宽 / 修饰键整体缩放；选中文本浮条与节点浮出菜单；背景透明度；客户端与服务端同一套白名单清洗
设计依据：`docs/superpowers/specs/2026-09-21-smart-note-richtext-design.md`
状态：步骤 1–4 已提交 `cd85a35`；步骤 5–7 已提交 `b838669`（含段落白名单 `p`/`div` 修复与菜单双类选择器覆盖）；步骤 8 的三条静态验收命令已跑通，浏览器交互验收待人工完成。

## 背景

画布便签目前是一个卡片里的纯文本 `<textarea>`，与「PureRef Note」模型（用户可定框、可换行、富文本、不编辑时隐入背景）有五处直接冲突：

1. **拖手柄改的是字号，不是框**：`static/js/smart-canvas.js:23605-23611` 由拖动位移算出 `node.fontSize = clamp(round(startFontSize * sqrt(widthRatio * heightRatio) * 10) / 10, 10, 48)`。而且就算这样也留不住——`fitSmartNoteToText()`（`static/js/smart-canvas.js:8637`）在每次输入时重写 `node.w`/`node.h`（`static/js/smart-canvas.js:13441-13449`），下一键入就把便签按 `targetWidth = clamp(longestLine + 28, 160, 640)` 重新量宽。
2. **文本是扁平的**：`node.text` 一个字符串，没有加粗、标题、列表、待办、对齐、链接。全仓也没有富文本编辑器与 HTML 清洗器——`execCommand` 的唯一用途是 `document.execCommand('copy')`（`static/js/smart-canvas.js:479`、`:500`、`static/js/admin-dashboard-v2.js:465`）。
3. **长便签被静默裁掉**：`fitSmartNoteToText()`（`static/js/smart-canvas.js:8637`）的 `targetHeight` 上限 720，而 `.smart-note-text` 是 `overflow: hidden` + `resize: none`（`static/css/smart-canvas.css:2221-2238`），超出部分用户看不到也够不着。
4. **粘贴无白名单**：剪贴板 HTML 会原样落地。
5. **常驻工具条占画布**：`canvasOrganizerHtml()`（`static/js/smart-canvas.js:11918`）无条件渲染 `organizerColorButtons(node)` + 复制 + 删除。

画布是共享的（`apply_canvas_node_operation()`，`main.py:7259`，字段级合并见 `main.py:7300-7315`），节点字段属于攻击者可控输入，因此**渲染路径绝不能吃存储里的 HTML**。

已确认的设计决策（详见设计文档）：方案一「受限 HTML 子集 + contenteditable」；`text` 继续写纯文本镜像；字号是逐段落四档而非节点级；粘贴保留基础格式；常驻工具条改为选中后浮出；背景可全透明。

## 目标

1. 便签可**就地**富文本编辑（加粗/斜体/删除线/列表/待办/标题层级/链接/四档字号/对齐）：**双击正文才进编辑态**，失焦/Esc/点到便签外即收笔；非编辑态没有常驻工具栏，正文不再吃键盘，也不参与选区。
2. **非编辑态＝普通图元**：按正文就能拖动，拖右下角手柄**整体等比缩放**（框与字号同一因子，文字跟着缩放）；编辑态里普通拖动仍是「只改框宽」（固定框、内容向下增长、永不裁剪），修饰键才等比。任何一次拖手柄都切成固定模式。
3. 6 色与背景透明度收进「选中便签时浮出的菜单」，`bgAlpha = 0` 即纯文字。
4. 任何来源（本地输入、协作对端、服务端回灌）的富文本都走同一条白名单路径，渲染侧不存在 `innerHTML = node.richText`。
5. 旧便签零迁移零破坏：没有 `richText` 时按 `text` 渲染；旧前端仍能通过 `text` 读写。

## 实施步骤

### 1. 客户端白名单与渲染器

全部放在 `static/js/smart-canvas.js` 内（不新增独立 JS 文件：静态缓存戳 `main.py:3206` 只枚举既有资产，新文件不会被戳覆盖，容易造成刷新后仍是旧脚本）。

- 新增常量 `NOTE_RICHTEXT_MAX = 20000`；标签白名单 `strong, em, s, ul, ol, li, h1, h2, h3, br, a`；属性白名单 `a[href]`、`li[data-checked]`、`data-fs`、`data-align`。
- 新增 `sanitizeNoteRichText(html)`：用 `<template>` 解析后递归剪枝——非白名单标签解包为文本（内容保留、标签消失），白名单标签只留白名单属性，`style`/`class`/`on*` 一律删除；`href` 只接受 `http:`/`https:`/`mailto:`/站内相对路径，`javascript:`、`data:` 丢弃；`data-fs` 只接受 `1`–`4`，`data-align` 只接受 `left|center|right`，`li[data-checked]` 归一为 `"true"|"false"`。顺手把编辑器可能产生的 `<b>/<i>/<strike>/<font>` 归一到白名单标签（见步骤 5）。
- 新增 `buildNoteFragment(html)`：先 `sanitizeNoteRichText()` 再建 `DocumentFragment`。**这是 stored data → canvas 的唯一入口**。
- 返回字符串而不是 DOM 的那一支（`sanitizeNoteRichText`）必须可在 `node -e` 里单独跑，测试沿用 `tests/test_workflow_group_text_run.py` 的 `extract_function` 范式。

### 2. 服务端清洗与字段校验

`main.py`，与 `validate_canvas_node_fields()`（`main.py:7193`）同区：

- 新增 `sanitize_note_richtext(html) -> str`：标准库 `html.parser.HTMLParser` 子类，实现与客户端同一份白名单（解包非法标签、只留白名单属性、丢弃 `style`/`on*`、校验 `href` 方案与 `data-*` 值域、转义文本与属性值）。不引入 `bleach`/`DOMPurify` 等依赖（离线可用 + 无构建步骤）。
- 在 `validate_canvas_node_fields()` 内按 `type == 'smart-note'` 加特例，与既有 `directorScene`/`directorThumb` 特例并列、沿用同一种拒绝方式：`richText` 非字符串或超 `NOTE_RICHTEXT_MAX` 字符 → 400；否则写回清洗结果。`sizeMode` 只接受 `'auto'|'fixed'`；`bgAlpha` 归一为 `max(0, min(100, int(v)))`。
- 服务端不新增其他知识：`text` 照旧不校验，白名单之外仍由客户端拥有 schema（`main.py:7193` 的既有约定不变）。

### 3. 数据字段与渲染路径

`static/js/smart-canvas.js`：

- `createSmartNote()`（`:8733`）新节点增加 `sizeMode: 'auto'`、`bgAlpha: 20`；不预写 `richText`（首次编辑时才生成，等价于对旧数据格式保持沉默）。
- `canvasOrganizerHtml()`（`:11918`）便签分支：`.smart-note-text` 由 `<textarea>` 改 `<div class="smart-note-text">`，类名保留（CSS 与复制按钮的选择器不用改）。`contenteditable` 由 `noteEditingIds.has(node.id)` 决定（`"true"`/`"false"`，`role="textbox" aria-multiline="true"` 只在编辑态加）；内容：有 `richText` 时 `appendChild(buildNoteFragment(node.richText))`，没有时 `document.createTextNode(node.text)`（旧便签原样显示）。
- 编辑态进入/退出：`beginSmartNoteEdit(node, el, event)`（`noteEditingIds.add` + 置 `contenteditable="true"` + focus + `placeNoteCaretFromPoint()` 把光标落在双击处）由**双击的第二下**（`beginNodeDrag` 里 `e.detail >= 2` 的分支，比 `dblclick` 事件可靠：两次点击之间选中态重渲染会把元素换掉）、`el.ondblclick`、以及 `createSmartNote()` 调用；退出＝`blur`（补回 `contenteditable="false"` 并摘掉 role，因为失焦不触发 render）、`Escape`、以及 capture 阶段 `document` mousedown 里「按在便签外就 `editor.blur()`」（画布的平移/框选会 `preventDefault()`，浏览器不会自己移焦点）。拖动守卫相应改成只跳过 `.smart-note-text[contenteditable="true"]`。
- 新增 `noteEditingIds = new Set()`（放在 `static/js/smart-canvas.js:110` 的 `promptTextEditingIds` 旁）；`render()` 收集 `editingNodeIds` 处（`:11959`）一并加入 `noteEditingIds`；`focus` 时 add（对标 `:12466`）、`blur` 时 delete（对标 `:12478`）。**没有这一步中文输入法会在合成中途被 DOM 重建打断。**
- 输入处理（`:13441-13449`）改为：DOM 即真源 → `node.richText = sanitizeNoteRichText(el.innerHTML)`、`node.text = el.innerText` → 仅当 `node.sizeMode === 'auto'` 调 `fitSmartNoteToText(node, el)`，否则调 `fitNoteHeightToContent(node, el)`（步骤 4）→ `renderMinimap()`、`scheduleConnectionLayerRefresh()`、`scheduleSave()`。同步继续走既有 `node_fields` 操作，协议不变。
- `beforeinput` 守卫：会超过 `NOTE_RICHTEXT_MAX` 的输入 `preventDefault()`，避免本地存下服务端下次必然 400 的内容。
- 复制按钮改读 `node.text`（不再依赖 `textarea.value`）。

### 4. 尺寸语义

- `fitSmartNoteToText()`（`:8637`）：仍只在 `auto` 模式调用；高度语义由「上限 720 的裁剪」改为「不小于内容高度」，并且**优先量屏幕上那份 DOM**（`measureNoteContentHeight()`：临时置 `height: auto` 读 `editor.scrollHeight` 再还原，加 24px 内边距），canvas `measureText` 估算只当无 DOM 时的兜底。下限 `NOTE_MIN_HEIGHT = 44`（一行正文＋内边距）。
- 新增 `fitNoteHeightToContent(node, el)`：内容溢出时 `node.h = max(NOTE_MIN_HEIGHT, node.h + 溢出量)`，fixed 模式用它，保证内容永不裁剪、也不会留一截空白。
- 手柄 mousedown（`:13761-13768`）：保留 `resizeState.startFontSize`，`resizeState.uniform = !noteEditingIds.has(node.id) || e.altKey || e.ctrlKey || e.metaKey`——**非编辑态一律等比**（便签当图片用），编辑态里普通拖动才是「只改框宽」。
- 手柄 move（`:23605-23611`）：`uniform` 时用 `startFontSize * sqrt(widthRatio * heightRatio)` 并同步等比缩放 `w`/`h`/`fontSize`，下限夹在**因子**上（`minFactor = max(NOTE_MIN_WIDTH/startW, NOTE_MIN_HEIGHT/startH, 10/startFontSize)`）而不是分别夹宽高，缩到最小也不破比例；非 uniform 时由指针位移定 `node.w`，高度交给 `fitNoteHeightToContent()`。两条分支都置 `node.sizeMode = 'fixed'`。
- 节点最小尺寸：便签改为 160×44（`NOTE_MIN_WIDTH`/`NOTE_MIN_HEIGHT`，旧的 160×110 是「框里还挂着工具栏」时代的值，正是「贴不到文字」的来源），其他节点类型不变。

### 5. 浮条、节点菜单、样式与文案

- **选中文本浮条**：新增 `#noteFormatBar`（`position: fixed`）。便签内 `selectionchange` 时若选区非折叠且落在同一便签内就显示，按钮＝加粗、四档字号（`data-fs`）、对齐（`data-align`）、无序列表、有序列表、待办、链接。加粗/列表/对齐走 `document.execCommand`（先 `document.execCommand('styleWithCSS', false, false)` 避免生成 `style`，再由步骤 1 的归一表把 `<b>/<i>/<strike>` 映射到 `strong/em/s`）；链接、待办勾选、四档字号用 Range 手写包装/属性设置（`execCommand` 做不到自定义属性）。失焦或选区折叠即隐藏。
- **节点浮出菜单**：选中便签时在右上角渲染 `#noteNodeMenu`，复用 `organizerColorButtons(node)` 的 6 色与 `smartTextCopyButtonHtml(...)`，新增 `bgAlpha` 滑杆、`auto/fixed` 切换、删除。`canvasOrganizerHtml()`（`:11918`）里的常驻工具条随之移除。
- **CSS** `static/css/smart-canvas.css:2221-2238`：`.smart-note-node` 改弱边框（hover 才显形）、更小圆角、去重阴影，背景由 `--note-bg-alpha` 与 `--organizer-color` 经 `color-mix` 计算，`bgAlpha = 0` 时边框与阴影一并淡出；`.smart-note-text` 不再靠 `overflow: hidden` 兜底（高度由步骤 4 保证）；新增浮条、浮出菜单、`data-fs` 四档、`data-align` 三档、`li[data-checked]` 勾选样式。
- **i18n** `static/js/i18n/smart-canvas.js`：浮条、菜单、尺寸模式、透明度、链接等新文案补 zh/en 双语，`node static/js/i18n/validate-i18n.js` 必须保持通过。

> 与原计划的偏差（实现后回写）：①浮条不是 `#noteFormatBar`，而是懒创建的单例 `div.smart-note-format-bar`（`data-note-format-bar="1"`）append 到 `document.body`，每帧按 Selection 的 client rect 重算位置（`updateNoteFormatBar()` 自递归 rAF）；②节点菜单不是新 id `#noteNodeMenu`，而是复用 `.smart-node-floating-menu` 的 `smartNoteFloatingMenuHtml()`（`class="smart-node-floating-menu smart-note-menu"`），于是「选中才浮出、拖拽/多选/缩放时隐藏、反向抵消画布缩放」全部自动成立；③色块与复制按钮必须用双类选择器（`.smart-note-menu .smart-note-menu-colors .organizer-color`、`.smart-note-menu .smart-text-copy-btn`）压过 `.smart-node-floating-menu button` 的通用尺寸，否则色块被撑成透明药丸；④字号/对齐/清单都落在 enclosing block 的 `data-fs`/`data-align` 上，不是节点基准 `fontSize`。
>
> 偏差（用户看完初版后的返工，2026-09-21 二次落盘）：⑤**初版把 `contenteditable="true"` 写死在卡片 HTML 里**，便签因此永远处于编辑态——点空白也不失焦（画布手势 `preventDefault` 掉了 mousedown，浏览器没有移焦点的机会），既不能像图片那样拖动，也没法整体缩放。改成「双击才进编辑态」后，非编辑态的正文就是一块普通图元：`cursor:move`、可拖、手柄等比缩放（文字同因子），编辑态才恢复 `cursor:text` + 选中浮条 + 焦点描边。⑥非编辑态拖手柄从「只改框宽」改成「整体等比」，编辑态保留宽向拖动；两条分支都置 `sizeMode = 'fixed'`。⑦高度从「估算＋110px 固定下限（外加旧的 39px 工具条高度）」改成「实测渲染后的正文高度＋44px 下限」，这才真正贴合文字。

### 6. 粘贴

便签内 `paste`：`text/html` 非空时 `preventDefault()`，过一遍客户端白名单（`sanitizeNoteRichText()`）后用 `document.execCommand('insertHTML')` 插入到光标处，再就地 `pruneNoteEditorDom(editor)` 兜住解析器的重构；`text/html` 为空则回退插入转义后的 `text/plain`。洗完为空就什么都不插（图片、表格、字体标签粘贴后不残留）。图片/文件粘贴直接忽略（对标提示词节点 `static/js/smart-canvas.js:24556` 的做法）。

> 与原计划的偏差：没用 `buildNoteFragment()` + Range 手写插入，而是走 `execCommand('insertHTML')`——只有走浏览器的编辑命令才能留在撤销栈上（Ctrl+Z 能撤回这次粘贴），代价是插入后要多 prune 一次。见 spec §2 粘贴一节。

### 7. 测试

- 新增 `tests/fixtures/smart-note-richtext-cases.json`：脏输入 → 期望输出的共享语料（`<script>`、`on*`、`style`、`javascript:`/`data:` href、`<iframe>`、`<img>`、表格、字体标签、`data-fs="9"`、`data-align="justify"`；**已落地 44 条**，另含段落 `p`/`div` 边界、实体只解一次、注释/CDATA/doctype 丢弃等）。
- **实际落地为一个文件** `tests/test_smart_note_richtext.py`（**35 个用例**，四个类）：`SmartNoteRichTextCaseTests`（客户端与服务端各跑同一份语料 + 两侧互等）、`SmartNoteNodeFieldTests`（`validate_canvas_node_fields()` 的 `smart-note` 特例：超长 400、`sizeMode` 只认两个字面量、`bgAlpha` 夹取、非便签节点忽略便签字段）、`SmartNoteRenderContractTests`（渲染唯一入口、编辑期 DOM 保活、双击才进编辑态、新建便签直接可打字、失焦/点便签外收笔、自适应按实测正文贴合且不再有 110/工具条常量、非编辑态等比缩放且下限夹在因子上、正文拖动/编辑区不拖动、持久态与编辑态 CSS 分离）、`SmartNoteFormattingContractTests`（浮条保选区、命令产出语义标签、默认块属性被删、勾选与链接形状、四档字号、`selectionchange`、菜单替换常驻工具条、粘贴剪枝、透明度只改自定义属性、CSS 分层淡出、两侧段落白名单字面量、菜单双类覆盖、`richText` 绝不进 `innerHTML`、i18n 键全有 zh/en）。
  > 与原计划的偏差：原计划拆成 `test_smart_note_richtext_sanitizer.py` / `test_smart_note_richtext_client.py` / `test_smart_note_size_semantics.py` 三个文件，实施时合并成一个——三者共用同一套 `extract_function()`/`node -e` 骨架，拆开只会三份重复 harness。
- 更新既有测试：`tests/test_smart_canvas_outline.py` 的 `test_note_edits_do_not_rebuild_the_outline` 改断言共享写入口 `syncNoteFromEditor(nodeForControls, noteInput, el);`（仍保留 `assertNotIn("renderSmartOutline", …)`）。

### 8. 验收

- `node --check static/js/smart-canvas.js`、`node --check static/js/i18n/smart-canvas.js`、`node static/js/i18n/validate-i18n.js`。
- `.\python\python.exe -c "import ast, pathlib; ast.parse(pathlib.Path('main.py').read_text(encoding='utf-8')); print('OK')"`（只 parse 不 import，避免启动写入）。
- `git diff --check`。
- 定向单测：`.\python\python.exe -m unittest tests.test_smart_note_richtext_sanitizer tests.test_smart_note_richtext_client tests.test_smart_note_size_semantics tests.test_smart_canvas_outline tests.test_workflow_group_text_run tests.test_smart_canvas_only`。
  - 环境注意：本机沙箱拒绝在 `tempfile.mkdtemp()` 生成的目录下建子目录，`tests/test_canvas_field_deletion.py`、`tests/test_static_cache_stamp.py` 的 `setUp` 会 `PermissionError: [WinError 5]`；这是环境限制而非代码问题。必要时用临时 runner 把 `tempfile.mkdtemp` 换成 `os.mkdir(name, 0o777)`（本次已用它验证过 44 tests OK）。
- 浏览器验收由用户完成（本机沙箱禁止 Chrome 命名管道 IPC，无法无头代验）：**双击正文才进编辑态、单击正文只拖动节点、点画布空白立刻收笔、Esc 收笔**；**非编辑态拖手柄整体等比缩放（文字跟着放大缩小、比例不歪）**；中文输入法连续输入不中断；从网页粘贴保留加粗/列表/链接、丢掉字体与图片；手动拖框后继续打字宽度不变、高度向下增长；透明度拖到 0 变纯文字；第二个浏览器会话看到同样的富文本。

## 不在本轮范围内

- 便签父子挂载与跟随（挂到图片/节点上、父项移动则跟随）。
- 折叠/隐藏便签。
- 待办勾选状态同步到画布目录。
- 同一便签的实时光标协同编辑；沿用现有字段级、后写覆盖的 `node_fields` 同步模型。
- 便签内图片、表格、字体族、文字颜色。
- Markdown 导入导出与服务端渲染便签内容。

## 回滚边界

主要涉及：

- `static/js/smart-canvas.js`（白名单/渲染器/输入/尺寸/菜单/粘贴）
- `static/css/smart-canvas.css`
- `static/js/i18n/smart-canvas.js`
- `main.py`（`sanitize_note_richtext()` + `validate_canvas_node_fields()` 的 `smart-note` 特例）
- `tests/fixtures/smart-note-richtext-cases.json`、`tests/test_smart_note_richtext_sanitizer.py`、`tests/test_smart_note_richtext_client.py`、`tests/test_smart_note_size_semantics.py`
- 本计划文件

可分两段回滚：

1. **先回滚前端**（`smart-canvas.js`/`css`/i18n）：旧前端忽略 `richText`、继续用 `text`，功能退回纯文本便签，已写入的 `richText` 留在数据里不丢，用户不会有数据损失，只是富文本暂时显示为纯文本。
2. **再回滚服务端**（`main.py` 的特例）：前端仍在时只是失去服务端那道白名单，仍受客户端剪枝保护。

顺序不能反：若先回滚服务端、且用户随后用新前端编辑，`richText` 会未经服务端清洗就落库（客户端仍会剪枝，但少了一层防线）。
