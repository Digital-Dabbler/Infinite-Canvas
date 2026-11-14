# 智能画布列表加载性能优化计划

## 1. 目标
解决项目工作台在画布较多时加载缓慢的问题，实现秒级响应。

## 2. 优化方案
采用“服务端过滤 + 分页”模式：
- **后端 (`main.py`)**: 改造 `/api/canvases` 接口，支持 `project_id`、`limit`、`offset` 查询参数，不再全量读取。
- **前端 (`static/js/canvas-list.js`)**: 改造 `loadAll` 函数，支持按需分页请求，并在 UI 上处理“加载更多”逻辑。

## 3. 具体实施步骤
1.  **后端重构**：
    - 修改 `list_canvases()` 函数，支持可选的过滤和分页。
    - 更新 `main.py` 的 `/api/canvases` 路由，解析 `request.query_params`。
2.  **前端适配**：
    - 修改 `canvas-list.js`，增加分页状态管理。
    - 确保 `renderBoard()` 能够增量渲染分页返回的数据。
    - 在界面下方添加“加载更多”按钮或监听滚动事件触发加载。
3.  **兼容性验证**：
    - 验证旧项目、默认项目及搜索功能在分页模式下依然正常工作。
    - 运行 `test_smart_canvas_only.py` 等相关测试，确保改动未破坏现有功能。

## 4. 风险控制
- 确保分页逻辑在不同用户权限下一致（`canvas_sharing_public` 权限检查需在过滤后应用）。
- 不破坏现有的撤销、重命名等基于内存状态的细粒度同步机制。

---
请审核此计划，如无问题，我将开始编写实现计划。

## 5. 实施结果（2026-11-15）

### 5.1 实测根因（本机 `data/canvases/`，200 个画布 / 163.7 MB）

旧链路每次回到工作台会完整解析画布目录 **4 遍**，并且全部同步跑在 `async def` 里，直接阻塞事件循环（同一时间 WebSocket 与轮询一起卡）：

| 调用 | 触发原因 |
| --- | --- |
| `iter_canvas_records()` | 列表本身 |
| `load_canvas(record["id"])` | `/api/canvases` 只为算 sharing 字段而重新读每个文件 |
| `cleanup_expired_canvas_trash()` | 被 `iter_canvas_records()` 和 `canvas_assets_index()` 各调一次 |
| `list_projects()` | `/api/projects` 再数一遍画布数量 |

实测量（本机）：一次全量解析 ≈ **1451 ms**；纯 `os.stat` 一遍 ≈ **5 ms**。因此旧路径 ≈ **5.8 s** 的纯 JSON 解析，加上 200 条记录的序列化与传输，即用户看到的“好半天”。

### 5.2 服务端实现

1. **持久化元数据索引** `data/canvas_meta_index_cache.json`（`CANVAS_META_INDEX_CACHE_NAME`，`CANVAS_META_INDEX_LOCK`）。
   - 条目：`{mtime_ns, size, deleted, record, sharing}`，按文件名索引。
   - 失效判定复用 `canvas_assets_index()` 已验证的 `(mtime_ns, size)` 签名范式；命中即完全不解析文件，只需 `os.stat`。
   - 缓存路径由 `canvas_meta_index_cache_path()` 在调用时按当前 `DATA_DIR` 拼出（不设模块级常量），测试替换 `DATA_DIR` 即自动隔离。
   - `ensure_canvas_cover()` 补写封面的情况会二次 `os.stat` 重新取签名，避免下次再解析一次。
   - 不可解析 / 非 `smart` 的文件按旧行为静默跳过，且**不会**因此每次重写缓存（由文件名集合比较负责剔除过期条目）。
2. **sharing 字段不读文件**：`canvas_meta_sharing_fields()` 从索引条目取 `owner_user_id` / `editor_user_ids` / `ownership_state`，并与 `load_canvas()` 的 legacy 合成逻辑保持一致。
3. **回收站清理由索引驱动**：`expire_canvas_trash_entries()` 只按条目里的 `deleted_at` 判断过期，不再为清理而解析全部画布。
4. **接口分页**：`/api/canvases` 支持 `project_id`、`q`、`limit`、`offset`，返回 `{canvases, total, offset, limit, has_more, counts}`；`counts` 给出每个项目的 `{total, editable}`，使项目列表与删除确认框不必依赖已加载的卡片。
   - `limit=0`（默认）保留整表返回的旧契约，Photoshop 桥与其他旧客户端不受影响。
   - 排序与旧实现一致：置顶优先、`updated_at` 降序。
