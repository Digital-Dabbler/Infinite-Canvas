# RunningHub 事前估价体系设计

> 本文是 2026-11-13 立项时的方案原文整理。实际落地过程见文末「实施记录」。

## 目标

在智能画布、PSD、Composer 等所有带「运行」意味的按钮上，**运行之前**就显示这一次大概要花多少钱。当前只针对 RunningHub 平台。

## 背景：为什么不能精确

调研结论（2026-11-13）：

- RunningHub 官方**没有「先询价再运行」的接口**。每个端点、AI 应用、工作流的官方文档里都没有单价字段。
- 每次调用的准确金额只在任务结束后随轮询响应的 `usage.consumeMoney`（人民币元）、`usage.thirdPartyConsumeMoney`（三方渠道）返回；失败任务两者为空。
- AI 应用（`webappId`）按 GPU 时长计费，本身不可能事前确定——同一应用不同输入耗时不同。

所以只能做「估」，不能做「问」。目标不是精确，是**不让人盲点**。

## 三条路线（按可靠性从高到低）

| 优先级 | 来源 | 精度 | 说明 |
| --- | --- | --- | --- |
| 1 | 历史实测（history） | 高 | 同一配置组、同一端点/应用的真实扣费样本 |
| 2 | 单价表（table / manual / official） | 中高 | 管理员录入的固定单价，或官方公开标价，乘本次数量 |
| 3 | GPU 分钟兜底（fallback） | 低 | 仅在配置了分钟单价时生效 |

三样都没有 → `unknown`，诚实显示「费用待定」，**绝不编造数字**。

## 估价闸门

对每个（配置组, 类型, 端点）三元组，按顺序判定：

1. **历史实测样本 ≥ 3 条（`HISTORY_RELIABLE_SAMPLES`）**
   → `confidence=high`，`source=history`，金额取样本中位数，区间取 p25~p75。
2. **历史样本 1~2 条**
   → `confidence=medium`，`source=history_sparse`，如实标注「样本较少」，金额取样本中位数并给区间。
3. **价格表命中**
   → `confidence=high`（token 计价降为 medium），`source=table|manual|official`。
   `amount = unit_cny × quantity`；`fixed` 计价不看数量。官方分档价天然带 low/high，显示为区间。
4. **GPU 分钟兜底**
   → `confidence=low`，`source=fallback`。
5. **全无** → `confidence=unknown`，`source=unknown`，显示「费用待定」。

估价**只展示、不拦截**：金额多高都不会阻止运行，也不会改变任何用量配额判定。

## 数据文件

| 文件 | 归属 | 内容 |
| --- | --- | --- |
| `data/runninghub_prices.json` | 管理员 | 管理员维护的单价表，schema version 1 |
| `data/runninghub_price_stats.json` | 系统滚动 | 实扣回流累积的样本，FIFO 上限 30 条/键 |
| `static/runninghub/official_prices.json` | 生成物 | 从官网抓取的公开标价（只读视图，不参与管理员表的读写） |

### 站点隔离（2026-11-14 修正）

RunningHub 有两个独立结算站点，**公开接口返回的币种由请求头 `Origin` 决定**：

| 站点 | 必须携带的 Origin | 返回币种 |
| --- | --- | --- |
| `https://www.runninghub.cn` | `Origin: https://www.runninghub.cn` | CNY |
| `https://www.runninghub.ai` | `Origin: https://www.runninghub.ai` | USD |

抓 `.ai` 时若误带 `.cn` 的 Origin（或不带 Origin），上游会把**人民币数字**原样返回（例如 `nano-banana-2/text-to-image/economy` 返回 `0.19` 而非 `0.027`）。旧脚本正是踩了这个坑：把 `¥0.10` 打上 `currency: USD` 落盘，运行按钮于是显示「人民币数字 + 美元符号」。

因此 `official_prices.json` 现在按站点分桶落盘：

```json
{ "models_by_site": { "cn": { "<端点>": {…"currency":"CNY"} }, "ai": { "<端点>": {…"currency":"USD"} } } }
```

