# -*- coding: utf-8 -*-
"""抓取 RunningHub 官方标价，生成 ``static/runninghub/official_prices.json``。

用途：运行按钮的事前估价优先使用官方标价（管理员价格表可覆盖）。RunningHub 有两个
独立结算站点，公开接口返回的价格取决于请求头里的 ``Origin``：

* 国内站 ``https://www.runninghub.cn`` → 必须带 ``Origin: https://www.runninghub.cn``，返回人民币（CNY）
* 国际站 ``https://www.runninghub.ai`` → 必须带 ``Origin: https://www.runninghub.ai``，返回美元（USD）

若抓国际站时误带了 ``.cn`` 的 Origin（或不带 Origin），上游会把人民币数字原样返回，
导致“人民币数字套美元符号”的严重错价。因此脚本对两站分别建独立请求头，抓取各自的
``/api/sku/list`` 与 ``/api/area/price/table``，落盘到 ``models_by_site.cn`` 与
``models_by_site.ai``（同时保留顶层 ``models`` 兼容旧读取端）。

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
from typing import Any, Dict, List, Optional, Tuple

# httpx 会把 NO_PROXY 里的 "[::1]" 当成 URL 解析并抛 InvalidURL，先清掉。
for _key in ("NO_PROXY", "no_proxy"):
    os.environ.pop(_key, None)

import httpx  # noqa: E402  (必须在清理环境变量之后导入)

CN_BASE = "https://www.runninghub.cn"
CN_SKU_LIST_URL = CN_BASE + "/api/sku/list"
CN_PRICE_TABLE_URL = CN_BASE + "/api/area/price/table"

AI_BASE = "https://www.runninghub.ai"
AI_SKU_LIST_URL = AI_BASE + "/api/sku/list"
AI_PRICE_TABLE_URL = AI_BASE + "/api/area/price/table"

REGISTRY_URL = (
    "https://raw.githubusercontent.com/HM-RunningHub/ComfyUI_RH_OpenAPI/main/models_registry.json"
)
REGISTRY_MIRRORS = [REGISTRY_URL, "https://ghproxy.net/" + REGISTRY_URL]
LOCAL_REGISTRY_PATH = os.path.join("static", "runninghub", "models_registry.json")
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


def site_headers(base_url: str, lang: str = "zh-CN,zh;q=0.9") -> Dict[str, str]:
    """按目标站点构造请求头。Origin 决定 RunningHub 返回 CNY 还是 USD，绝不能混用。"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": lang,
        "Origin": base_url,
        "Referer": base_url + "/call-api",
    }


# 匹配 ¥0.23 / ￥0.23 / $0.035；排除 "+$0.16" / "+¥1.09" 这类附加项增量（Addon）。
MONEY_RE = re.compile(r"(?<![+\uff0b])[¥￥$]\s*([0-9]+(?:\.[0-9]+)?)")
ADDON_MONEY_RE = re.compile(r"[+\uff0b]\s*[¥￥$]\s*([0-9]+(?:\.[0-9]+)?)")

UNIT_HINTS = (
    ("秒", "per_second", "秒"),
    ("second", "per_second", "秒"),
    ("/sec", "per_second", "秒"),
    ("per sec", "per_second", "秒"),
    ("百万 token", "per_unit", "百万 tokens"),
    ("百万token", "per_unit", "百万 tokens"),
    ("1m token", "per_unit", "百万 tokens"),
    ("千 token", "per_unit", "千 tokens"),
    ("千token", "per_unit", "千 tokens"),
    ("token", "per_unit", "千 tokens"),
    ("张", "per_unit", "张"),
    ("图", "per_unit", "张"),
    ("piece", "per_unit", "张"),
    ("image", "per_unit", "张"),
    ("次", "per_unit", "次"),
    ("call", "per_unit", "次"),
    ("times", "per_unit", "次"),
    ("clip", "per_unit", "次"),
    ("帧", "per_unit", "帧"),
    ("frame", "per_unit", "帧"),
    ("字符", "per_unit", "千字符"),
    ("char", "per_unit", "千字符"),
)


def _norm(name: Any) -> str:
    return re.sub(r"[\s\-_/]+", "", str(name or "").strip().lower())


