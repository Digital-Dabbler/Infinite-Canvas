# -*- coding: utf-8 -*-
"""抓取 RunningHub 官方标价，生成 ``static/runninghub/official_prices.json``。

用途：运行按钮的事前估价优先使用官方标价（管理员价格表可覆盖）。官方价目不在
``models_registry.json`` 里，而是来自 RunningHub 官网 call-api 页面的两个公开接口：

* ``POST https://www.runninghub.cn/api/sku/list``        标准模型 SKU 目录（含单价）
* ``POST https://www.runninghub.cn/api/area/price/table`` 单个 SKU 的分档价格表

模型识别用 ``models_registry.json`` 的 ``name_cn`` 与 SKU 标题对齐，最终以
``endpoint``（也就是本项目 RunningHub 模型 ID）为键，和价格表保持一致。

用法（项目内置解释器）::

    .\\python\\python.exe tools\\sync_runninghub_official_prices.py

脚本只写这一个 JSON 文件，不改仓库其他内容；需要代理时沿用系统 http_proxy。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

# httpx 会把 NO_PROXY 里的 "[::1]" 当成 URL 解析并抛 InvalidURL，先清掉。
for _key in ("NO_PROXY", "no_proxy"):
    os.environ.pop(_key, None)

import httpx  # noqa: E402  (必须在清理环境变量之后导入)

BASE = "https://www.runninghub.cn"
SKU_LIST_URL = BASE + "/api/sku/list"
PRICE_TABLE_URL = BASE + "/api/area/price/table"
# 国际站目录包含国内站没有的“渠道版”型号（nano-banana / gpt-image / veo / grok），
# 名称直接就是 ``<产品>/<操作>/<economy|stable>``，是我们唯一能拿到的渠道价来源。
AI_BASE = "https://www.runninghub.ai"
AI_SKU_LIST_URL = AI_BASE + "/api/sku/list"
REGISTRY_URL = (
    "https://raw.githubusercontent.com/HM-RunningHub/ComfyUI_RH_OpenAPI/main/models_registry.json"
)
REGISTRY_MIRRORS = [REGISTRY_URL, "https://ghproxy.net/" + REGISTRY_URL]
OUTPUT_PATH = os.path.join("static", "runninghub", "official_prices.json")

# 官网档位段 → 画布模型 ID 的后缀
TIER_TO_MODEL_SUFFIX = {"economy": "-channel-low-price", "stable": "-official-stable"}
SKU_NAME_RE = re.compile(r"^(?P<base>.+)/(?P<op>[a-z0-9][a-z0-9-]*)/(?P<tier>economy|stable)$")

# 画布模型 ID ↔ 实际调用端点，和 main.RUNNINGHUB_MODEL_ENDPOINT_ALIASES /
# runninghub_registry_fallback 保持一致：同一个模型两种写法都要能命中价格。
MODEL_ID_ENDPOINTS = {
    "gpt-image-2.0/text-to-image-channel-low-price": "rhart-image-g-2/text-to-image",
    "gpt-image-2.0/edit-channel-low-price": "rhart-image-g-2/image-to-image",
    "gpt-image-2/text-to-image-official-stable": "rhart-image-g-2-official/text-to-image",
    "gpt-image-2/image-to-image-official-stable": "rhart-image-g-2-official/image-to-image",
    "nano-banana/text-to-image-channel-low-price": "rhart-image-v1/text-to-image",
    "nano-banana/edit-channel-low-price": "rhart-image-v1/edit",
    "nano-banana/text-to-image-official-stable": "rhart-image-v1-official/text-to-image",
    "nano-banana/edit-official-stable": "rhart-image-v1-official/edit",
    "google/veo3.1-fast/text-to-video-channel-low-price": "rhart-video-v3.1-fast/text-to-video",
    "sora-2/text-to-video-official-stable": "rhart-video-s-official/text-to-video",
}

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": BASE,
    "Referer": BASE + "/call-api",
}

MONEY_RE = re.compile(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)")
UNIT_HINTS = (
    ("秒", "per_second", "秒"),
    ("second", "per_second", "秒"),
    ("千 token", "per_unit", "千 tokens"),
    ("千token", "per_unit", "千 tokens"),
    ("token", "per_unit", "千 tokens"),
    ("张", "per_unit", "张"),
    ("图", "per_unit", "张"),
    ("次", "per_unit", "次"),
    ("call", "per_unit", "次"),
    ("帧", "per_unit", "帧"),
    ("字符", "per_unit", "千字符"),
    ("char", "per_unit", "千字符"),
    ("秒", "per_second", "秒"),
)


def _norm(name: Any) -> str:
    return re.sub(r"[\s\-_/]+", "", str(name or "").strip().lower())


def _money(text: Any) -> Optional[float]:
    match = MONEY_RE.search(str(text or ""))
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


# 单价比同表其他档低两个数量级，基本是把“计价系数”当成了单价。
_ANOMALY_PRICE = 0.01


def _unit_of(text: Any) -> tuple:
    low = str(text or "").lower()
    for hint, billing, label in UNIT_HINTS:
        if hint in low:
            return billing, label
    return "per_unit", "次"


def fetch_sku_catalog(client: httpx.Client, url: str = SKU_LIST_URL) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = client.post(url, json={"pageNum": page, "pageSize": 100})
        payload = resp.json() or {}
        data = ((payload.get("data") or {}).get("page") or {})
        batch = data.get("records") or []
        records.extend(batch)
        if not data.get("hasNext") or page >= 20:
            break
        page += 1
    return records


def _trim_model_name(name: Any) -> str:
    """``f-2-klein-9b/text-to-image`` → ``klein-9b/text-to-image``。

    部分是"通用名+版本号"的写法，注册表里的端点不带厂商前缀，去掉首段
    数字/版本前缀后再比一次，仍然要求操作名完全一致。
    """
    text = str(name or "").strip()
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        return text
    head = re.sub(r"^[a-z]-\d+-", "", parts[0], flags=re.I)
    head = re.sub(r"^\d+-", "", head)
    return "/".join([head, *parts[1:]]) if head != parts[0] else text


# 官网 SKU 名与注册表端点完全对不上、但经人工核对确认是同一个模型的，
# 显式登记在这里。只放核实过的条目，不靠模糊匹配猜。
SKU_TO_ENDPOINT = {
    # FLUX 3 Image Klein：官网 SKU 名带厂商前缀 f-2-，注册表端点没有。
    "f-2-klein-9b/text-to-image": "rhart-image-flux3-klein/text-to-image",
    "f-2-klein-4b/text-to-image": "rhart-image-flux3-klein/text-to-image",
    "f-2-klein-9b/edit": "rhart-image-flux3-klein/image-to-image",
    "f-2-klein-4b/edit": "rhart-image-flux3-klein/image-to-image",
}


def app_model_ids_from_sku_name(name: Any) -> List[str]:
    """官网 SKU 名 ``nano-banana/text-to-image/economy`` → 画布模型 ID 候选。

    画布里的渠道模型 ID 是 ``<产品>/<操作>-channel-low-price`` /
    ``-official-stable``；同时保留官网写法本身，两种键都能命中价格表。
    """
    text = str(name or "").strip()
    match = SKU_NAME_RE.match(text)
    if not match:
        return [text] if text else []
    suffix = TIER_TO_MODEL_SUFFIX.get(match.group("tier"))
    ids = [text]
    if suffix:
        ids.insert(0, f"{match.group('base')}/{match.group('op')}{suffix}")
    return ids


def fetch_registry(client: httpx.Client) -> List[Dict[str, Any]]:
    last_error: Optional[Exception] = None
    for url in REGISTRY_MIRRORS:
        try:
            data = client.get(url, headers={"Accept": "application/json"}, timeout=60).json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        items = data.get("models") if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = list(items.values())
        if isinstance(items, list) and items:
            return [item for item in items if isinstance(item, dict)]
    raise RuntimeError(f"模型注册表拉取失败：{last_error}")


def fetch_price_table(client: httpx.Client, sku_id: str) -> Dict[str, Any]:
    try:
        resp = client.post(PRICE_TABLE_URL, json={"skuId": sku_id})
        payload = resp.json() or {}
    except Exception:
        return {}
    return payload.get("data") or {}


def _is_header_row(cells: List[Any], ncols: int) -> bool:
    """跨列的“小标题”行：只用来标注下面的分组（模型名/参数名）。

    Topaz 这类多维表的样式是：表头行只填第一格（``Proteus系列（常规）``），
    下面若干行才是真实价格。表头行不能当档位，否则会被挑成可选价。
    """
    filled = [cell for cell in cells if str(cell or "").strip()]
    return len(filled) == 1 and len(cells) >= ncols


def _table_ncols(table: Dict[str, Any]) -> int:
    """这份价格表最宽的行有几列，用来识别跨列小标题。"""
    return max((len(row.get("cells") or []) for row in table.get("rows") or []), default=0)


def tiers_from_table(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    tiers: List[Dict[str, Any]] = []
    ncols = _table_ncols(table)
    group = ""
    for row in table.get("rows") or []:
        cells = row.get("cells") or []
        if _is_header_row(cells, ncols):
            group = str(cells[0] or "").strip()
            continue
        # 有些表（火山画质增强）一行里横向排了多档价格：第 1 列是分组，
        # 第 2 列才是档位（分辨率），后面若干列各是一个价。前两格都没有
        # 金额时，按“每列一个档”拆开，否则会被当成无价行整行丢掉。
        if len(cells) >= 3 and _money(cells[1]) is None and any(_money(c) for c in cells[2:]):
            row_group = str(cells[0] or "").strip() or group
            tier_label = str(cells[1] or "").strip()
            for cell in cells[2:]:
                price = _money(cell)
                if price is None:
                    continue
                billing, label = _unit_of(cell)
                head = " · ".join(part for part in (row_group, tier_label) if part)
                tiers.append(
                    {
                        "label": head,
                        "price": price,
                        "unit": label,
                        "billing": billing,
                        "note": str(cell).strip(),
                    }
                )
            continue
        if len(cells) < 2:
            continue
        price = _money(cells[1])
        if price is None:
            continue
        billing, label = _unit_of(cells[1])
        label_text = str(cells[0] or "").strip()
        if group:
            # 分组标题给出选型（用哪个模型），行标题给出规格（几秒/多少像素）。
            label_text = f"{group} · {label_text}" if label_text else group
        tiers.append(
            {
                "label": label_text,
                "price": price,
                "unit": label,
                "billing": billing,
                "note": str(cells[2] or "").strip() if len(cells) > 2 else "",
            }
        )
    return tiers


# 个别 SKU 把计算口径写在单价列、把真实单价写进说明列，
# 所以单价列取不到金额时再退回说明列找“¥x/单位”。


def _is_input_tier(label: Any) -> bool:
    """分档行是否属于输入侧（上传素材）计费，而非这次生成的输出价。"""
    text = str(label or "")
    if "输出" in text:
        return False
    return any(word in text for word in ("输入图", "参考图", "输入视频", "输入音频"))


def _is_grid_tier(tier: Dict[str, Any]) -> bool:
    """一格一价的规格表（分辨率/时长），不是可变倍率的计价系数。

    计价系数行的说明里会写“单价×时长”“= min(...)` 这类乘法口径，
    而规格表的说明多是“按秒计费”“按张计费”这类口径说明。
    """
    note = str(tier.get("note") or "")
    if "×" in note or re.search(r"(?<!数)\d\s*\*\s*\d", note):
        return False
    return tier.get("price") is not None


def _is_anomalous_tier(tier: Dict[str, Any], peers: List[float], grid_mode: bool = False) -> bool:
    """单价比同表其他档低两个数量级以上，基本是把计价系数当成了单价。

    只看绝对阈值会把合法的低价模型（参考生视频 ¥0.001、音频 ¥0.004/秒）
    一起误杀，所以拿同表其他档位做参照；只有一个档位时不猜。

    规格表（一格一价）本身价差就大（分辨率越高越贵），不按这个规则丢档。
    """
    if grid_mode:
        return False
    price = float(tier.get("price") or 0)
    others = [p for p in peers if p > 0 and abs(p - price) > 1e-12]
    if not others:
        return False
    return price * 100 < min(others)


def _fallback_tier(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """单价列没有金额时，从说明列里挖“¥x/单位”。"""
    tiers: List[Dict[str, Any]] = []
    for row in table.get("rows") or []:
        cells = [str(cell if not isinstance(cell, dict) else cell.get("value", "")) for cell in (row.get("cells") or [])]
        if len(cells) < 2:
            continue
        price = _money(cells[1])
        if price is None:
            for text in cells[2:]:
                price = _money(text)
                if price is not None:
                    billing, label = _unit_of(text)
                    tiers.append(
                        {
                            "label": cells[0].strip(),
                            "price": price,
                            "unit": label,
                            "billing": billing,
                            "note": text.strip(),
                        }
                    )
                    break
    return tiers


def _pick_tiers(sku: Dict[str, Any], table: Dict[str, Any]) -> List[Dict[str, Any]]:
    tiers = tiers_from_table(table)
    if not tiers:
        tiers = _fallback_tier(table)
    if not tiers:
        return []
    used = [tier for tier in tiers if not _is_input_tier(tier.get("label"))]
    if not used:
        return tiers
    # 只有全部档位都像“规格表”时才按格取价；否则一律按横向比价剔除
    # 计价系数行——系数的说明也可能写得像规格（“按秒计费”）。
    grid_mode = len(used) > 1 and all(_is_grid_tier(tier) for tier in used) and not any(
        _is_anomalous_tier(tier, [float(t.get("price") or 0) for t in used]) for tier in used
    )
    if grid_mode:
        # 一格一价的规格表：每个规格都是独立定价，不能互相折算。
        return used
    peers = [float(tier.get("price") or 0) for tier in used]
    usable = [tier for tier in used if not _is_anomalous_tier(tier, peers)]
    return usable or used


def entry_from_sku(sku: Dict[str, Any], tiers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    price = sku.get("price") or {}
    simple = price.get("price")
    unit_label = price.get("unitName") or price.get("unitNameEn") or ""
    billing, label = _unit_of(unit_label or price.get("unitKey") or "")
    # “一格一价”的规格表（MULTI_DIMENSION）列表处只写一句“价格表”，
    # 没有代表单价，按真实档位算。其余情况一律以列表价为准。
    simple_text = str(price.get("simplePriceText") or "").strip()
    if simple_text in ("价格表",) or (
        simple_text and _money(simple_text) is None and "价格表" in simple_text
    ):
        simple = None
    candidates = [float(simple)] if isinstance(simple, (int, float)) and simple > 0 else []
    # 分档表里除了“输出图”，还会单列“输入图”“参考图”等输入侧计费项。
    # 它们是上传素材按张收的钱，不是这次生成本身的报价：并进最低价会把
    # 输出价压成 ¥0.018 这种假低价，挑档时也会错档，所以只留输出侧档位。
    output_tiers = [tier for tier in tiers if not _is_input_tier(tier.get("label"))]
    priced_tiers = output_tiers or tiers
    for tier in priced_tiers:
        candidates.append(tier["price"])
        if label in ("次", "") and tier.get("unit"):
            label = tier["unit"]
            billing = tier.get("billing") or billing
    if not candidates:
        return None
    low = min(candidates)
    high = max(candidates)
    entry = {
        "billing": billing,
        "unit_cny": low,
        "low_cny": low,
        "high_cny": high,
        "unit_label": label,
        "sku_id": str(sku.get("id") or ""),
        "name_cn": str(sku.get("name") or "").strip(),
        "provider": str(sku.get("sourceTypeName") or "").strip(),
        "category": str(sku.get("categoryName") or "").strip(),
        "price_text": str(price.get("simplePriceText") or "").strip()
        or (f"¥{low}/{label}" if low == high else f"¥{low}~{high}/{label}"),
        "note": "官方标价",
    }
    if tiers:
        entry["tiers"] = tiers
        # 分档说明（“单张 ≤260万像素，含 1K、1.5K 档”）是挑档的唯一依据，
        # 按“档位名 → 单价”落盘，估价时据此把区间收敛成单一档位。
        # 只收输出侧档位：输入图单价混进来会让挑档按 ¥0.018 命中。
        notes = {}
        for tier in priced_tiers:
            key = tier.get("label") or tier.get("note") or ""
            if key and key not in notes:
                notes[str(key)] = tier["price"]
        if notes and len(notes) == len(priced_tiers):
            entry["notes"] = notes
    return entry


# 国际站标准模型 SKU 用英文名（"Dola Seedream 5.5 Text To Image"），
# 与注册表 name_cn 字段的写法差异只在大小写和空格，_norm 后可直接对上。
# 不要在这里把 A 型号的价格套给 B 型号：Dola 系列和 Seedream 系列是两条
# 不同的 SKU，价格相同也不代表同一个模型，必须各取各的价目表。
MODEL_NAME_ALIASES = {}
ALIAS_SOURCE_KEYS = {}


def main() -> int:
    started = time.time()
    endpoint_pick: Dict[str, Dict[str, Any]] = {}
    unmapped: List[str] = []
    with httpx.Client(headers=WEB_HEADERS, timeout=40, follow_redirects=True) as client:
        registry = fetch_registry(client)
        by_name: Dict[str, Dict[str, Any]] = {}
        for item in registry:
            for field in ("name_cn", "display_name", "name_en"):
                key = _norm(item.get(field))
                if key:
                    by_name.setdefault(key, item)

        # 1) 国内站：中文名与注册表 name_cn 对齐，覆盖标准模型。
        skus = fetch_sku_catalog(client)
        print(f"国内站 SKU: {len(skus)} 条")
        need_table = []
        for sku in skus:
            name = str(sku.get("name") or "").strip()
            matched = by_name.get(_norm(name)) or by_name.get(_norm(_trim_model_name(name)))
            endpoint = str(matched.get("endpoint") or "").strip() if matched else ""
            endpoint = endpoint or SKU_TO_ENDPOINT.get(name, "")
            if not endpoint:
                unmapped.append(name)
                continue
            price = sku.get("price") or {}
            if not isinstance(price.get("price"), (int, float)):
                need_table.append((endpoint, sku))
                continue
            entry = entry_from_sku(sku, [])
            if entry:
                endpoint_pick.setdefault(endpoint, entry)

        print(f"  匹配注册表: {len(skus) - len(unmapped)}，列表价命中: {len(endpoint_pick)}，"
              f"需取分档价: {len(need_table)}，未匹配: {len(unmapped)}")

        def work(item):
            endpoint, sku = item
            table = fetch_price_table(client, str(sku.get("id") or ""))
            return endpoint, sku, _pick_tiers(sku, table)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for endpoint, sku, tiers in pool.map(work, need_table):
                entry = entry_from_sku(sku, tiers)
                if entry:
                    endpoint_pick.setdefault(endpoint, entry)

        # 2) 国际站：渠道版型号 + 用英文名标价的标准模型。
        ai_skus = fetch_sku_catalog(client, AI_SKU_LIST_URL)
        print(f"国际站 SKU: {len(ai_skus)} 条")
        # 2a) 英文名标准模型（如 Dola Seedream 5.5 Text To Image）走名字匹配，
        # 与国内站同一套逻辑，绝不跨型号借价。
        need_table_ai = []
        for sku in ai_skus:
            name = str(sku.get("name") or "").strip()
            matched = by_name.get(_norm(name)) or by_name.get(_norm(_trim_model_name(name)))
            endpoint = str(matched.get("endpoint") or "").strip() if matched else ""
            endpoint = endpoint or SKU_TO_ENDPOINT.get(name, "")
            if not endpoint or endpoint in endpoint_pick:
                continue
            price = sku.get("price") or {}
            if not isinstance(price.get("price"), (int, float)):
                need_table_ai.append((endpoint, sku))
                continue
            entry = entry_from_sku(sku, [])
            if entry:
                entry["note"] = "官方标价（国际站）"
                endpoint_pick.setdefault(endpoint, entry)

        def work_ai(item):
            endpoint, sku = item
            table = fetch_price_table(client, str(sku.get("id") or ""))
            return endpoint, sku, _pick_tiers(sku, table)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for endpoint, sku, tiers in pool.map(work_ai, need_table_ai):
                if endpoint in endpoint_pick:
                    continue
                entry = entry_from_sku(sku, tiers)
                if entry:
                    entry["note"] = "官方标价（国际站）"
                    endpoint_pick.setdefault(endpoint, entry)
        print(f"  国际站英文名标准模型补价: {len(need_table_ai)} 个待取分档价")

        # 2b) 渠道版型号，SKU 名本身就是 ``<产品>/<操作>/<档位>``。
        channel_keys = 0
        for sku in ai_skus:
            name = str(sku.get("name") or "").strip()
            if not SKU_NAME_RE.match(name):
                continue
            entry = entry_from_sku(sku, [])
            if not entry:
                continue
            entry["note"] = "官方标价（国际站渠道）"
            for key in app_model_ids_from_sku_name(name):
                if key and key not in endpoint_pick:
                    endpoint_pick[key] = dict(entry)
                    channel_keys += 1
        print(f"  渠道版写入: {channel_keys} 个键")

        # 3) 同一个模型的端点写法也补一份价，画布/注册表两套 ID 都能出价。
        mirrored = 0
        for model_id, endpoint in MODEL_ID_ENDPOINTS.items():
            entry = endpoint_pick.get(model_id)
            if entry and endpoint not in endpoint_pick:
                endpoint_pick[endpoint] = dict(entry)
                mirrored += 1
        print(f"  端点别名补价: {mirrored} 个")

        # 4) 同款模型不同命名（Dola-Seedream-5.5 ↔ seedream-v5.5）：对到同一个价。
        aliased = 0
        for target, source in ALIAS_SOURCE_KEYS.items():
            entry = endpoint_pick.get(source)
            if entry and target not in endpoint_pick:
                endpoint_pick[target] = dict(entry)
                aliased += 1
        for target, source in MODEL_NAME_ALIASES.items():
            entry = endpoint_pick.get(source)
            if entry and target not in endpoint_pick:
                endpoint_pick[target] = dict(entry)
                aliased += 1
        print(f"  型号别名补价: {aliased} 个")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "generated_note": "由 tools/sync_runninghub_official_prices.py 抓取 RunningHub 官网标价生成",
        "source": [SKU_LIST_URL, PRICE_TABLE_URL, AI_SKU_LIST_URL],
        "count": len(endpoint_pick),
        "models": dict(sorted(endpoint_pick.items())),
        "unmatched_skus": sorted(set(unmapped)),
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print(f"写入 {OUTPUT_PATH}：{len(endpoint_pick)} 个键，用时 {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())