# 灵感库 Git 同步实施计划

日期：2026-09-20
范围：提示词库与工作流库「灵感库」的受跟踪存储、服务端读路径收敛、管理后台同步按钮、静态资源 MIME 修复
设计依据：`docs/superpowers/specs/2026-09-20-inspiration-library-git-sync-design.md`

## 背景

灵感库目前无法可靠地随 Git 检出迁移，四个独立缺陷：

1. 工作流发布产物全部落在被忽略的目录：`data/workflow_library.json`（`data/*`）与 `assets/workflow_library/public/`（`assets/`），新克隆的机器灵感库为空。
2. 提示词发布只同步清单 `static/data/prompt-library-published.json`，封面落在 `static/images/prompt-library/published/` 却没有任何机制暂存；拉取后清单到了、封面全是死链。带封面的那一次提交（`708a683`）在未合并分支 `codex/pre-rollback-20260904-1730` 上，按决定不恢复。
3. 读路径把受跟踪清单与 `data/prompt_libraries.json["published"]` 运行时副本合并，同一逻辑记录两个写入方，A 机撤回会被 B 机的陈旧副本复活。
4. `/static`、`/assets`、`/output` 三个 `StaticFiles` 挂载经 Starlette `FileResponse` 用 `mimetypes.guess_type(...) or "text/plain"` 判类型；内置 CPython 3.10 不认识 `.webp`，实测 `HEAD /static/images/prompt-library/style_ue5.webp` 返回 `Content-Type: text/plain; charset=utf-8`。装过图片工具、注册表有 `.webp` 的机器正常，所以表现为"有的电脑正常有的不正常"。

已确认的用户决策：多机各自发布（需要合并与撤回墓标）；管理后台显式按钮完成「暂存 + 提交」，不 push；封面入库前压成 WebP、最长边 1024；工作流包内图片转 WebP（2048 / q90）。

## 目标

1. 灵感库的已发布内容全部落在受 Git 跟踪路径下，一次提交完整带走，包含封面与工作流包。
2. 多机各自发布/撤回不产生 JSON 数组冲突，撤回不被陈旧运行时副本复活。
3. 管理后台一个显式动作完成迁移、暂存与提交，且不污染无关的已暂存内容。
4. 打开灵感库不再出现封面异常；`.webp` 以 `image/webp` 提供。
5. 不把个人草稿、收藏、回收站等运行时数据带入版本库。

## 实施步骤

### 1. 常量与目录

`main.py` 常量区（约 410–455 行）：

- 新增 `LIBRARY_TRACKED_PATHS`：七个受跟踪目录（提示词/工作流的 `published`、`withdrawn`、封面目录，以及 `static/workflow-library/published`）。
- 由该常量派生读取用的 `published` / `withdrawn` glob，不要在别处重复硬编码路径。
- 新增 `WORKFLOW_LIBRARY_PUBLISHED_DIR`、`WORKFLOW_LIBRARY_WITHDRAWN_DIR`、`WORKFLOW_LIBRARY_PUBLISHED_ARCHIVE_DIR`、`WORKFLOW_LIBRARY_PUBLISHED_COVER_DIR`。
- 把 `PROMPT_LIBRARY_PUBLISHED_PATH`（单文件）改为 `PROMPT_LIBRARY_PUBLISHED_DIR` 与 `PROMPT_LIBRARY_WITHDRAWN_DIR`；`PROMPT_LIBRARY_PUBLISHED_COVER_DIR` 转为 WebP 目标目录。
- 删除 `PROMPT_LIBRARY_PUBLIC_DIR`（`main.py` 内无引用）。
- 启动流程中确保这七个目录存在（`os.makedirs(..., exist_ok=True)`），使写入路径不需要各自建目录。

### 2. 静态媒体类型

`main.py` 导入区、在三个 `app.mount` 之前注册 `.webp` 与 `.avif`：

```python
for _ext, _mime in ((".webp", "image/webp"), (".avif", "image/avif")):
    mimetypes.add_type(_mime, _ext)
```

`StaticFiles` / `FileResponse` 每次请求都调用 `mimetypes.guess_type`，因此三个挂载同时修好。

### 3. 资源重编码 helper

两个同步函数，调用方负责放进 `asyncio.to_thread`：

- `library_cover_webp(source_path, target_path, max_edge=1024, quality=82) -> bool`
  RGB、按最长边缩放、WebP 保存到 `target_path`。源打不开或不是图片返回 `False`，由调用方决定降级。
- `workflow_public_archive_bytes(raw: bytes) -> bytes`
  读入 zip；对 `resources/` 下的图片资源重编码为 WebP（最长边 2048、q90），重写 `workflow.json` 中该资源的 `archive` / `name` / `size`，**`url` 保持不变**（`import_canvas_workflow()` 靠 `url` 重写节点引用、靠 `archive` 定位文件）；用 `ZIP_DEFLATED` 重新打包。非图片资源与无法打开的资源原样拷贝。私有包不改写。