def _money(text: Any) -> Optional[float]:
    raw = str(text or "")
    match = MONEY_RE.search(raw)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _is_addon_cell(text: Any) -> bool:
    """形如 ``+$0.16/times`` 或 ``+¥1.09/次`` 的可选附加项增量，不能当成基础单价下界。"""
    raw = str(text or "").strip()
    return bool(raw) and bool(ADDON_MONEY_RE.search(raw)) and _money(raw) is None


def _unit_of(text: Any) -> Tuple[str, str]:
    low = str(text or "").lower()
    for hint, billing, label in UNIT_HINTS:
        if hint in low:
            return billing, label
    return "per_unit", "次"


def _is_token_priced_table(sku: Dict[str, Any], table: Dict[str, Any]) -> bool:
    """识别按百万/千 Token 计价的模型分档表。

    这类模型（如 ``gpt-image-2.6/.../stable-token``、``MiniMax-H3-Image``、
    ``seedance-2.5-...-token``）的价格表里写的是 ``$8.00 / 1M tokens`` 或
    ``¥87.5 / 百万 tokens``，属于 Token 费率而非单张/单次价格！如果直接当成
    ``per_unit`` 塞进按钮估价，运行 1 张图会荒谬地显示 ``$8.00~$30.00`` 或
    ``¥87.5``。事前不知道上游实际消耗多少 token，绝不能拿每百万 token 费率冒充单次总价。
    """
    if not isinstance(table, dict) or not table:
        return False
    sku_name = str((sku or {}).get("name") or "").lower()
    simple_text = str(table.get("simplePriceText") or "").lower()
    col_names = " ".join(str((col or {}).get("name") or "") for col in (table.get("columns") or [])).lower()
    row_texts = " ".join(
        str(cell or "")
        for row in (table.get("rows") or [])
        for cell in ((row or {}).get("cells") or [])
    ).lower()
    combined = " | ".join((sku_name, simple_text, col_names, row_texts))
    return "token" in combined