5. **不阻塞事件循环**：`/api/canvases` 与 `/api/projects` 均为 `await asyncio.to_thread(...)`；冷启动仍需一次全量解析，但那一次不再冻结整个服务。

### 5.3 前端实现（`static/js/canvas-list.js`）

- 单页 `CANVAS_PAGE_SIZE = 60`；`loadBoardPage({reset, projectId, query})` 负责请求、去重追加、维护 `offset/total/has_more/counts`，并用 `canvasPageRequestId` 丢弃乱序响应。
- 首屏 loading 占位、底部“加载更多”卡片、滚动到距底 240 px 自动续页、窗口 resize 后自动补满首屏。
- 计数全部改信服务端 `counts`（项目名后的数字、删除确认框中的可删数量、画布总数），分页不再影响统计。
- 搜索改为 250 ms debounce 后走服务端 `q`；输入瞬间仍做一次本地过渡过滤，避免等待期间列表跳变。
- 剪切/粘贴改用 `clipboardCanvas` 快照：分页后目标项目尚未加载时也不会丢失被剪画布。

### 5.4 效果

| 场景 | 优化前 | 优化后 |
| --- | --- | --- |
| 首次访问（索引未建立） | ≈5.8 s 阻塞 + 全量传输 | ≈1.45 s 一次解析（在线程中，不阻塞） |
| 之后每次回到工作台 | ≈5.8 s 阻塞 + 全量传输 | ≈5 ms（`os.stat`）+ 60 条卡片 |

### 5.5 验证

- 新增 `tests/test_performance_optimizations.py`：`CanvasMetaIndexCacheTests`（7）、`CanvasListPageTests`（4）、`CanvasListPagingContractTests`（4）、`CanvasAssetIndexCacheTests`（2），共 17 项全部通过（含 skip 缓存：经典画布只解析一次、命中后不重写索引、不进入项目计数）。
- 回归：`tests/test_project_canvas_deletion.py`（10）、`tests/test_smart_canvas_only.py`（23）通过；`main.py` 语法检查、全部 `static/js/**/*.js` 的 `node --check`、`node static/js/i18n/validate-i18n.js`（1568 keys）、`git diff --check` 均通过。

### 5.6 补充：旧版经典画布与索引 skip 缓存（2026-11-15 追补）

清点 `data/canvases/` 时发现 **16 个 `kind: "classic"` 的旧版经典画布（5.85 MB）**，其中 `7122875e2b4d4c4baabf6db62ad905c2.json`（标题「徐豪康」，2026-06-25 最后一次保存时被截断，解码后 30939 字符即报错位置）已损坏。

它们与列表无关：`list_canvases()` 只收 `smart`，`load_canvas()`（[main.py:6470](main.py#L6470)）对非 smart 直接 404，因此这批文件既不可见也不可打开；`data/` 下唯一引用这些 id 的只有会自动重建的 `canvas_asset_index_cache.json`。处理方式：

1. **全部归档而非删除**：移动到 `data/canvases/.recovery/<id>.json.classic-archived-20261115`（沿用既有 recovery 命名，字节保留；`.recovery/` 不参与列表、恢复或媒体索引，`archive_orphan_canvas_files()` 也不会再动它们）。归档后画布目录为 **184 个全部 smart / 157.9 MB**。
2. **索引补 skip 缓存**：`build_canvas_meta_index()` 与 `canvas_assets_index()` 现在都会为非 smart、不可解析的文件写入 `{"mtime_ns", "size", "skip": True}` 条目并持久化。此前这些文件没有条目可命中，**每次回工作台都要重新解析**（本机实测 16 个文件 / 5.85 MB / 48 ms），是"热路径 5 ms"名不副实的来源。现在热路径真正只剩 `os.stat`；`canvas_meta_entries()` 会过滤掉 skip 条目，项目计数与回收站列表不受影响。

### 5.7 遗留事项

- `tests/test_performance_optimizations.py` 中的 `SmartCanvasPerformanceContractTests` 因引用已删除的 `static/js/asset-manager.js` 而无法运行（与本次改动无关），未在本次修复。