顶层 `models` 只保留合并视图作兼容。估价时 `runninghub_estimate_for_profile()` 先按配置组的 RunningHub `base_url` 判定站点，**直接取该站点的原生标价**（`.ai` 直接是官网美元单价），只有跨站兜底才走 `RUNNINGHUB_CURRENCY_RATES` 换算，避免汇率口径漂移。

### 历史实扣的币种归属

`usage.consumeMoney` 的币种与该配置组钱包一致：`.ai` 扣美元、`.cn` 扣人民币。统计条目因此记录 `currency` 字段；早期缺失该字段的旧样本按**当前配置组结算币种**解释，绝不把美元实测值当人民币再除以汇率（那会把 `$0.015` 算成 `$0.0021`）。配置组切换站点致币种冲突时，`apply_observed()` 会清空异币种旧样本，绝不相混求中位数。


### 价格表结构

```json
{
  "schema_version": 1,
  "models": {
    "<端点 ID>": {
      "billing": "per_unit | per_second | per_minute | fixed",
      "unit_cny": 0.13,
      "low_cny": 0.13,
      "high_cny": 0.52,
      "unit_quantity_param": "",
      "default_quantity": 1,
      "currency": "CNY | USD",
      "confidence": "high | medium | low",
      "note": "官方标价 ¥0.13~0.52/张"
    }
  },
  "apps": {},
  "workflows": {},
  "fallback": { "unit_cny": 0 }
}
```

单价合法区间 `0.0001 ~ 1000`（`UNIT_PRICE_MIN/MAX`），越界视为非法录入并拒绝保存。

### 统计结构

键为 `api_profile_id|kind|entry_id`，**按配置组隔离**——A 组的实扣数据不会影响 B 组的估价。

```json
{
  "schema_version": 1,
  "entries": {
    "legacy-shared|model|dola-Seedream-5.5/text-to-image": {
      "samples": [0.016, 0.015],
      "median": 0.0155,
      "p25": 0.015,
      "p75": 0.016,
      "updated_at": "2026-11-13T19:00:00+08:00"
    }
  }
}
```

金额 ≤0 或非法的样本不入库；超过 30 条按 FIFO 丢弃最旧的（`HISTORY_SAMPLE_CAP`）。

### 三层合并顺序

估价视图 = 管理员表 → 实测种子（`SEED_MODELS`）→ 官方标价，**先到先得**：

1. 管理员录入的价最优先——他知道自己的账号实际怎么扣费；
2. 实测种子次之——真金白银扣过的金额比官网标价更贴近账单（如图层拆分实测 ¥0.19）；
3. 官方标价兜底，保证大部分模型首日就有数字。

种子与官方标价**只在内存中合并**，永不写回 `data/runninghub_prices.json`，否则管理员点一次保存就会把几百条官方价固化成用户数据。

## 实扣回流

任务结束（SUCCESS）时，从轮询响应里取 `consumeMoney`（取不到则 `thirdPartyConsumeMoney`），写进该配置组对应的统计键。

关键点：

- 异步任务的配置组在**提交时**就记进 scope metadata（`rh_price_kind` / `rh_price_id`），轮询时按原配置组归属，不会因为轮询请求来自别的上下文而串组。
- 只有 `provider == runninghub` 且模型不是 `app:` / `workflow:` 前缀时才记录（应用与工作流走另外两条线）。
- 失败任务不写样本。
- 同一任务重复轮询不会重复记账：样本按事件收口一次。

