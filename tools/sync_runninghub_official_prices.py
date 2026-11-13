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
REGISTRY_URL = (
    "https://raw.githubusercontent.com/HM-RunningHub/ComfyUI_RH_OpenAPI/main/models_registry.json"
)
OUTPUT_PATH = os.path.join("static", "runninghub", "official_prices.json")

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


def _unit_of(text: Any) -> tuple:
    low = str(text or "").lower()
    for hint, billing, label in UNIT_HINTS:
        if hint in low:
            return billing, label
    return "per_unit", "次"


def fetch_sku_catalog(client: httpx.Client) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = client.post(SKU_LIST_URL, json={"pageNum": page, "pageSize": 100})
        payload = resp.json() or {}
        data = ((payload.get("data") or {}).get("page") or {})
        batch = data.get("records") or []
        records.extend(batch)
        if not data.get("hasNext") or page >= 20:
            break
        page += 1
    return records


def fetch_price_table(client: httpx.Client, sku_id: str) -> Dict[str, Any]:
    try:
        resp = client.post(PRICE_TABLE_URL, json={"skuId": sku_id})
        payload = resp.json() or {}
    except Exception:
        return {}
    return payload.get("data") or {}


def tiers_from_table(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    tiers: List[Dict[str, Any]] = []
    for row in table.get("rows") or []:
        cells = row.get("cells") or []
        if len(cells) < 2:
            continue
        price = _money(cells[1])
        if price is None:
            continue
        billing, label = _unit_of(cells[1])
        tiers.append(
            {
                "label": str(cells[0] or "").strip(),
                "price": price,
                "unit": label,
                "billing": billing,
                "note": str(cells[2] or "").strip() if len(cells) > 2 else "",
            }
        )
    return tiers


def entry_from_sku(sku: Dict[str, Any], tiers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    price = sku.get("price") or {}
    simple = price.get("price")
    unit_label = price.get("unitName") or price.get("unitNameEn") or ""
    billing, label = _unit_of(unit_label or price.get("unitKey") or "")
    candidates = [float(simple)] if isinstance(simple, (int, float)) and simple > 0 else []
    for tier in tiers:
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
    return entry


def main() -> int:
    started = time.time()
    with httpx.Client(headers=WEB_HEADERS, timeout=40, follow_redirects=True) as client:
        skus = fetch_sku_catalog(client)
        print(f"SKU 目录: {len(skus)} 条")

        registry = client.get(REGISTRY_URL, headers={"Accept": "application/json"}).json()
        by_name = {}
        endpoint_pick: Dict[str, Dict[str, Any]] = {}
        for item in registry:
            for field in ("name_cn", "display_name", "name_en"):
                key = _norm(item.get(field))
                if key:
                    by_name.setdefault(key, item)

        need_table = []
        entries: Dict[str, Dict[str, Any]] = {}
        unmapped: List[str] = []
        for sku in skus:
            name = str(sku.get("name") or "").strip()
            matched = by_name.get(_norm(name))
            if not matched:
                unmapped.append(name)
                continue
            endpoint = str(matched.get("endpoint") or "").strip()
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

        print(f"名称匹配到注册表: {len(skus) - len(unmapped)}，直接用列表价: {len(endpoint_pick)}，"
              f"需要分档价格表: {len(need_table)}，未匹配: {len(unmapped)}")

        def work(item):
            endpoint, sku = item
            table = fetch_price_table(client, str(sku.get("id") or ""))
            return endpoint, sku, tiers_from_table(table)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for endpoint, sku, tiers in pool.map(work, need_table):
                entry = entry_from_sku(sku, tiers)
                if entry:
                    endpoint_pick.setdefault(endpoint, entry)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "generated_note": "由 tools/sync_runninghub_official_prices.py 抓取 RunningHub 官网标价生成",
        "source": [SKU_LIST_URL, PRICE_TABLE_URL],
        "count": len(endpoint_pick),
        "models": dict(sorted(endpoint_pick.items())),
        "unmatched_skus": sorted(set(unmapped)),
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print(f"写入 {OUTPUT_PATH}：{len(endpoint_pick)} 个端点，用时 {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())