def _post_json_with_retry(client: httpx.Client, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = client.post(url, json=payload, headers=headers)
            return resp.json() or {}
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    if last_exc:
        raise last_exc
    return {}


def fetch_sku_catalog(client: httpx.Client, url: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    page = 1
    while True:
        payload = _post_json_with_retry(client, url, {"pageNum": page, "pageSize": 100}, headers)
        data = ((payload.get("data") or {}).get("page") or {})
        batch = data.get("records") or []
        records.extend(batch)
        if not data.get("hasNext") or page >= 20:
            break
        page += 1
    return records


def _trim_model_name(name: Any) -> str:
    """``f-2-klein-9b/text-to-image`` → ``klein-9b/text-to-image``。"""
    text = str(name or "").strip()
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        return text
    head = re.sub(r"^[a-z]-\d+-", "", parts[0], flags=re.I)
    head = re.sub(r"^\d+-", "", head)
    return "/".join([head, *parts[1:]]) if head != parts[0] else text


SKU_TO_ENDPOINT = {
    "f-2-klein-9b/text-to-image": "rhart-image-flux3-klein/text-to-image",
    "f-2-klein-4b/text-to-image": "rhart-image-flux3-klein/text-to-image",
    "f-2-klein-9b/edit": "rhart-image-flux3-klein/image-to-image",
    "f-2-klein-4b/edit": "rhart-image-flux3-klein/image-to-image",
}


def app_model_ids_from_sku_name(name: Any) -> List[str]:
    """官网 SKU 名 ``nano-banana/text-to-image/economy`` → 画布模型 ID 候选。"""
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
    if os.path.exists(LOCAL_REGISTRY_PATH):
        with open(LOCAL_REGISTRY_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        items = data.get("models") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            return [item for item in items if isinstance(item, dict)]
    raise RuntimeError(f"模型注册表拉取失败：{last_error}")


def fetch_price_table(client: httpx.Client, url: str, sku_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    try:
        payload = _post_json_with_retry(client, url, {"skuId": sku_id}, headers)
    except Exception:
        return {}
    return payload.get("data") or {}


def _is_header_row(cells: List[Any], ncols: int) -> bool:
    filled = [cell for cell in cells if str(cell or "").strip()]
    return len(filled) == 1 and len(cells) >= ncols


def _table_ncols(table: Dict[str, Any]) -> int:
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
        if len(cells) >= 3 and _money(cells[1]) is None and any(_money(c) for c in cells[2:]):
            row_group = str(cells[0] or "").strip() or group
            tier_label = str(cells[1] or "").strip()
            for cell in cells[2:]:
                if _is_addon_cell(cell):
                    continue
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
        if _is_addon_cell(cells[1]):
            continue
        price = _money(cells[1])
        if price is None:
            continue
        billing, label = _unit_of(cells[1])
        label_text = str(cells[0] or "").strip()
        if group:
            label_text = f"{group} · {label_text}" if label_text else group
        note_text = str(cells[2] or "").strip() if len(cells) > 2 else ""
        tiers.append(
            {
                "label": label_text,
                "price": price,
                "unit": label,
                "billing": billing,
                "note": note_text,
            }
        )
    return tiers


def _is_input_tier(label: Any) -> bool:
    """分档行是否属于输入侧（上传素材）计费，而非这次生成的输出价（兼容中英文）。"""
    text = str(label or "").strip()
    low = text.lower()
    if "输出" in text or "output" in low:
        return False
    if any(word in text for word in ("输入图", "参考图", "输入视频", "输入音频")):
        return True
    return any(
        word in low
        for word in ("input image", "reference image", "input video", "input audio")
    )


def _is_grid_tier(tier: Dict[str, Any]) -> bool:
    note = str(tier.get("note") or "")
    if "×" in note or re.search(r"(?<!数)\d\s*\*\s*\d", note):
        return False
    return tier.get("price") is not None


def _is_anomalous_tier(tier: Dict[str, Any], peers: List[float], grid_mode: bool = False) -> bool:
    if grid_mode:
        return False
    price = float(tier.get("price") or 0)
    others = [p for p in peers if p > 0 and abs(p - price) > 1e-12]
    if not others:
        return False
    return price * 100 < min(others)


def _fallback_tier(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """单价列没有金额时，从说明列里挖“¥x/单位”或“$x/unit”。"""
    tiers: List[Dict[str, Any]] = []
    for row in table.get("rows") or []:
        cells = [
            str(cell if not isinstance(cell, dict) else cell.get("value", ""))
            for cell in (row.get("cells") or [])
        ]
        if len(cells) < 2:
            continue
        price = _money(cells[1])
        if price is None:
            for text in cells[2:]:
                if _is_addon_cell(text):
                    continue
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
    if _is_token_priced_table(sku, table):
        # 按百万/千 Token 计价的模型无法在运行前确定 token 用量，不能把费率当单次价。
        return []
    tiers = tiers_from_table(table)
    if not tiers:
        tiers = _fallback_tier(table)
    if not tiers:
        return []
    used = [tier for tier in tiers if not _is_input_tier(tier.get("label"))]
    if not used:
        return tiers
    grid_mode = len(used) > 1 and all(_is_grid_tier(tier) for tier in used) and not any(
        _is_anomalous_tier(tier, [float(t.get("price") or 0) for t in used]) for tier in used
    )
    if grid_mode:
        return used
    peers = [float(tier.get("price") or 0) for tier in used]
    usable = [tier for tier in used if not _is_anomalous_tier(tier, peers)]
    return usable or used


def entry_from_sku(
    sku: Dict[str, Any],
    tiers: List[Dict[str, Any]],
    currency: str = "CNY",
    site: str = "cn",
) -> Optional[Dict[str, Any]]:
    price = sku.get("price") or {}
    simple = price.get("price")
    unit_source = price.get("unitName") or price.get("unitNameEn") or price.get("unitKey") or ""
    if "token" in str(unit_source).lower():
        return None
    billing, label = _unit_of(unit_source)
    price_mode = str(price.get("priceMode") or "").strip().upper()
    if price_mode == "FIXED" and billing == "per_unit" and label == "次":
        billing = "fixed"

    simple_text = str(price.get("simplePriceText") or "").strip()
    if simple_text.lower() in ("价格表", "pricing", "price table") or (
        simple_text and _money(simple_text) is None and ("价格表" in simple_text or "billed by" in simple_text.lower())
    ):
        simple = None

    candidates = [float(simple)] if isinstance(simple, (int, float)) and simple > 0 else []
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
    cur = "USD" if str(currency).upper() == "USD" else "CNY"
    sign = "$" if cur == "USD" else "¥"
    price_text = f"{sign}{low}/{label}" if low == high else f"{sign}{low}~{high}/{label}"
    note_label = "官方标价（国际站）" if site == "ai" else "官方标价"
    entry: Dict[str, Any] = {
        "billing": billing,
        "unit_cny": low,
        "low_cny": low,
        "high_cny": high,
        "unit_label": label,
        "currency": cur,
        "site": site,
        "sku_id": str(sku.get("id") or ""),
        "price_text": price_text,
        "note": note_label,
    }
    if tiers:
        # 只落盘“档位名 → 单价”，不重复存整张 tiers 明细：估价侧（parse_tier_notes）
        # 只需这一层就能把区间收敛成单档，明细会把价格表文件撑大 3 倍。
        # tiers 仍保留在内存 entry 里供测试与调试，写入文件前由 _slim_entry 剔除。
        entry["tiers"] = tiers
        notes: Dict[str, float] = {}
        for tier in priced_tiers:
            # 优先保持 label 纯净（如 "输出图 1.5K" / "Output image 2K"）；
            # 只有 label 本身为空或相同名称冲突时才补入说明列文字。
            raw_label = str(tier.get("label") or "").strip()
            raw_note = str(tier.get("note") or "").strip()
            key = raw_label or raw_note
            if key and key in notes and raw_note and raw_note not in key:
                key = f"{raw_label} ({raw_note})" if raw_label else raw_note
            if key and key not in notes:
                notes[key] = tier["price"]
        if notes and len(notes) == len(priced_tiers):
            entry["notes"] = notes
    return entry


def _build_registry_maps(registry: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    for item in registry:
        for field in ("endpoint", "name_cn", "display_name", "name_en"):
            key = _norm(item.get(field))
            if key:
                by_name.setdefault(key, item)
    return by_name


def _slim_entry(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """落盘前剔除冗余的 tiers 明细（notes 已足够收敛档位价）。"""
    if not isinstance(entry, dict):
        return None
    slim = {k: v for k, v in entry.items() if k != "tiers"}
    return slim


def _collect_site_prices(
    client: httpx.Client,
    site: str,
    sku_url: str,
    table_url: str,
    headers: Dict[str, str],
    currency: str,
    by_name: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """抓取单个站点的完整价格字典（所有金额保持该站点的原生结算币种）。"""
    picked: Dict[str, Dict[str, Any]] = {}
    unmapped: List[str] = []
    skus = fetch_sku_catalog(client, sku_url, headers)
    print(f"[{site.upper()}] SKU 总数: {len(skus)} 条")

    need_table: List[Tuple[List[str], Dict[str, Any]]] = []
    for sku in skus:
        name = str(sku.get("name") or "").strip()
        target_keys: List[str] = []

        # 1) 渠道版命名（如 nano-banana/text-to-image/economy）
        if SKU_NAME_RE.match(name):
            for key in app_model_ids_from_sku_name(name):
                if key and key not in target_keys:
                    target_keys.append(key)

        # 2) 标准模型名 / 端点名匹配注册表
        matched = by_name.get(_norm(name)) or by_name.get(_norm(_trim_model_name(name)))
        endpoint = str(matched.get("endpoint") or "").strip() if matched else ""
        endpoint = endpoint or SKU_TO_ENDPOINT.get(name, "")
        if endpoint and endpoint not in target_keys:
            target_keys.append(endpoint)
        if matched:
            name_en = str(matched.get("name_en") or "").strip()
            if name_en and "/" in name_en and "deprecated" not in name_en.lower() and name_en not in target_keys:
                target_keys.append(name_en)

        # 3) SKU 名本身已经是标准路径形式（含 / 且无空格，如 google/veo3.1-fast/...）
        if "/" in name and " " not in name and name not in target_keys:
            target_keys.append(name)

        if not target_keys:
            unmapped.append(name)
            continue

        price = sku.get("price") or {}
        if not isinstance(price.get("price"), (int, float)):
            need_table.append((target_keys, sku))
            continue

        entry = entry_from_sku(sku, [], currency=currency, site=site)
        if entry:
            slim = _slim_entry(entry)
            for key in target_keys:
                picked.setdefault(key, dict(slim))

    def work(item: Tuple[List[str], Dict[str, Any]]):
        keys, sku = item
        table = fetch_price_table(client, table_url, str(sku.get("id") or ""), headers)
        return keys, sku, _pick_tiers(sku, table)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for keys, sku, tiers in pool.map(work, need_table):
            entry = entry_from_sku(sku, tiers, currency=currency, site=site)
            if entry:
                slim = _slim_entry(entry)
                for key in keys:
                    picked.setdefault(key, dict(slim))

    # 补齐模型别名 ↔ 实际调用端点双向映射，确保无论前端传哪种 ID 都能命中同站点原生价
    for model_id, endpoint in MODEL_ID_ENDPOINTS.items():
        if model_id in picked and endpoint not in picked:
            picked[endpoint] = dict(picked[model_id])
        elif endpoint in picked and model_id not in picked:
            picked[model_id] = dict(picked[endpoint])

    print(
        f"  [{site.upper()}] 落盘键数: {len(picked)}，分档查询: {len(need_table)}，未映射: {len(unmapped)}"
    )
    return dict(sorted(picked.items())), sorted(set(unmapped))


def main() -> int:
    started = time.time()
    cn_headers = site_headers(CN_BASE, "zh-CN,zh;q=0.9")
    ai_headers = site_headers(AI_BASE, "en-US,en;q=0.9")

    with httpx.Client(timeout=40, follow_redirects=True) as client:
        registry = fetch_registry(client)
        by_name = _build_registry_maps(registry)

        cn_models, cn_unmapped = _collect_site_prices(
            client, "cn", CN_SKU_LIST_URL, CN_PRICE_TABLE_URL, cn_headers, "CNY", by_name
        )
        ai_models, ai_unmapped = _collect_site_prices(
            client, "ai", AI_SKU_LIST_URL, AI_PRICE_TABLE_URL, ai_headers, "USD", by_name
        )

    # 顶层 models 保留合并视图以兼容旧结构（国内站 CNY + 国际站独有条目 USD）；
    # 新版估价优先读取 models_by_site[site]，直接拿到对应站点的 100% 原生定价。
    merged_models: Dict[str, Dict[str, Any]] = dict(cn_models)
    for key, entry in ai_models.items():
        if key not in merged_models:
            merged_models[key] = dict(entry)

    site_currencies = {"cn": "CNY", "ai": "USD"}
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    header = {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "generated_note": "由 tools/sync_runninghub_official_prices.py 按站点独立 Origin 抓取 RunningHub 官网原生标价生成",
        "source": [CN_SKU_LIST_URL, CN_PRICE_TABLE_URL, AI_SKU_LIST_URL, AI_PRICE_TABLE_URL],
        "site_currencies": site_currencies,
        "counts_by_site": {"cn": len(cn_models), "ai": len(ai_models)},
        "count": len(merged_models),
        "unmatched_skus": sorted(set(cn_unmapped) | set(ai_unmapped)),
    }
    # 手工序列化：顶层字段保持可读缩进，但每条模型只占一行——标准 indent 会把
    # 几百个分档 notes 拆成数万行，让 400KB 的价目表几乎没法 review diff。
    def dump_group(group: Dict[str, Any], indent: str) -> List[str]:
        return [
            indent
            + json.dumps(k, ensure_ascii=False)
            + ": "
            + json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            for k, v in group.items()
        ]

    parts: List[str] = []
    for key, value in header.items():
        parts.append(" " + json.dumps(key, ensure_ascii=False) + ": " + json.dumps(value, ensure_ascii=False))
    site_lines: List[str] = []
    for site_name, group in (("cn", cn_models), ("ai", ai_models)):
        body_lines = dump_group(group, "    ")
        site_lines.append(
            "  "
            + json.dumps(site_name, ensure_ascii=False)
            + ": {\n"
            + ",\n".join(body_lines)
            + "\n  }"
        )
    merged_lines = dump_group(dict(sorted(merged_models.items())), "  ")
    parts.append(" \"models_by_site\": {\n" + ",\n".join(site_lines) + "\n }")
    parts.append(" \"models\": {\n" + ",\n".join(merged_lines) + "\n }")
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("{\n" + ",\n".join(parts) + "\n}\n")
    print(
        f"写入 {OUTPUT_PATH}：CN={len(cn_models)} 条(CNY)，AI={len(ai_models)} 条(USD)，"
        f"合并={len(merged_models)} 条，用时 {time.time() - started:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
