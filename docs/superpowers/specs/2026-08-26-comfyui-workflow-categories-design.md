# ComfyUI 工作流分类设计

## 目标

将产品功能固定使用的 ComfyUI 工作流，与管理员上传、供画布自由选择的工作流彻底分离。解决角度生成结果被笼统标记为“自定义”、系统工作流混入自定义选择器的问题。

## 分类与存储

| 分类 | 目录 | 来源标记 | 用途 |
| --- | --- | --- | --- |
| 系统工作流 | `workflows/system/` | `source: "system"` | 角度、抠图、多图编辑等产品功能固定调用 |
| 自定义工作流 | `workflows/custom/` | `source: "custom"` | 管理员上传，供画布在“自定义工作流”中选择 |

不再在 `workflows/` 根目录放置任一类别的工作流 JSON。此次迁移将根目录现有的全部工作流 JSON（排除 `.config.json`）统一迁入 `workflows/system/`；`workflows/custom/` 现有项目保持自定义来源。工作流 API 通过明确的 `source` 字段表达来源，不靠名称、文件图标或当前硬编码内置清单推断。

## 用户界面与行为

1. 智能画布的 ComfyUI“自定义工作流”选择器只请求并显示 `source: "custom"` 的项目。
2. 角度等功能继续复用统一的 ComfyUI 执行链路，但用自己的固定系统工作流名称运行；结果卡和生成面板显示功能名及系统工作流标题，例如“角度编辑 · 多角度参考图生成”，不显示“自定义”作为唯一来源。
3. ComfyUI 设置页分为“系统工作流”和“自定义工作流”两个清晰分组。系统工作流可编辑 `.config.json` 参数映射，以便管理员维护输入字段；系统工作流不可删除。自定义工作流可编辑和删除。

## API 与兼容性

- `GET /api/workflows` 返回每项的 `source`，并支持按来源筛选；ComfyUI 设置页使用完整列表并分组，智能画布的自定义选择器必须显式请求 `source=custom`。
- `POST /api/workflows` 始终写入 `workflows/custom/`。
- 读取、运行及保存配置接口接受 `system/<name>.json` 和 `custom/<name>.json`，并做目录白名单与路径清洗。
- 删除接口拒绝 `source: "system"`；不能再依赖少量文件名构成的 `BUILTIN_WORKFLOWS` 集合做权限判断。
- 既有画布中保存的根目录工作流名，会按本次“根目录全部迁入 system”的确定规则映射到 `system/<name>`；迁移完成后，根目录不再作为正常读取来源。
- `/api/generate` 及所有其他实际执行入口必须使用同一来源解析与路径白名单 helper，不得继续直接拼接 `WORKFLOW_DIR + workflow_json`。

## 数据流

```text
功能动作（角度等） -> 固定 system/<workflow> -> /api/generate -> 结果显示功能名和工作流标题
画布自定义选择器 -> /api/workflows?source=custom -> custom/<workflow> -> /api/generate
ComfyUI 设置页 -> system/custom 分组 -> 对应配置管理权限
```

## 错误处理

- 固定功能工作流缺失或映射不存在时，阻止提交并返回明确的“系统工作流不可用”错误，禁止退回到列表第一项。
- 自定义工作流不存在、已删除或字段映射不合法时，保持现有明确错误响应，禁止退回到其他工作流，不影响系统功能工作流。
- 目录外路径、根目录新工作流和未知来源均拒绝访问。

## 验收与测试

- API 列表能区分并筛选两种来源；上传只写入 `custom/`。
- 系统工作流不能删除，但其参数映射可保存；自定义工作流可以删除。
- 角度生成请求确实引用 `system/comfyui-workflow-multiple-angles-api.json`，且结果面板显示“角度编辑 · 多角度参考图生成”；自定义结果显示“自定义工作流 · 标题”。
- 自定义选择器不显示系统工作流。
- 旧画布中保存的根目录工作流名在迁移后仍可加载和运行，且不会退回或误选其他工作流。
- 运行定向 ComfyUI/智能画布测试、JavaScript 语法检查和 `git diff --check`；有已登录本地服务时补充角度与自定义工作流的浏览器验证。