### 4. 提示词发布层

- `load_versioned_published_prompts()` 改为扫描 `PROMPT_LIBRARY_PUBLISHED_DIR` 下的 `*.json`，按 id 排序，排除 `PROMPT_LIBRARY_WITHDRAWN_DIR` 中已墓标的 id。
- `save_versioned_published_prompts()` 由「写一个数组」改为两个函数：写入单个 `<snapshot_id>.json`、以及删除并写墓标 `{"id": "<snapshot_id>"}`（内容确定，不含时间戳）。
- `merged_published_prompt_snapshots()` 增加墓标过滤，其余合并顺序（受跟踪优先）不变。
- `remove_published_prompt_snapshot()` 改为删除受跟踪单文件 + 写墓标 + 清理运行时副本，保持现有「两层都清」的语义。
- `prompt_public_cover_copy()` 输出改为 WebP（§3 的 helper），仅当 `source_prompt_id` 路径可解析时才重编码。
- `remove_prompt_public_cover()` 适配新目录与 `.webp` 扩展名，并覆盖同名的历史 `.png`。
- 发布/撤回路径已走 `merged_published_prompt_snapshots()`，无需改动调用方。

### 5. 工作流发布层

- 新增 `published_workflow_snapshots(data)`：与提示词同构的读路径（扫描 + 墓标 + 运行时并集 + 受跟踪优先）。
- `workflow_file_path()` 扩展：除 `/assets/` 外，支持 `/static/workflow-library/published/` 与 `/static/images/workflow-library/published/`，沿用 `commonpath` 包含校验。
- 服务端所有发布记录查找改走 `published_workflow_snapshots()`：`workflow_public_view()`、`apply_workflow_library_item()`、`download_workflow_library_package()`、`publish_workflow_library_item()`、`withdraw_workflow_library_snapshot()`。
  - 其中 `apply` 与 `package` 是灵感库每张卡片的「应用」与下载入口，不改会在运行时数组清空后 404。
  - `publish` 不改会对同一源工作流重复创建快照。
- `remove_workflow_files()` 走扩展后的 `workflow_file_path()`；不改会静默删不掉新路径下的包与封面，导致撤回内容被下一次 `git add -A` 重新提交。
- 发布时用 `workflow_public_archive_bytes()` 生成公开包；私有包与私有封面仍留在 `assets/workflow_library/private/`，位置与 URL 不变。

### 6. 并发与锁

- 发布、撤回、同步迁移共用 `CANVAS_LOCK`（现有 `save_versioned_published_prompts()` 用的就是它），不新增平行锁。
- 所有图像处理、zip 重建、文件复制一律在 `asyncio.to_thread` 内执行；`async def` 路由只做请求解析与响应组装。

### 7. 管理后台同步动作

**只读状态** `GET /api/admin/library-sync`（`require_admin`）返回：

- `git` 是否可用、项目根是否为工作树、当前分支；
- 受跟踪路径下的未提交变更数（`git status --porcelain -- <已存在路径>`）；
- 各库尚未迁移的运行时发布记录条数；
- 完整性自检：清单引用了但磁盘上缺失的封面/包（即「清单提交了、封面忘了提交」）。

**执行** `POST /api/admin/library-sync`（`require_admin`）：

1. **迁移**：运行时 `published` 中未被墓标的记录写入受跟踪布局，写完后从运行时 JSON 移除。工作流记录若 `archive_url`/`cover_url` 已不可解析，回退到 `source_workflow_id` 的私有包与封面；提示词记录**没有**回退路径，源条目的 `cover_url` 不参与。两者都取不到时字段写空，不丢弃记录。
2. **清理**：删除运行时中已被墓标的记录。
3. `git add -A -- <受跟踪路径中实际存在的那些>`。
4. `git commit -m "chore(library): sync inspiration library" -- <同一组路径>`，使用 pathspec 以免提交无关的已暂存内容。路径已过滤为磁盘上存在的，因为 Git 把不存在的 pathspec 当错误。无变更时返回 `changed: false` 且不产生提交。
5. 返回 commit hash、文件数与 Git 输出尾部。

Git 调用统一走一个 helper：`subprocess.run` 包在 `asyncio.to_thread` 里、`cwd` 固定项目根、返回码与 stderr 尾部原样回传。不做 push。

### 8. 前端

