# 本地 ComfyUI 工作流接入规范

本文是 Infinite Canvas 新增或改造本地 ComfyUI 工作流时的唯一对照清单。它约束工作流文件、字段映射、多后端执行和结果回收；不记录局域网地址、模型文件绝对路径或任何密钥。

## 开发时怎么使用本文

1. **先建工作流与字段映射**：将 ComfyUI 导出的 API 格式 JSON 放到 `workflows/`，同名 `.config.json` 描述需要从产品传入的字段。
2. **逐项套用“接入清单”**：确认输入媒体、提示词、随机 seed、输出节点及模型依赖，不能只在前端增加一个工作流名称。
3. **复用既有执行链路**：本地工作流应走 `main.py` 的 `/api/generate`，不得各自复制提交、轮询和下载逻辑。
4. **按“故障转移规则”审查错误处理**：只有明确的连接/服务不可用或模型兼容性错误可以换后端；工作流自身的确定性错误必须直接返回。
5. **测试并查看 timing**：用真实参考图运行一次，检查任务记录中的后端、`submission_attempts` 与 timing，再决定是否需要优化。

改造已有功能时，先确认它是否已经走 `/api/generate`。若是，只补工作流/config 和调用参数；若不是，优先迁入这条统一链路，而不是另起一套本地 ComfyUI 客户端。

## 1. 工作流与字段映射

### 1.1 工作流文件

- 必须是 **ComfyUI API 格式**：顶层为以节点 ID 为键的对象，每个节点有 `class_type` 和 `inputs`。
- 放在 `workflows/`；用户上传的工作流在 `workflows/custom/`。
- 仅把可由产品控制的值暴露为字段，模型、LoRA、VAE、采样器等工作流固有依赖保留在 JSON 内。
- 工作流必须有可产出文件的节点（通常是 `SaveImage`）；结果回收会从 ComfyUI history 的 outputs 中筛选实际输出。

### 1.2 `.config.json` 映射

同名映射文件把 UI/业务参数写到指定节点的 `inputs`：

```json
{
  "title": "工作流名称",
  "fields": [
    { "id": "reference_image", "label": "参考图", "type": "image", "node": "41", "input": "image" },
    { "id": "prompt", "label": "提示词", "type": "prompt", "node": "112", "input": "prompt" },
    {
      "id": "seed", "label": "随机种子", "type": "number",
      "node": "106", "input": "seed", "default": 1,
      "min": 1, "max": 4294967295, "step": 1, "random_enabled": true
    }
  ]
}
```

字段的 `node` 与 `input` 必须和 API JSON 完全一致。参考实现是 [comfyui-workflow-multiple-angles-api.config.json](/C:/AI/Infinite-Canvas/workflows/comfyui-workflow-multiple-angles-api.config.json)：`41.image`、`112.prompt`、`106.seed`。

### 1.3 随机种子

- 工作流有 seed 时必须映射它，并标记 `random_enabled: true`。
- 当前产品默认每次运行生成新的 seed，用户不需要理解或操作额外开关；任务结果会保存实际 seed，便于排查和复现。
- 不要依赖 JSON 内固定的 seed 值，否则每次生成会缺少结果多样性。

## 2. 多后端配置与选择

后端列表由 ComfyUI 设置页维护，服务端写入 `COMFYUI_INSTANCES`。地址只使用 `host:port`；列表顺序是稳定的兜底顺序，不是“第一个地址永远执行”。

一次任务的选择规则：

1. 并发读取可用后端的 `/queue`，并确认本次输入媒体是否已在该后端的 input 目录中。
2. 优先选择有效负载（远端队列与本服务本地保留量取较大值）更低的后端；负载相同则优先已有输入媒体者。
3. 对某一工作流刚发生模型兼容性失败的后端，临时排除；若全部被排除，仍保留全部候选，避免无后端可用。

兼容性缓存的键是 `(workflow_name, backend)`，冷却期为 30 分钟。到期后该后端会自动重新参与选择，因此修复模型后不会被永久跳过。

## 3. 故障转移规则

提交顺序是“选中的最佳后端 + 其余后端”。只有以下情况允许尝试下一台：

- 网络/连接错误、超时、连接重置或不可达；
- HTTP `502`、`503`、`504`；
- ComfyUI 返回 `prompt_outputs_failed_validation` 且包含 `value_not_in_list`：通常表示 UNet、LoRA、CLIP 等模型清单不兼容。

