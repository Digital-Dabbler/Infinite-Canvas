# 便签富文本（PureRef Note 语义）实施计划

日期：2026-09-21
范围：`smart-note` 由纯文本 `<textarea>` 改造为受限 HTML + contenteditable 的富文本节点；拖手柄改框宽 / 修饰键整体缩放；选中文本浮条与节点浮出菜单；背景透明度；客户端与服务端同一套白名单清洗
设计依据：`docs/superpowers/specs/2026-09-21-smart-note-richtext-design.md`

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

1. 便签可**就地**富文本编辑（加粗/斜体/删除线/列表/待办/标题层级/链接/四档字号/对齐），没有编辑态切换、没有常驻工具栏。
2. **拖右下角手柄改框宽**并切成固定模式，内容向下增长、永不裁剪；**Alt/Ctrl + 拖动整体等比缩放**（框与字号一起）。
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
- `canvasOrganizerHtml()`（`:11918`）便签分支：`.smart-note-text` 由 `<textarea>` 改 `<div contenteditable="true">`，类名保留（CSS 与复制按钮的选择器不用改）。内容：有 `richText` 时 `appendChild(buildNoteFragment(node.richText))`，没有时 `document.createTextNode(node.text)`（旧便签原样显示）。
- 新增 `noteEditingIds = new Set()`（放在 `static/js/smart-canvas.js:110` 的 `promptTextEditingIds` 旁）；`render()` 收集 `editingNodeIds` 处（`:11959`）一并加入 `noteEditingIds`；`focus` 时 add（对标 `:12466`）、`blur` 时 delete（对标 `:12478`）。**没有这一步中文输入法会在合成中途被 DOM 重建打断。**
- 输入处理（`:13441-13449`）改为：DOM 即真源 → `node.richText = sanitizeNoteRichText(el.innerHTML)`、`node.text = el.innerText` → 仅当 `node.sizeMode === 'auto'` 调 `fitSmartNoteToText(node, el)`，否则调 `fitNoteHeightToContent(node, el)`（步骤 4）→ `renderMinimap()`、`scheduleConnectionLayerRefresh()`、`scheduleSave()`。同步继续走既有 `node_fields` 操作，协议不变。
- `beforeinput` 守卫：会超过 `NOTE_RICHTEXT_MAX` 的输入 `preventDefault()`，避免本地存下服务端下次必然 400 的内容。
- 复制按钮改读 `node.text`（不再依赖 `textarea.value`）。

### 4. 尺寸语义

- `fitSmartNoteToText()`（`:8637`）：仍只在 `auto` 模式调用；高度语义由「上限 720 的裁剪」改为「不小于内容高度」。
- 新增 `fitNoteHeightToContent(node, el)`：`node.h = max(node.h, el.scrollHeight + 内边距/工具条高度)`，fixed 模式用它，保证内容永不裁剪。
- 手柄 mousedown（`:13761-13768`）：保留 `resizeState.startFontSize`，新增 `resizeState.uniform = e.altKey || e.ctrlKey || e.metaKey`。
- 手柄 move（`:23605-23611`）：`uniform` 为真时沿用现有 `startFontSize * sqrt(widthRatio * heightRatio)` 并同步等比缩放 `w`/`h`；为假时由指针位移定 `node.w`、置 `node.sizeMode = 'fixed'`、**不动 `fontSize`**，高度交给 `fitNoteHeightToContent()`。
- 节点最小尺寸沿用 `:23545-23546`（160×110）。

### 5. 浮条、节点菜单、样式与文案

