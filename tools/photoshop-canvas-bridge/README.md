# Infinite Canvas Bridge 0.2.7 · Photoshop CEP 面板

独立的 Photoshop 图像交接面板，不修改也不依赖现有的
`photoshop-asset-connector` UXP 面板。

后续开发前请先阅读 [DEVELOPMENT.md](DEVELOPMENT.md)。其中记录了当前能力、
长期目标、不可破坏的行为约束、明确的非目标和变更验收清单。

## 工作方式

### 画布 → Photoshop

1. 启动 Infinite Canvas 服务并在面板中连接一次。
2. 在智能画布图片上右键，选择“发送到 Photoshop”。
3. 连接期间收到的新图片会立即作为 Photoshop 新文档打开。
4. 图片同时保留在最近收件箱中，可预览、重复打开，或作为智能对象置入当前文档。

连接成功后面板会注册 Photoshop Persistent。隐藏、折叠或关闭面板不会停止
WebSocket；但 Photoshop 每次启动后仍需至少加载并连接一次面板。重新打开面板、
刷新图库或网络重连只同步最近图片，不会自动打开历史任务。

收件箱展示当前账号最近 7 天、最多 50 张图片。单击进入大图预览，双击可选择
“置入当前文档”或“打开为新文档”。

互传记录按 Infinite Canvas 登录账号隔离。连接后状态栏会显示当前账号；点击
“切换账号”会退出旧账号、清空当前面板的收件箱，再要求使用新账号登录。同一
账号连接多台 Photoshop 时，各端收件箱都会更新，但只有最先认领任务的一台会
自动打开图片；如需完全独立的记录，应分别登录不同的 Infinite Canvas 账号。

### Photoshop → 智能画布

1. 在底部选择目标智能画布，可按画布名或项目名搜索。
2. 打开任意 Photoshop 文档后点击“发送到画布”。
3. 没有选区时发送整张合并画面；存在选区时自动按总选区最外围矩形裁切。
4. 选区宽或高小于 64px 时，面板会要求确认是否仍然发送。
5. 每次发送都会在目标智能画布最右侧创建一个新的上传图片节点。

导出始终在临时副本内完成并扁平化，不修改原文档、图层或选区。传入图片与
Photoshop 文档、来源节点和回传目标之间没有绑定，目标画布可随时更换。

## 兼容范围

- Windows
- Photoshop CC 2018（v19）
- Photoshop 2024
- Photoshop 2025

为覆盖 CC 2018，本插件采用 CEP + ExtendScript 以及 ES5 兼容写法，不使用 UXP，
也不依赖 Creative Cloud 安装流程。清单使用 CEP 7 schema 和 CSXS 7 最低运行时；
这是 CEP 8（Photoshop CC 2018）以及后续 CEP 版本共同支持的兼容格式。

## ZIP 覆盖安装

1. 解压 `Infinite-Canvas-Photoshop-Bridge.zip`。
2. 关闭 Photoshop。
3. 双击 `install.bat`；旧版本可直接覆盖升级。
4. 重启 Photoshop。
5. 从“窗口 → 扩展”或“窗口 → 扩展（旧版）”打开
   `Infinite Canvas Bridge`。

安装脚本会复制到：

```text
%APPDATA%\Adobe\CEP\extensions\com.daxiong.infinitecanvas.bridge
```

同时为当前 Windows 用户启用 CEP 7–12 的 `PlayerDebugMode`。安装不需要管理员权限
或签名证书。

## 开发结构

```text
CSXS/manifest.xml       CEP 宿主、兼容版本与面板清单
client/index.html       收件箱、预览、画布选择及发送界面
client/style.css        自适应瀑布流与面板视觉
client/js/cep.js        CEP Persistent 与 ExtendScript 调用封装
client/js/net.js        登录、REST 和认证媒体下载
client/js/app.js        WebSocket、幂等打开、图库与自由发送流程
host/index.jsx          文档打开、智能对象置入、选区检测与 PNG 副本导出
install.bat             当前用户安装/覆盖升级
uninstall.bat           删除插件文件
build-package.ps1       生成安装 ZIP
```

## 打包

在 PowerShell 中运行：

```powershell
.\build-package.ps1
```

输出位于 `dist/Infinite-Canvas-Photoshop-Bridge.zip`。
