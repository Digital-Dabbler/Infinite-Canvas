# Design QA — RunningHub 对标改版

最终结果：**passed**

## 对照基准

- RunningHub 无限画布参考：`codex-clipboard-0c3076aa-0fa1-43de-9f99-9170b298968e.png`
- 项目工作台参考：`codex-clipboard-a8264a9c-67c1-494c-affa-f9961b9ab0d3.png`
- 封面网格参考：`codex-clipboard-8c29cb89-87ca-4693-a3b9-54bce5f63bc1.png`
- 画布标注参考：`codex-clipboard-8caa9934-734e-48ad-8162-b33e7cef1711.png`

## 已验证

- 根页面只显示项目工作台；项目侧栏、固定封面网格、首格新建、搜索和旧版折叠区结构正确。
- 设置中心可从工作台左下打开；API 设置覆盖层可通过 `?panel=api-settings` 打开并保留工作台。
- 390×844 视口下项目侧栏切换为抽屉，封面网格为单列，设置中心保持可操作。
- 智能画布具备顶部账户区、六项左栏、底部视图控制、纵向新建菜单和右侧 Canvas AI 面板。
- 新建菜单仅保留上传、图片生成、视频生成、工作流分组、便签、提示词和循环。
- Canvas AI 紧凑模式显示“添加当前所选”，消息回写入口已接入图片、提示词和便签。
- 深浅主题变量、点阵背景、边线、圆角、层级和品牌绿交互态已统一。
- 页面 DOM、关键交互和控制台均完成浏览器检查；付费生成、真实平台余额与视频抽帧未发起外部调用。

## 视觉修正记录

- 移除根外壳的自动 UI 缩放，避免工作台在小屏被二次缩小。
- 将项目卡片从绝对定位画板改为响应式 16:9 封面网格。
- 将新建菜单限制为可滚动的纵向列表，避免小视口溢出。
- 账户、余额、设置和对话均使用浮层，不挤压或重排画布世界坐标。

---

# Clip Window QA — 2026-08-17

## Comparison target

- Source visual truth: `C:\Users\xuhao\.codex\generated_images\01a00bf5-bafc-75d3-afda-d9d5dee86cbc\exec-e439e9ef-5ac2-4fc0-811d-a9b5a70645bf.png`
- Browser-rendered implementation: `C:\Users\xuhao\AppData\Local\Temp\smart-clip-qa-clean.png`
- Full-view comparison evidence: `C:\Users\xuhao\AppData\Local\Temp\smart-clip-qa-comparison-final.png`
- Viewport: 1280 × 720 CSS px, device pixel ratio 1. Source: 1762 × 892 px; implementation: 1280 × 720 px; comparison normalized both captures to 1200 px wide.
- State: clip dialog open with a real canvas video selected, eight preview frames, and a non-zero start handle.

## Browser evidence

- Verified the renamed left-rail `剪辑` entry, active state, empty state, close button, and Escape close.
- Verified nine source-video options on a saved-video canvas; source switching, metadata loading, eight thumbnails, mute state, range drag from 0.0 s to 1.0 s, and visible timeline/export controls all behaved as expected.
- Checked mobile empty state at 390 × 844: no horizontal overflow and all controls remain usable.
- Browser console errors on the final clean test tab: none.
- Export was not submitted: it writes a media file and new node into the user's existing canvas. The tested UI reuses the existing `/api/video-tools/trim` endpoint and return-to-canvas path.

## Comparison history

- [P2, fixed] Moved the source selector from above the preview to the right settings column to match the selected composition.
- [P2, fixed] Constrained desktop preview height so portrait footage no longer pushes the timeline or export button out of view.
- [P1, fixed] Added an explicit hidden-state rule so the empty state cannot overlay loaded video.
- [P1, fixed] Pinned preview video to the frame with `object-fit: contain`, preserving the full portrait source with deliberate sidebars.

## Final fidelity review

**Findings**

No actionable P0, P1, or P2 differences remain in the tested states.

- Typography: existing product font hierarchy and monospace time readouts stay legible at 11–16 px.
- Spacing/layout: the dialog preserves the selected left-preview/right-settings hierarchy with all primary controls visible at desktop size.
- Colors/tokens: brand-green active, range, focus, and export states reuse `--rh-brand`; the implementation intentionally follows the active light theme while the selected mock is dark.
- Image/icon fidelity: real canvas media and the existing Lucide icon system are used; no placeholder imagery or custom-drawn interface icons were introduced.
- Copy/accessibility: semantic dialog, labels, focus-visible states, disabled empty state, reduced-motion behavior, and responsive layout were checked.

**Open Questions**

- A disposable canvas can be used for a release-time export write smoke test if an end-to-end persisted-output check is needed.

**Follow-up Polish**

- [P3] Add a thumbnail-card source picker only if users need richer source browsing than the current native video selector.

final result: passed
