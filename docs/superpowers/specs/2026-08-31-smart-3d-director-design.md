# 智能画布 3D 导演台节点设计

## 目标

参考 TE MAN 3D导演台（ComfyUI 插件），在 Infinite Canvas 智能画布内新增一个 **`smart-3d-director` 节点类型**：节点承载独立 3D 场景状态，点击节点打开 3D 导演台面板，在 3D 空间摆放人物、道具与机位，产出构图参考图（截图）送入后续生图/生视频流程。

3D 渲染逻辑完全隔离在 `director3d.html`（iframe）中，不写入 `smart-canvas.js` 主逻辑；后端 `main.py` 零改动。

## 用户体验

1. 用户在智能画布左侧工具导轨点击「3D导演台」，或空白处右键 → 创建菜单选「3D导演台」，画布上生成一个 `smart-3d-director` 节点。
2. 双击节点或点击节点上的「打开」按钮，打开画布内 iframe 悬浮 3D 面板。
3. 在面板内摆放人物（程序化素体 / GLB 导入）、道具、机位，选用姿势预设与关节滑条、机位预设与 FOV、取景比例，右侧实时预览当前机位画面。
4. 退出面板后，最终场景状态与构图缩略图写回节点，节点显示构图缩略图。
5. 可再次进入面板继续编辑；每个节点独立，各自保存各自场景，随画布 JSON 持久化，刷新/重开画布后恢复。
6. 节点提供「导出截图」：下载 / 保存到资产库 / 发送到智能画布（生成独立图片节点）/ 导出构图提示词。节点不参与连线拓扑。

## 架构与文件清单

```
┌─ smart-canvas.html ─────────────────────────────────┐
│  .rh-tool-rail  [3D导演台]   ← 新增按钮             │
│  #createMenu    [3D导演台]   ← 新增卡片             │
│  节点 smart-3d-director  ← 新增节点类型              │
│  ┌─ iframe 悬浮面板 ─────────────────────────┐      │
│  │  director3d.html?embed=canvas&node_id=.. │      │
│  │  3D 场景编辑器（Three.js，独立隔离）       │      │
│  └───────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────┘
     ▲ postMessage 双向同步（director3d:*）
```

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `static/director3d.html` | 新建 | 3D 面板骨架（importmap + Three + i18n） |
| `static/js/director3d.js` | 新建 | 3D 编辑器核心（DirectorPanel 逻辑，独立重写） |
| `static/css/director3d.css` | 新建 | 编辑器样式 |
| `static/js/i18n/director3d.js` | 新建 | `director3d.*` 中英文词条 |
| `static/vendor/js/three-addons/OrbitControls.js` / `TransformControls.js` / `GLTFLoader.js` | 新建 | 从 three 0.160 匹配版拷入（离线/局域网要求） |
| `static/smart-canvas.html` | 修改 | 工具导轨按钮、创建菜单卡片、面板容器 |
| `static/js/smart-canvas.js` | 修改 | 新增节点类型：创建/渲染/normalize/撤销/复制粘贴/删除/导入导出/保存、`message` 接收分支、`createNodeFromMenu` 分支 |
| `static/js/smart-canvas-shell.js` | 修改 | `surfaces` 注册表增加 `director3d` surface |

后端 `main.py` 不改：截图落盘复用 `POST /api/ai/upload-base64`（返回 `/assets/...` URL），保存资产复用 `POST /api/asset-library/items`。

## 节点数据模型

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `type` | string | `'smart-3d-director'` | 节点类型 |
| `title` | string | `'3D导演台'` | 节点标题 |
| `directorScene` | string | `'{}'` | 场景 JSON（版本、实体、机位、取景比例、骨架模式等） |
| `directorPrompt` | string | 示例提示词 | 构图提示词 |
| `directorThumb` | string | `''` | 构图缩略图（~240px JPEG dataURL，退出/同步时刷新） |

- 独立节点：不参与生成拓扑、不画连线；但可重进编辑（区别于一次性工具栏结果节点）。
- 持久化随 `data/canvases/*.json`；结构更新须同步检查：归一化（旧节点缺字段给默认值）、撤销 `pushUndo`、复制粘贴（深拷贝 `directorScene` 保持独立）、删除、工作流导入导出、保存。
- 缩略图用 dataURL 自包含，控制在 ~240px 防止画布 JSON 膨胀。

## 3D 编辑器核心

- **实体系统**：`character`（人物）、`prop`（道具）、`camera`（机位），统一 `id` + Three.Group 管理，右侧 Inspector 面板编辑选中实体。
- **交互**：OrbitControls（导演视角环绕/平移/缩放）+ TransformControls（选中实体位移/旋转/缩放）+ Raycaster 射线拾取 + 地面网格。
- **人物**：程序化胶囊素体（自绘，各部位挂嵌套 Group，`mixamorig:*` 命名）与 GLB 导入（GLTFLoader 扫描骨骼构建 boneMap）共用同一套关节/姿势系统。
- **关节滑条**：身体、躯干、头、肩（左/右：前举/外展/扭转）、肘（左/右）、腕（左/右）、髋（左/右：抬腿/外展）、膝（左/右）、踝（左/右）九组约 27 个，各带角度范围与骨骼名映射。
- **17 个姿势预设**：站立 / T型 / 行走 / 跑步 / 叉腰 / 鞠躬 / 思考 / 格斗 / 踢球 / 投掷 / 招手 / 够物 / 坐下 / 单膝跪 / 双膝跪 / 俯卧 / 躺下。
- **道具**：方块 / 球体 / 圆柱三种基础几何体，可拖动/旋转/缩放/命名/换色。
- **机位**：机位实体（PerspectiveCamera + 视锥可视化）可摆放并「对准主体」；15 个机位预设（正面中景/特写/全景、侧面跟拍、侧面近景、背面中景、俯拍全景、45°俯拍、低角度仰拍、低角度广角、过肩镜头左/右、鸟瞰、荷兰角 + 当前视角）；FOV 20–90° 可调；导演视角 ↔ 机位视角切换；右侧离屏 WebGLRenderer 实时缩略预览。
- **取景比例**：16:9 / 9:16 / 1:1 / 4:3 / 3:4 / 21:9 / 自由，截图按此比例裁切。
- **截图**：`captureDataUrl()` 离屏渲染当前机位/导演视角 → 按取景比例裁切 → PNG dataURL。