提示词格式错误、工作流 JSON 不存在或损坏、字段映射写错、未知节点等确定性问题必须直接报错，不能无意义轮询全部后端。

检测到模型清单不兼容时，记录失败原因并写入该工作流的临时兼容性缓存；下一次同一工作流会优先选择其他后端。

## 4. 输入媒体、提交和结果回收

### 输入媒体同步

`collect_required_comfy_media()` 从运行参数中识别图片、视频、音频、遮罩和文件名字段。选中后端缺少输入文件时：

1. 从其他可用后端的 `/view?type=input` 找到该文件；
2. 通过目标后端 `/upload/image` 同步；
3. 回退到另一台后端提交前，再把输入文件从初始后端同步到回退目标。

因此，工作流字段中的媒体名称必须是 ComfyUI 可识别的 input 文件名。不要把浏览器本机绝对路径写进工作流参数。

### 固定后端生命周期

一旦 `/prompt` 成功返回 `prompt_id`，后续 `/history` 轮询与 `/view` 输出下载**必须固定使用这台成功提交的后端**。跨机器查询会导致“提交成功但查不到历史/结果”的假失败。

输出下载后保存为站内 `/output/...` URL；浏览器不应直接持有 ComfyUI 主机地址或本机绝对路径。

## 5. 可观测性与排障

成功任务会保存 `timings`：

| 字段 | 含义 |
| --- | --- |
| `backend_select_ms` | 探测并预留后端的总耗时 |
| `backend_selection` | 选中的后端、探测到的队列/输入文件状态、临时跳过名单 |
| `input_sync_ms` | 首选后端所需输入媒体同步耗时 |
| `submission_ms` | 包含失败尝试及最终成功提交的总耗时 |
| `submission_attempts` | 每台后端的提交结果、耗时、回退输入同步耗时及失败原因 |
| `queue_at_submit` | 成功提交瞬间的队列快照 |
| `comfy_wait_ms` | 从提交后开始到 history 中出现结果的等待时间 |
| `output_download_ms` | 从该后端下载并落盘输出的耗时 |
| `total_ms` | 服务端工作流执行链路总耗时 |

`queue_wait_ms` 和 `execution_and_history_ms` 目前是“轮询首次观察到 running”的近似值。若 `queue_at_submit.prompt_state` 已是 `running`，不要把前者解读为真实排队时间；它可能包含下一次轮询观测延迟。

排障顺序：先看 `submission_attempts` 是否发生回退和模型不兼容；再看 `backend_selection` 是否选到预期机器；最后对比 `comfy_wait_ms` 与 `output_download_ms`。ComfyUI 前端显示的耗时通常只覆盖模型执行，不等于端到端 `total_ms`。

## 6. 新工作流接入清单

- [ ] API JSON 可以在目标 ComfyUI 直接通过 `/prompt` 校验。
- [ ] 目标机器都安装了该工作流所需的节点与模型；若并非全部具备，允许服务端兼容性回退。
- [ ] 添加同名 `.config.json`，并逐项核对节点 ID/输入名。
- [ ] 有输入参考图时映射为 `image` 字段，并确认文件会进入 ComfyUI input。
- [ ] 有随机 seed 时映射为数值字段并开启 `random_enabled`。
- [ ] 前端/业务调用传入的是字段映射的参数，而不是硬编码节点 ID 的私有分支。
- [ ] 成功后确认结果、`prompt_id`、最终 `backend` 和 `timings` 都被保存。
- [ ] 使用项目内置解释器做静态检查：

```powershell
.\python\python.exe -c "import ast, pathlib; ast.parse(pathlib.Path('main.py').read_text(encoding='utf-8')); print('main.py syntax OK')"
.\python\python.exe -m unittest discover -s tests -p 'test_smart_canvas_only.py'
git diff --check
```

## 7. 当前实现位置

- 后端地址读写与校验：[main.py](/C:/AI/Infinite-Canvas/main.py) 的 `COMFYUI_INSTANCES`、`/api/comfyui/instances`。
- 选择、兼容性缓存、队列快照：`reserve_best_backend()`、`comfy_submission_should_failover()`、`comfy_queue_snapshot()`。
- 执行链路：`/api/generate`。
- 角度工作流示例：[comfyui-workflow-multiple-angles-api.json](/C:/AI/Infinite-Canvas/workflows/comfyui-workflow-multiple-angles-api.json)。

修改这些通用函数前，先按本文检查是否会影响所有本地工作流，而不仅是正在开发的一个功能。