- **选中文本浮条**：新增 `#noteFormatBar`（`position: fixed`）。便签内 `selectionchange` 时若选区非折叠且落在同一便签内就显示，按钮＝加粗、四档字号（`data-fs`）、对齐（`data-align`）、无序列表、有序列表、待办、链接。加粗/列表/对齐走 `document.execCommand`（先 `document.execCommand('styleWithCSS', false, false)` 避免生成 `style`，再由步骤 1 的归一表把 `<b>/<i>/<strike>` 映射到 `strong/em/s`）；链接、待办勾选、四档字号用 Range 手写包装/属性设置（`execCommand` 做不到自定义属性）。失焦或选区折叠即隐藏。
- **节点浮出菜单**：选中便签时在右上角渲染 `#noteNodeMenu`，复用 `organizerColorButtons(node)` 的 6 色与 `smartTextCopyButtonHtml(...)`，新增 `bgAlpha` 滑杆、`auto/fixed` 切换、删除。`canvasOrganizerHtml()`（`:11918`）里的常驻工具条随之移除。
- **CSS** `static/css/smart-canvas.css:2221-2238`：`.smart-note-node` 改弱边框（hover 才显形）、更小圆角、去重阴影，背景由 `--note-bg-alpha` 与 `--organizer-color` 经 `color-mix` 计算，`bgAlpha = 0` 时边框与阴影一并淡出；`.smart-note-text` 不再靠 `overflow: hidden` 兜底（高度由步骤 4 保证）；新增浮条、浮出菜单、`data-fs` 四档、`data-align` 三档、`li[data-checked]` 勾选样式。
- **i18n** `static/js/i18n/smart-canvas.js`：浮条、菜单、尺寸模式、透明度、链接等新文案补 zh/en 双语，`node static/js/i18n/validate-i18n.js` 必须保持通过。

### 6. 粘贴

便签内 `paste`：`preventDefault()`；取 `text/html`（无则 `text/plain`）；`buildNoteFragment()` 剪枝；用 Range 插入到光标处。图片/文件粘贴直接忽略（对标提示词节点 `static/js/smart-canvas.js:24556` 的做法）。

### 7. 测试

- 新增 `tests/fixtures/smart-note-richtext-cases.json`：脏输入 → 期望输出的共享语料（`<script>`、`on*`、`style`、`javascript:`/`data:` href、`<iframe>`、`<img>`、表格、字体标签、`data-fs="9"`、`data-align="justify"`、超长输入）。
- 新增 `tests/test_smart_note_richtext_sanitizer.py`：直接测 `main.py` 的 `sanitize_note_richtext()`，逐条比对语料；另测 `validate_canvas_node_fields()` 的 `smart-note` 特例（超长 400、`sizeMode` 非法、`bgAlpha` 归一、正常加粗/列表/待办/链接保留）。
- 新增 `tests/test_smart_note_richtext_client.py`：`node -e` 跑 `sanitizeNoteRichText`，逐条比对**同一份语料**（客户端与服务端不许漂移）；另加源码契约断言：便签渲染路径中不出现把 `richText` 直接赋给 `innerHTML`。
- 新增 `tests/test_smart_note_size_semantics.py`（同一 node 范式）：普通拖动改 `w`、置 `sizeMode='fixed'`、不动 `fontSize`；修饰键拖动按同一比例缩放 `w`/`h`/`fontSize`；fixed 便签不调用 `fitSmartNoteToText`；内容高于 720 时高度容纳内容。
- 更新既有测试：实施时先 `grep -rn "smart-note-text" tests/` 找断言便签 DOM 的用例，随 `textarea → div` 调整。

### 8. 验收

- `node --check static/js/smart-canvas.js`、`node --check static/js/i18n/smart-canvas.js`、`node static/js/i18n/validate-i18n.js`。
- `.\python\python.exe -c "import ast, pathlib; ast.parse(pathlib.Path('main.py').read_text(encoding='utf-8')); print('OK')"`（只 parse 不 import，避免启动写入）。
- `git diff --check`。
- 定向单测：`.\python\python.exe -m unittest tests.test_smart_note_richtext_sanitizer tests.test_smart_note_richtext_client tests.test_smart_note_size_semantics tests.test_smart_canvas_outline tests.test_workflow_group_text_run tests.test_smart_canvas_only`。
  - 环境注意：本机沙箱拒绝在 `tempfile.mkdtemp()` 生成的目录下建子目录，`tests/test_canvas_field_deletion.py`、`tests/test_static_cache_stamp.py` 的 `setUp` 会 `PermissionError: [WinError 5]`；这是环境限制而非代码问题。必要时用临时 runner 把 `tempfile.mkdtemp` 换成 `os.mkdir(name, 0o777)`（本次已用它验证过 44 tests OK）。
- 浏览器验收由用户完成（本机沙箱禁止 Chrome 命名管道 IPC，无法无头代验）：中文输入法连续输入不中断；从网页粘贴保留加粗/列表/链接、丢掉字体与图片；手动拖框后继续打字宽度不变、高度向下增长；透明度拖到 0 变纯文字；第二个浏览器会话看到同样的富文本。

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