## 接口

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/runninghub/estimate?kind=&id=&quantity=&res=` | 登录用户，按自己的配置组解析 | 返回单次估价 |
| GET | `/api/runninghub/prices` | 管理员 | 返回管理员表原文 + 官方标价条数摘要 |
| PUT | `/api/runninghub/prices` | 管理员 | 保存管理员表（只回写管理员层） |

`kind` 取 `model` / `app` / `workflow`；`id` 为端点 ID 或 `webappId` / 工作流 ID；`quantity` 为张数或秒数；`res` 为分辨率（`1k` / `2k` / `4k` 或 `WIDTHxHEIGHT`），用于官方分档价收敛。

响应：

```json
{
  "success": true,
  "currency": "CNY",
  "estimate": {
    "amount": 0.23, "low": 0.23, "high": 0.23,
    "confidence": "high", "source": "official",
    "samples": 0, "note": "官方标价 2K ¥0.23/张",
    "native": { "amount": 0.23, "low": 0.23, "high": 0.23 }
  }
}
```

## 前端徽标

`static/js/price-badge.js` 提供 `window.RHPriceBadge = { attach(anchor, specFn), refreshAnchor(anchor) }`：

- 徽标作为「运行」按钮外部左侧的 `<span class="rh-price-badge">` 插入，按钮自身尺寸不变。
- `spec = { kind, id, quantity, res, multiplier }`，`multiplier`（张数等）纯前端乘算，不重复请求后端。
- 300ms 防抖 + 60s TTL 缓存 + 同键请求去重。
- 文案：`unknown` → 「费用待定」灰色；有区间 → `≈¥0.13~0.52` 金色；`confidence=low` → 弱化色。

挂载点：Composer 运行按钮、PSD 图层拆分按钮，以及其它以「运行/提交」为主要动作的按钮。

## 后台维护

管理员后台「API 设置 → RunningHub → 事前估价 · 价格表」可编辑模型单价、应用/工作流行与 GPU 分钟兜底；面板同时显示「已内置 N 条官方标价（只读）」，同一端点管理员录入的价优先。

## 已知局限

- **不覆盖**：官方未公开标价的模型（如部分三方渠道版、Luma uni-1 等）只能等实扣回流后才有数字，之前显示「费用待定」。
- **应用与工作流**按 GPU 时长计费，即使有单价也只能给区间。
- **Token 计价模型不估单次总价**：`gpt-image-2.5/2.6 .../stable-token`、`MiniMax-H3-Image`、`seedance-2.5-...-token` 的官方价是「每百万 tokens 费率」，跑之前无法知道实际 token 用量。旧逻辑把 `$8.00/1M tokens` 当成「每次 `$8.00`」，运行一张图能显示 `$8.00~$30.00`。现在这类条目**不入价格表**，只在实扣回流后按历史样本报价（如 `$0.0077`），之前显示「费用待定」——这是刻意选择的诚实，不是缺失。
- 官方标价是「名义价」，实际账单可能因折扣、渠道、分辨率档位不同而不同，故文案统一注明「实际以扣费为准」。
- **小额美元精度**：国际站大量单价是千分位/万分位（`$0.015`、`$0.027`、`$0.009`），徽标金额格式化必须保留到第 3~4 位有效小数，固定两位会把它们截成 `$0.02` 甚至 `$0.00`。

## 实施记录

- `7a31b71` 落地三档闸门、实扣回流、管理员价格表与运行按钮徽标。
- `1200bad` 接入 RunningHub 官方标价（抓取脚本 `tools/sync_runninghub_official_prices.py` + `static/runninghub/official_prices.json`）。
- 后续修正：`66484fc`（国际站渠道版）、`f9ccd1d`（展示名→端点映射）、`1a2d95e`（价目表解析剔除假低价）、`f718cb0`（运行按钮估价跟随当前 Composer 模型）。
- 2026-11-14 修正「人民币数字 + 美元符号」错价：
  - 抓取脚本为两站分别构造 `Origin`，产出 `models_by_site.cn` / `models_by_site.ai` 双桶；实测 `.ai` 渠道版 `gpt-image-2.0/text-to-image` = `$0.015`（此前被误记为 `$0.10`）。
  - 价目表解析补上 `$` 金额、英文单位（`piece`/`call`/`sec`）、英文输入侧档位（`Input image`）、英文像素档位（`2.36MP`、`1024×1536`）与 `+$0.16` 附加项增量的排除；Token 计价表整体拒收。
  - 估价按配置组站点直接取同站原生价，跨币种才换算；历史实扣按配置组结算币种记账与解读。
  - 徽标金额格式化为第 3~4 位有效小数保留（`$0.015`），与右上角钱包单位一致。
  - 后台价格表输入框占位符跟随当前配置组币种（`美元 $` / `元 ¥`），避免国际站管理员心算填人民币。