## 画布集成与消息协议

入口：工具导轨按钮（`smart-canvas-shell.js` `surfaces` 注册表 + `data-rh-action="director3d"`）与右键创建菜单（`createNodeFromMenu` 增加 `director3d` 分支）都只负责**创建节点**；双击节点或点击节点「打开」按钮才打开面板。

面板：iframe 悬浮面板（可拖动/缩放），src 为 `director3d.html?embed=canvas&canvas_id=<id>&node_id=<nodeId>`。

消息（postMessage，同源校验）：

| 方向 | type | 载荷 |
| --- | --- | --- |
| 面板 ready → 父 | `director3d:ready` | `{nodeId}` |
| 父 → 面板 | `director3d:load` | `{nodeId, scene, prompt, theme, lang}` |
| 面板 → 父（节流 800ms + 退出强制） | `director3d:sync-scene` | `{scene, prompt, thumb}` → 更新节点字段 + `scheduleSave()` |
| 面板 → 父（导出截图） | `director3d:insert-asset` | `{url, name, kind}` → `createImageNodeAt(center,[item],{select:true})` + pushUndo + render + scheduleSave |
| 面板 → 父（导出提示词） | `director3d:prompt` | `{text}` → 新建 prompt 节点并写入 text |
| 面板 → 父 | `director3d:close` | 关闭面板、回写按钮态 |

## 输出链路

```
captureDataUrl() → PNG dataURL
  ├─ 下载            a[download] 直接保存
  ├─ 发送到画布       POST /api/ai/upload-base64 → /assets URL → postMessage insert-asset → 父画布建图片节点
  ├─ 保存到资产库     POST /api/ai/upload-base64 → /assets URL → POST /api/asset-library/items {library_id, category_id, url, name}
  └─ 导出构图提示词    buildPrompt() 生成中文构图描述 → 复制到剪贴板 或 postMessage director3d:prompt 建 prompt 节点
```

- 保存资产库时面板内提供「项目/分类」下拉（数据来自 `GET /api/asset-library`，默认「默认项目 / 3D导演台」）。
- 构图提示词生成规则：取景比例 + 当前机位 + 各人物位置(x/z)与姿势 + 道具位置。

## 人物模型方案（方案 B）

- 内置默认：程序化胶囊素体 + 一个 CC0 许可的现成人形 GLB（如 Quaternius），附 CREDITS 说明文件。
- GLB 导入：加载任意人形 GLB，自动识别 `mixamorig:*` 骨骼并套用全部姿势预设/关节滑条；可「设为默认」，之后新建人物默认用它。
- 骨骼统一 `mixamorig:*` 命名，兼容用户后续从 Mixamo 官网下载 X Bot 自行替换（Mixamo 下载为 FBX，需 Blender FBX→GLB 一次转换）。
- 许可红线：不复用 TE_MAN 仓库内任何文件（其 LICENSE 禁止复制）；不使用 Mixamo 角色作为对外再分发资产。

## 错误处理

- 打开面板时 `directorScene` 解析失败 → try-catch 回退空场景 + 状态栏提示，不阻塞。
- 截图落盘 `/api/ai/upload-base64` 失败 / 资产库保存 403 → 面板内 toast，不写回节点。
- iframe 加载失败 → 面板内错误占位 + 重试按钮。

## i18n

- 新增 `static/js/i18n/director3d.js`（`director3d.*`：面板标题、姿势/机位/道具/关节中文标签、按钮、状态提示，中英双语）。
- `smart-canvas.js` i18n 词条补：`smart.railDirector3d`、`smart.createDirector3d`、`smart.directorNodeTitle` 等。
- 跑 `node static/js/i18n/validate-i18n.js` 校验。

## 非目标（第一版不做）

- 群演阵列、全景图/全景模式、骨骼模式与 OpenPose BODY_18 导入导出、FBX 直接导入。
- 场景的服务端单独持久化（场景只随画布节点保存）。
- 节点的连线输出端口（截图仅手动导出）。

## 验证

- 基础：`node --check` 全量 JS、`node static/js/i18n/validate-i18n.js`、`git diff --check`。
- 定向：`test_smart_canvas_only.py`（新增画布节点类型，确认仅智能画布、旧画布不回归）。
- 手动验收闭环：创建节点 → 打开面板 → 摆人物/机位（实时预览）→ 退出 → 节点缩略图更新 → 重进状态保留 → 复制节点独立 → 截图四出口 → 刷新画布节点恢复。
- 受登录/外部服务限制的项目须明确报告（模型资产来源、CC0 许可、Mixamo 替换路径）。