- `static/admin.html`：在 `system` 分区新增同步 panel，元素带 id 以便 `sectionFor('#<id>')` 注册；把 `static/js/admin-dashboard-v2.js` 里该 workspace 的导航标签由「公告发布」放宽为覆盖两个面板。
- `static/js/admin-dashboard-v2.js`：注册进 `workspaceElements.system`；渲染状态区与按钮；按钮有 busy 态与二次确认（动作会产生提交）；失败时展示 Git 的 stderr。
- `static/js/prompt-library.js`、`static/js/workflow-library.js`：封面 `<img>` 加 `onerror` 兜底为现有占位图标。
- `static/js/i18n/library.js`：新增文案补中英词条，前端一律走 `t()` 并保留中文兜底。
- 不手工改 `?v=`，由 `sync_static_html_versions()` 维护。

### 9. 数据迁移提交

- 把 `static/data/prompt-library-published.json` 的 4 条拆成 4 个 `static/data/prompt-library-published/<id>.json`，删除旧单文件。它是受跟踪文件，必须在提交内完成，不能留给运行时迁移。这 4 条 `cover_url` 为空且任何 ref 里都没有封面，保持无封面。
- 机器本地的运行时记录由管理后台按钮迁移；在按钮执行前读路径已兼容，不会丢。

### 10. 测试

新增 `tests/test_library_git_sync.py`（临时仓库里真跑 `git`，`shutil.which("git")` 缺失时跳过）：

- 发布提示词/工作流写入受跟踪布局，封面为 WebP，工作流包内图片被重编码且 `workflow.json` 的 `archive`/`name`/`size` 已更新、`url` 未变；
- 撤回写出的墓标内容确定（两次撤回同一 id 得到字节相同的文件），读路径不再返回，运行时陈旧副本被清除；
- 迁移把运行时记录搬进受跟踪布局并清空运行时数组；工作流走私有源回退；提示词无封面时写空；
- 运行时数组清空后，`apply` 与 `/package` 仍能解析受跟踪快照；同一源工作流重复发布不产生第二份快照；
- pathspec 提交不影响其它已暂存内容；
- 无变更返回 `changed: false` 且不产生提交；
- 非 Git 仓库或缺少 `git` 时返回可读错误而非 500。

新增 `tests/test_static_media_mime.py`：`.webp` 解析为 `image/webp`。

更新既有测试：

- `tests/test_prompt_library_publication.py`：路径由单文件改为目录，撤回改为墓标语义，移除对已删除的 `PROMPT_LIBRARY_PUBLIC_DIR` 的 patch。
- `tests/test_workflow_library_publication.py`：覆盖受跟踪布局与第 5 步列出的调用点。

### 11. 验收

- `.\python\python.exe -c "import ast, pathlib; ast.parse(pathlib.Path('main.py').read_text(encoding='utf-8')); print('OK')"`（不 import，避免启动写入）。
- 仓库级 `node --check` 扫描与 `node static/js/i18n/validate-i18n.js`。
- `git diff --check`。
- 定向测试：`test_library_git_sync.py`、`test_static_media_mime.py`、`test_prompt_library_publication.py`、`test_workflow_library_publication.py`、`test_smart_canvas_only.py`（工作流/画布相邻回归）。
- 起服务实测 `curl -I /static/images/prompt-library/style_ue5.webp` 为 `Content-Type: image/webp`。
- 浏览器验收由用户完成：两个灵感库打开无下载弹窗、无破损封面，第二个克隆能列出拉取到的工作流发布。本机沙箱禁止 Chrome 命名管道 IPC，无法起无头浏览器代验。

## 不在本轮范围内

- 不恢复 `codex/pre-rollback-20260904-1730` 上的任何内容。
- 不同步个人草稿、收藏、回收站、用户、部门等运行时状态。
- 不执行 `git push`，不处理远端与凭据。
- 不支持发布后编辑公开元数据或从「我的发布」重新发布。
- 不使用 Git LFS，改用重编码控制体积。
- 不清理旧机上遗留的 `assets/prompt_library/public/`、`assets/workflow_library/public/`（两个目录都已被忽略且迁移后不再读取，保留以便迁移出错时重试）。

## 回滚边界

主要涉及：

- `main.py`
- `static/admin.html`、`static/js/admin-dashboard-v2.js`
- `static/js/prompt-library.js`、`static/js/workflow-library.js`、`static/js/i18n/library.js`
- `static/data/prompt-library-published/*`（由 `static/data/prompt-library-published.json` 拆分而来）
- `tests/test_library_git_sync.py`、`tests/test_static_media_mime.py` 及两个既有发布测试
- 本计划文件

若同步机制本身出问题，可先回滚管理后台按钮与服务端同步端点，保留存储布局与 MIME 修复：前者可独立移除，后两者是缺陷 1、2、4 的直接修复。注意迁移一旦提交，受跟踪清单已按新布局落盘，回滚代码需要同时恢复旧单文件。
