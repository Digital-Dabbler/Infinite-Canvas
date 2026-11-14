# -*- coding: utf-8 -*-
"""RunningHub 事前估价体系的纯逻辑层。

本模块只做计算与校验，不碰网络、不碰磁盘，方便离线单元测试。文件读写、锁、
HTTP 路由都在 main.py 里，复用既有 helper。

设计背景（2026-11-13 调研确认）：RunningHub 没有“先询价再运行”的官方接口，
每次调用的准确金额只在任务结束后随 ``usage.consumeMoney`` 返回。因此这里用三
条可落地的路线，按可靠性从高到低组成一个估价闸门：

1. 实测回流（history）：同一配置组、同一端点/应用的历史实扣金额。样本足够多
   （>=HISTORY_RELIABLE_SAMPLES）时取中位数，区间取四分位；样本少时如实标注
   “样本较少”。这是最贴近真实扣费的来源。
2. 官方单价表（table / manual）：管理员在价格表里录入的固定单价，
   乘本次数量（图片张数 / 视频秒数）得到预估。规则固定的模型估得准。
3. 兜底单价（fallback）：按 GPU 分钟计费的粗略兜底，仅在配置了兜底单价时生效。

三样都没有时返回 unknown —— 诚实显示“费用待定”，绝不编造数字。
"""

from typing import Any, Dict, List, Optional, Tuple

import re

PRICING_SCHEMA_VERSION = 1

# 历史样本达到这个数量才给 high 置信；少于它但 >=1 给 medium（样本较少）。
HISTORY_RELIABLE_SAMPLES = 3
# 每个 key 保留的实扣样本上限（FIFO），只存金额 > 0 的记录。
HISTORY_SAMPLE_CAP = 30

KINDS = {"model", "app", "workflow"}
BILLING_MODES = {"per_unit", "per_second", "per_minute", "fixed"}

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_UNKNOWN = "unknown"

SOURCE_HISTORY = "history"
SOURCE_HISTORY_SPARSE = "history_sparse"
SOURCE_TABLE = "table"
SOURCE_MANUAL = "manual"
SOURCE_OFFICIAL = "official"
SOURCE_FALLBACK = "fallback"
SOURCE_UNKNOWN = "unknown"

# 单价合理区间（人民币元）。超过上限或 ≤0 视为非法录入。
UNIT_PRICE_MIN = 0.0001
UNIT_PRICE_MAX = 1000.0

# RunningHub 两个站点各自用一种币种结算，绝不混用：
# 国内站 runninghub.cn 用人民币，国际站 runninghub.ai 用美元。
SITE_CURRENCIES = {"cn": "CNY", "ai": "USD"}
DEFAULT_CURRENCY = "CNY"
SUPPORTED_CURRENCIES = ("CNY", "USD")


def site_key(site: Any) -> str:
    """把站点标识（cn/ai，或含域名的 URL）归一成 ``'cn'`` 或 ``'ai'``。"""
    text = str(site or "").strip().lower()
    if "runninghub.ai" in text or text == "ai":
        return "ai"
    if "runninghub.cn" in text or text == "cn":
        return "cn"
    key = text.strip("/").split(".")[0]
    return key if key in SITE_CURRENCIES else "cn"


def site_currency(site: Any) -> str:
    """把站点标识（cn/ai，或含域名的字符串）映射成结算币种。"""
    return SITE_CURRENCIES.get(site_key(site), DEFAULT_CURRENCY)


def normalize_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in SUPPORTED_CURRENCIES else DEFAULT_CURRENCY


def _as_money(value: Any) -> Optional[float]:
    """把任意输入解析成一个合法金额，非法返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if number < 0:
        return None
    return round(number, 6)


def _quantile(sorted_values: List[float], fraction: float) -> float:
    """线性插值分位数；sorted_values 需已升序且非空。"""
    count = len(sorted_values)
    if count == 1:
        return sorted_values[0]
    position = (count - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, count - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def median_of(values: List[float]) -> float:
    clean = sorted(float(v) for v in values)
    if not clean:
        return 0.0
    return _quantile(clean, 0.5)


def _result(amount, low, high, confidence, source, samples=0, note="", currency=None, native=None):
    return {
        "amount": None if amount is None else round(float(amount), 4),
        "low": None if low is None else round(float(low), 4),
        "high": None if high is None else round(float(high), 4),
        "confidence": confidence,
        "source": source,
        "samples": int(samples or 0),
        "note": note,
        # currency 是这条价格自身记账用的币种（官价条目 = 该站点币种，历史实扣 = 人民币），
        # native 是与 currency 同币种的金额；amount/low/high 留给上层换算后的人民币值。
        "currency": currency,
        "native": native,
    }


def _money_triplet(amount, low, high) -> Dict[str, Any]:
    def clean(value):
        return None if value is None else round(float(value), 4)

    return {"amount": clean(amount), "low": clean(low), "high": clean(high)}


def _empty_result():
    return _result(None, None, None, CONFIDENCE_UNKNOWN, SOURCE_UNKNOWN, 0, "")


def empty_estimate() -> Dict[str, Any]:
    """对外公开的 unknown 结果构造器，供路由在参数非法时返回同构结果。"""
    return _empty_result()


def _history_result(stats_entry: Optional[Dict[str, Any]], default_currency: str = DEFAULT_CURRENCY):
    """基于历史实扣样本给结论；无有效样本返回 None 交给下一档。

    实扣来自 RunningHub 的 usage.consumeMoney，币种与该配置组接入站点的钱包一致
    （.ai 站点扣美元 USD，.cn 站点扣人民币 CNY）。早期未记录 currency 字段的样本
    按当前配置组的结算币种 ``default_currency`` 解释，避免把美元实扣误当人民币二次除汇率。
    """
    if not isinstance(stats_entry, dict):
        return None
    samples = [_as_money(item) for item in (stats_entry.get("samples") or [])]
    samples = [s for s in samples if s is not None and s > 0]
    if not samples:
        return None
    entry_currency = normalize_currency(stats_entry.get("currency") or default_currency)
    ordered = sorted(samples)
    mid = median_of(ordered)
    if len(samples) >= HISTORY_RELIABLE_SAMPLES:
        low = ordered[0] if len(samples) < 4 else _quantile(ordered, 0.25)
        high = ordered[-1] if len(samples) < 4 else _quantile(ordered, 0.75)
        result = _result(mid, low, high, CONFIDENCE_HIGH, SOURCE_HISTORY, len(samples))
    else:
        result = _result(
            mid, ordered[0], ordered[-1], CONFIDENCE_MEDIUM, SOURCE_HISTORY_SPARSE, len(samples)
        )
    result["currency"] = entry_currency
    result["native"] = _money_triplet(mid, result["low"], result["high"])
    return result


def _resolve_quantity(quantity: Any, default_quantity: Any) -> float:
    """本次数量：优先调用方传入，回退表内默认，再回退 1。"""
    for candidate in (quantity, default_quantity, 1):
        try:
            number = float(candidate)
        except (TypeError, ValueError):
            continue
        if number and number > 0:
            return number
    return 1.0


def _normalize_models(models: Any) -> Dict[str, Dict[str, Any]]:
    """价格表里的 models 值必须是字典；键被重复哈希过（如 "ep" 对 "ep/usd"）时，
    "ep" 查不到条目却不能直接返回 None——那是表结构问题，安静返回 None 会让
    整个估价退化成"费用待定"。这里统一做一次规整，键和值都按需修正。"""
    if not isinstance(models, dict):
        return {}
    clean: Dict[str, Dict[str, Any]] = {}
    for key, entry in models.items():
        name = str(key or "").strip()
        if name and isinstance(entry, dict):
            clean[name] = entry
    return clean


def _table_result(table: Dict[str, Any], kind: str, entry_id: str, quantity: Any, default_currency: str = DEFAULT_CURRENCY):
    """固定单价 × 数量；无表项或单价非法返回 None。"""
    if not isinstance(table, dict):
        return None
    entry_id = str(entry_id or "").strip()
    if kind == "model":
        entry = _normalize_models(table.get("models")).get(entry_id)
    elif kind == "app":
        entry = _normalize_models(table.get("apps")).get(entry_id)
    else:
        entry = _normalize_models(table.get("workflows")).get(entry_id)
    if not isinstance(entry, dict):
        return None
    # 表内金额优先按条目显式标注的 currency 记账（官方价带站点币种，内置种子带 CNY）；
    # 管理员未标注 currency 的手填项跟随当前配置组结算币种 default_currency。
    table_currency = entry.get("currency") or table.get("currency") or default_currency
    entry_currency = normalize_currency(table_currency)
    tiers = parse_tier_notes(entry.get("notes"))
    # 应用/工作流走 manual_cny（手工单次总价），模型走 unit_cny × 数量。
    if "manual_cny" in entry and entry.get("manual_cny") is not None:
        manual = _as_money(entry.get("manual_cny"))
        if manual is not None and manual > 0:
            result = _result(manual, manual, manual, CONFIDENCE_MEDIUM, SOURCE_MANUAL)
            result["currency"] = entry_currency
            result["native"] = _money_triplet(manual, manual, manual)
            return result
    unit = _as_money(entry.get("unit_cny"))
    if unit is None or unit <= 0:
        return None
    billing = str(entry.get("billing") or "per_unit").strip().lower()
    factor = 1.0 if billing == "fixed" else _resolve_quantity(quantity, entry.get("default_quantity"))
    amount = unit * factor
    # 官方分档价（1.5K/2K/3K 不同单价）天然是一段区间：最低档到最高档。
    low = _as_money(entry.get("low_cny"))
    high = _as_money(entry.get("high_cny"))
    low_amount = low * factor if (low is not None and low > 0) else amount
    high_amount = high * factor if (high is not None and high > 0) else amount
    if low_amount > high_amount:
        low_amount, high_amount = high_amount, low_amount
    source = str(entry.get("source") or "").strip().lower()
    if source not in {SOURCE_TABLE, SOURCE_MANUAL, SOURCE_OFFICIAL}:
        source = SOURCE_TABLE
    confidence = str(entry.get("confidence") or "").strip().lower()
    if confidence not in {CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW}:
        confidence = CONFIDENCE_HIGH
    result = _result(
        amount,
        low_amount,
        high_amount,
        confidence,
        source,
        0,
        str(entry.get("note") or "")[:120],
        currency=entry_currency,
        native=_money_triplet(amount, low_amount, high_amount),
    )
    if tiers:
        # 档位价由 fit_resolution 按本次分辨率收敛成单一档位。
        result["_tiers"] = tiers
    return result


def convert_estimate(result: Optional[Dict[str, Any]], rates: Optional[Dict[str, Any]], target_currency: str) -> Dict[str, Any]:
    """把估价的 amount/low/high 换算到目标币种，同时保留原币种金额与原币种金额。

    ``rates`` 形如 ``{"CNY": 1.0, "USD": 7.1}``：每个币种 1 单位值多少人民币，
    人民币恒为 1。目标币种没有汇率时原样返回并标 ``rateMissing``，让上层能提示。
    """
    if not isinstance(result, dict):
        return result or {}
    currency = str(result.get("currency") or "").strip().upper()
    target = str(target_currency or "").strip().upper()
    native = result.get("native") if isinstance(result.get("native"), dict) else None
    if native is None:
        native = {
            "amount": result.get("amount"),
            "low": result.get("low"),
            "high": result.get("high"),
        }
    if not currency or not target or currency == target:
        payload = dict(result)
        payload["currency"] = target or currency
        payload["native"] = native
        return payload
    table = rates if isinstance(rates, dict) else {}
    payload = dict(result)
    payload["native"] = native
    source_rate = _as_money(table.get(currency))
    target_rate = _as_money(table.get(target))
    if not source_rate or not target_rate or source_rate <= 0 or target_rate <= 0:
        # 汇率不可用：宁可显示原币种，也不能把美元数字当人民币展示。
        payload["currency"] = currency
        payload["rateMissing"] = True
        return payload
    for field in ("amount", "low", "high"):
        value = _as_money(native.get(field))
        payload[field] = None if value is None else round(value * source_rate / target_rate, 4)
    payload["currency"] = target
    payload["rate"] = round(source_rate / target_rate, 6)
    return payload


def currency_symbol(currency: Any) -> str:
    """估价文案用的货币符号：人民币 ¥、美元 $。"""
    return "$" if normalize_currency(currency) == "USD" else "¥"


def _fallback_result(table: Dict[str, Any], kind: str, quantity: Any, default_currency: str = DEFAULT_CURRENCY):
    """应用/工作流的 GPU 分钟兜底单价；未配置或模型类返回 None。"""
    if kind == "model" or not isinstance(table, dict):
        return None
    fallback = table.get("fallback")
    if not isinstance(fallback, dict):
        return None
    unit = _as_money(fallback.get("gpu_minute_cny"))
    if unit is None or unit <= 0:
        return None
    minutes = _resolve_quantity(quantity, 1)
    amount = unit * minutes
    fb_currency = normalize_currency(fallback.get("currency") or table.get("currency") or default_currency)
    result = _result(amount, amount, amount, CONFIDENCE_LOW, SOURCE_FALLBACK)
    result["currency"] = fb_currency
    result["native"] = _money_triplet(amount, amount, amount)
    return result


def estimate_price(
    table: Optional[Dict[str, Any]],
    stats_entry: Optional[Dict[str, Any]],
    kind: str,
    entry_id: str,
    quantity: Any = None,
    default_currency: str = DEFAULT_CURRENCY,
) -> Dict[str, Any]:
    """按三档优先级给出本次调用的预估金额与置信度。

    返回 ``{amount, low, high, confidence, source, samples, note, currency, native}``。
    amount/low/high 与 currency 同币种，native 保留同一组原币种金额，供跨币种兜底时换算。
    """
    kind = str(kind or "").strip().lower()
    if kind not in KINDS or not str(entry_id or "").strip():
        return _empty_result()

    history = _history_result(stats_entry, default_currency=default_currency)
    if history:
        return history

    priced = _table_result(table or {}, kind, entry_id, quantity, default_currency=default_currency)
    if priced:
        return priced

    fallback = _fallback_result(table or {}, kind, quantity, default_currency=default_currency)
    if fallback:
        return fallback

    return _empty_result()


def apply_observed(
    stats_entry: Optional[Dict[str, Any]],
    amount: Any,
    observed_at: float,
    currency: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """把一次实扣金额并入历史统计并返回更新后的条目（不改入参）。

    金额 <=0 或非法返回 None，表示不应记录（失败任务实扣为空/0）。
    配置组若切换了结算站点（导致新样本 currency 与旧样本不一致），则清空异币种旧样本，
    绝不把人民币样本与美元样本混算中位数。
    """
    money = _as_money(amount)
    if money is None or money <= 0:
        return None
    entry = dict(stats_entry or {})
    normalized_cur = normalize_currency(currency) if currency else None
    existing_cur = normalize_currency(entry.get("currency")) if entry.get("currency") else None
    if normalized_cur and existing_cur and normalized_cur != existing_cur:
        samples: List[float] = []
    else:
        samples = [_as_money(item) for item in (entry.get("samples") or [])]
        samples = [s for s in samples if s is not None and s > 0]
    samples.append(money)
    if len(samples) > HISTORY_SAMPLE_CAP:
        samples = samples[-HISTORY_SAMPLE_CAP:]
    entry["samples"] = samples
    entry["count"] = len(samples)
    if normalized_cur:
        entry["currency"] = normalized_cur
    try:
        entry["last_at"] = float(observed_at)
    except (TypeError, ValueError):
        entry["last_at"] = 0.0
    ordered = sorted(samples)
    entry["median"] = round(median_of(ordered), 6)
    if len(ordered) >= 4:
        entry["p25"] = round(_quantile(ordered, 0.25), 6)
        entry["p75"] = round(_quantile(ordered, 0.75), 6)
    else:
        entry["p25"] = ordered[0]
        entry["p75"] = ordered[-1]
    return entry


def stats_key(profile_id: str, kind: str, entry_id: str) -> str:
    """历史统计的隔离键：配置组 | 类型 | 端点/应用 ID。

    同一个端点在不同配置组可能对应不同密钥与价格，绝不能跨组串用样本。
    """
    profile = str(profile_id or "").strip().lower() or "-"
    kind = str(kind or "").strip().lower() or "-"
    entry = str(entry_id or "").strip()
    return "::".join((profile, kind, entry))


# 画布模型 ID 的档位后缀 → 官网 SKU 名里的档位段。
# 渠道低价版在官网目录（国际站）记作 /economy，官方稳定版记作 /stable。
MODEL_TIER_SUFFIXES = (
    ("-channel-low-price", "economy"),
    ("-official-stable", "stable"),
)


def model_price_keys(model_id: str) -> List[str]:
    """把一个画布模型 ID 展开成价格表候选键，原始 ID 始终排在最前。

    RunningHub 的模型 ID 是 ``<产品>/<操作>-<档位后缀>``，例如
    ``nano-banana/text-to-image-channel-low-price``；官网价目表用的是
    ``<产品>/<操作>/<档位>``，例如 ``nano-banana/text-to-image/economy``。
    这里补齐两种写法，调用方按顺序命中第一个有价的键。
    """
    raw = str(model_id or "").strip().strip("/")
    if not raw:
        return []
    keys = [raw]
    for suffix, tier in MODEL_TIER_SUFFIXES:
        if not raw.endswith(suffix):
            continue
        base = raw[: -len(suffix)].strip("/")
        if base:
            keys.append(f"{base}/{tier}")
            keys.append(f"{base}/{'stable' if tier == 'economy' else 'economy'}")
        break
    ordered: List[str] = []
    for key in keys:
        if key and key not in ordered:
            ordered.append(key)
    return ordered


def _valid_unit(value: Any) -> Optional[float]:
    money = _as_money(value)
    if money is None:
        return None
    if money < UNIT_PRICE_MIN or money > UNIT_PRICE_MAX:
        return None
    return money


def normalize_price_table(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """校验并清洗管理员提交的价格表，返回 (clean_table, errors)。

    errors 非空时调用方应拒绝写入。合法字段之外的多余键丢弃；数值越界记错。
    """
    errors: List[str] = []
    source = raw if isinstance(raw, dict) else {}

    def clean_models():
        result = {}
        models = source.get("models")
        if not isinstance(models, dict):
            return result
        for key, entry in models.items():
            key = str(key or "").strip()
            if not key or not isinstance(entry, dict):
                continue
            billing = str(entry.get("billing") or "per_unit").strip().lower()
            if billing not in BILLING_MODES:
                errors.append(f"模型 {key} 的计费方式 {billing!r} 不支持")
                continue
            unit = _valid_unit(entry.get("unit_cny"))
            if unit is None:
                errors.append(f"模型 {key} 的单价无效或超出范围")
                continue
            clean = {
                "billing": billing,
                "unit_cny": unit,
                "unit_quantity_param": str(entry.get("unit_quantity_param") or "").strip(),
                "default_quantity": _as_money(entry.get("default_quantity")) or 1,
            }
            if entry.get("currency"):
                clean["currency"] = normalize_currency(entry.get("currency"))
            note = str(entry.get("note") or "").strip()[:120]
            if note:
                clean["note"] = note
            result[key] = clean
        return result

    def clean_apps(list_key: str):
        result = {}
        block = source.get(list_key)
        if not isinstance(block, dict):
            return result
        for key, entry in block.items():
            key = str(key or "").strip()
            if not key or not isinstance(entry, dict):
                continue
            manual = _valid_unit(entry.get("manual_cny"))
            if manual is None:
                errors.append(f"{list_key} {key} 的手工价无效或超出范围")
                continue
            result[key] = {"manual_cny": manual}
            if entry.get("currency"):
                result[key]["currency"] = normalize_currency(entry.get("currency"))
            note = str(entry.get("note") or "").strip()[:120]
            if note:
                result[key]["note"] = note
        return result

    table = {
        "schema_version": PRICING_SCHEMA_VERSION,
        "updated_at": _as_money(source.get("updated_at")) or 0,
        "models": clean_models(),
        "apps": clean_apps("apps"),
        "workflows": clean_apps("workflows"),
    }
    fallback = source.get("fallback") if isinstance(source.get("fallback"), dict) else {}
    gpu = _valid_unit(fallback.get("gpu_minute_cny"))
    table["fallback"] = {"gpu_minute_cny": gpu} if gpu is not None else {}
    return table, errors


def empty_price_table() -> Dict[str, Any]:
    return {
        "schema_version": PRICING_SCHEMA_VERSION,
        "updated_at": 0,
        "models": {},
        "apps": {},
        "workflows": {},
        "fallback": {},
    }


# 已知实测价的种子条目：只放本项目真正跑过、有实际扣费记录的那几条，绝不猜价。
# 管理员在价格表里写入同键条目后以管理员值为准（见 main.load_runninghub_price_table）。
SEED_MODELS = {
    "seedream-v5.5/layer-decomposition": {
        "billing": "fixed",
        "unit_cny": 0.19,
        "currency": "CNY",
        "unit_quantity_param": "",
        "default_quantity": 1,
        "note": "本项目实测 ¥0.168~0.21（2026-11-11/12），取中位数",
    },
}


def merge_seed_models(table: Dict[str, Any], site: str = "cn") -> Dict[str, Any]:
    """把已知国内站人民币实测价补进价格表视图；国际站优先用国际站官方原生 USD 标价。"""
    merged = dict(table or {})
    if site_key(site) != "cn":
        return merged
    models = dict(merged.get("models") or {})
    for key, entry in SEED_MODELS.items():
        if key not in models:
            models[key] = dict(entry)
    merged["models"] = models
    return merged


def normalize_official_prices(raw: Any, site: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """把官方价目表清洗成可直接并进价格表 models 的条目。

    当 ``raw`` 含 ``models_by_site`` 且传入 ``site``（``'cn'`` 或 ``'ai'``）时，优先取
    目标站点的原生价格表，缺失条目再从另一站点补充（跨站补充保留原 currency 供上层换算）。
    """
    source = raw if isinstance(raw, dict) else {}
    by_site = source.get("models_by_site") if isinstance(source.get("models_by_site"), dict) else {}
    if site and by_site:
        primary_site = site_key(site)
        secondary_site = "cn" if primary_site == "ai" else "ai"
        primary_raw = by_site.get(primary_site) if isinstance(by_site.get(primary_site), dict) else {}
        secondary_raw = by_site.get(secondary_site) if isinstance(by_site.get(secondary_site), dict) else {}
        entries: Dict[str, Any] = dict(secondary_raw)
        entries.update(primary_raw)
    else:
        entries = source.get("models") if isinstance(source.get("models"), dict) else {}

    models: Dict[str, Dict[str, Any]] = {}
    for key, entry in entries.items():
        key = str(key or "").strip()
        if not key or not isinstance(entry, dict):
            continue
        billing = str(entry.get("billing") or "per_unit").strip().lower()
        if billing not in BILLING_MODES:
            continue
        unit = _valid_unit(entry.get("unit_cny"))
        if unit is None:
            continue
        entry_site = entry.get("site")
        inferred_cur = entry.get("currency") or entry.get("site_currency") or (site_currency(entry_site) if entry_site else DEFAULT_CURRENCY)
        clean: Dict[str, Any] = {
            "billing": billing,
            "unit_cny": unit,
            "unit_quantity_param": "",
            "default_quantity": 1,
            "source": SOURCE_OFFICIAL,
            # 官价条目自带站点币种：国内站 CNY、国际站 USD。
            "currency": normalize_currency(inferred_cur),
            "confidence": CONFIDENCE_MEDIUM if "token" in str(entry.get("unit_label") or "").lower() else CONFIDENCE_HIGH,
        }
        low = _valid_unit(entry.get("low_cny"))
        high = _valid_unit(entry.get("high_cny"))
        if low is not None and high is not None:
            clean["low_cny"] = min(low, high)
            clean["high_cny"] = max(low, high)
        label = str(entry.get("price_text") or entry.get("unit_label") or "").strip()[:40]
        if label:
            clean["note"] = f"官方标价 {label}"
        notes_raw = entry.get("notes") or entry.get("tiers")
        notes = _tier_notes(notes_raw)
        if notes:
            clean["notes"] = notes
        models[key] = clean
    return models


def _tier_notes(raw: Any) -> Dict[str, float]:
    """从官方条目里取“档位名 → 单价”，兼容 notes 字典与 tiers 列表两种写法。"""
    notes: Dict[str, float] = {}
    if isinstance(raw, dict):
        for label, amount in raw.items():
            money = _as_money(amount)
            if str(label).strip() and money is not None and money > 0:
                notes[str(label)] = money
        return notes
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            money = _as_money(item.get("price"))
            if label and money is not None and money > 0:
                notes.setdefault(label, money)
    return notes


# 模型 ID 的写法归一：同一款模型可能被写成 text2image / t2i / txt2img 等。
# 只处理“同一个名字的不同写法”，绝不把两条不同型号的 SKU 合并——
# 例如 Dola-Seedream-5.5 与 Seedream-5.5 是官网上两条独立 SKU，价格相同
# 也必须各查各的价目表，不能互相顶替。
_OPERATION_ALIASES = {
    "text2image": "text-to-image",
    "t2i": "text-to-image",
    "txt2img": "text-to-image",
    "image2image": "image-to-image",
    "i2i": "image-to-image",
    "img2img": "image-to-image",
    "edit": "image-to-image",
    "text2video": "text-to-video",
    "t2v": "text-to-video",
    "image2video": "image-to-video",
    "i2v": "image-to-video",
    "layerdecomposition": "layer-decomposition",
}
# 版本号里的 "v" 只是写法差异（seedream-v5.5 与 seedream-5.5 同一个名字），
# 规范化产品名时删掉；注意这只作用于同一型号内部，不会把 Dola- 前缀去掉，
# 因为 Dola Seedream 与 Seedream 在官网是两条不同的 SKU。
_VERSION_V_RE = re.compile(r"v(?=\d)")


def _fold_name(text: str) -> str:
    """去掉分隔符并转小写，只用于比较。"""
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _fold_product(text: str) -> str:
    """产品名规范化：除分隔符外再去掉版本号后的 v。"""
    return _VERSION_V_RE.sub("", _fold_name(text))


def _normalize_model_name(model_id: str) -> Tuple[str, str]:
    """把模型 ID 拆成（规范化产品名, 规范化操作名）。

    不可靠的部分返回空串，调用方据此放弃兜底，绝不硬猜。
    """
    raw = str(model_id or "").strip().strip("/")
    if not raw:
        return "", ""
    parts = [part for part in raw.replace("_", "-").split("/") if part]
    if not parts:
        return "", ""
    operation = parts[-1] if len(parts) > 1 else ""
    product = "/".join(parts[:-1]) if len(parts) > 1 else parts[0]
    return _fold_product(product), _fold_name(_OPERATION_ALIASES.get(operation, operation))


def model_name_variants(model_id: str) -> List[str]:
    """同一模型名的字母大小写写法（不含原始 ID）。

    只在大小写上做等价，不改型号本身：Dola Seedream 与 Seedream 是官网
    两条独立 SKU，绝不通过剥掉前缀来互相顶替价格。
    """
    raw = str(model_id or "").strip().strip("/")
    if not raw:
        return []
    return [raw.lower()] if raw.lower() != raw else []


def loose_model_keys(table: Dict[str, Any], candidates: List[str]) -> List[str]:
    """按规范化模型名，在价格表里找出与候选键同名同操作的键。

    只收敛写法（分隔符、大小写、版本号 v、操作同义词），跨型号不借价。
    """
    models = (table or {}).get("models")
    if not isinstance(models, dict) or not models:
        return []
    wanted = set()
    for candidate in candidates or []:
        product, operation = _normalize_model_name(candidate)
        if product and operation:
            wanted.add((product, operation))
    if not wanted:
        return []
    matches: List[str] = []
    skip = set(candidates or [])
    for key in models:
        if key in skip:
            continue
        product, operation = _normalize_model_name(key)
        if product and operation and (product, operation) in wanted:
            matches.append(key)
    return matches


def merge_official_prices(table: Dict[str, Any], official: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """官方标价并入价格表视图；管理员已配置的同键条目优先。"""
    merged = dict(table or {})
    models = dict(merged.get("models") or {})
    for key, entry in (official or {}).items():
        if key not in models:
            models[key] = dict(entry)
    merged["models"] = models
    return merged


def fit_resolution(result: Dict[str, Any], res: Any) -> Dict[str, Any]:
    """按本次分辨率把分档价（1.5K/2K/3K）收敛成单一档位单价。

    档位由官网价目表给出（如 2K = ¥0.23/张），这里只做区间 → 单值的收敛：能
    确定档位就换成该档单价；分辨率小于最低档或大于最高档取端点档；含 token 等
    无法归一的计价保持区间不动。任何无法判断的情况原样返回。
    """
    if not isinstance(result, dict) or result.get("confidence") == CONFIDENCE_UNKNOWN:
        return result
    label = str(result.get("note") or "")
    if "token" in label.lower():
        return result
    tiers = result.get("_tiers")
    if not isinstance(tiers, list) or not tiers:
        return result
    amount = pick_tier_amount(tiers, res)
    if amount is None:
        return result
    fitted = dict(result)
    fitted["amount"] = amount
    fitted["low"] = amount
    fitted["high"] = amount
    # 原币金额同步收敛，否则换算到美元后又会退回整段区间。
    fitted["native"] = _money_triplet(amount, amount, amount)
    return fitted


def parse_tier_notes(notes: Any) -> List[Tuple[float, float]]:
    """把官网分档说明解析成 [(最低百万像素阈值, 单价)]，按阈值升序。

    兼容国内站中文（“≤260万像素”“>236万像素”）与国际站英文（“≤2.6MP”“>2.36MP”
    “1024×1536”“1.5K / 2K / 3K”）：
    - 仅标上界 ``≤ X``（如“≤236万像素”/“≤2.36MP”）代表最低档，起始阈值为 ``0.0``；
    - 标下界 ``> X``（如“>236万像素”/“>2.36MP”）代表超出 X 百万像素才命中，起始阈值为 ``X + 1e-6``；
    - 显式尺寸 ``W×H`` 按 ``W*H/1e6`` 计算；``1.5K/2K/3K`` 按方形像素近似。
    """
    if not isinstance(notes, dict) or not notes:
        return []
    parsed: List[Tuple[float, float]] = []
    for index, (label, amount) in enumerate(notes.items()):
        money = _as_money(amount)
        if money is None or money <= 0:
            continue
        text = str(label).replace(" ", "")
        gt_wan = re.search(r"[>＞](\d+(?:\.\d+)?)万(?:像素)?", text)
        le_wan = re.search(r"(?:≤|<=|<|＜)(\d+(?:\.\d+)?)万像素", text)
        any_wan = re.search(r"(\d+(?:\.\d+)?)万像素", text)
        gt_mp = re.search(r"[>＞](\d+(?:\.\d+)?)mp\b", text, re.I)
        le_mp = re.search(r"(?:≤|<=|<|＜)(\d+(?:\.\d+)?)mp\b", text, re.I)
        any_mp = re.search(r"(\d+(?:\.\d+)?)mp\b", text, re.I)
        dims = re.search(r"(\d{3,5})[x×*](\d{3,5})", text, re.I)
        tier = re.search(r"(\d+(?:\.\d+)?)[Kk]", text)

        if gt_wan:
            floor = float(gt_wan.group(1)) * 10000.0 / 1_000_000.0 + 1e-6
        elif gt_mp:
            floor = float(gt_mp.group(1)) + 1e-6
        elif le_wan:
            # 只有上界“≤236万像素”而无下界 > 时，它是最低档（从 0 MP 起算）。
            floor = 0.0 if index == 0 else float(le_wan.group(1)) * 10000.0 / 1_000_000.0
        elif le_mp:
            floor = 0.0 if index == 0 else float(le_mp.group(1))
        elif any_wan:
            floor = float(any_wan.group(1)) * 10000.0 / 1_000_000.0
        elif any_mp:
            floor = float(any_mp.group(1))
        elif dims:
            floor = float(dims.group(1)) * float(dims.group(2)) / 1_000_000.0
        elif tier:
            floor = (float(tier.group(1)) * 1024.0) ** 2 / 1_000_000.0
        else:
            floor = float(index)
        parsed.append((floor, money))
    parsed.sort(key=lambda item: item[0])
    return parsed


def _res_megapixels(res: Any) -> Optional[float]:
    """把画布上的 2K / 1024x1024 之类收敛成像素数（百万像素）。"""
    text = str(res or "").strip().lower()
    if not text:
        return None
    numeric = re.findall(r"\d+(?:\.\d+)?", text)
    if "x" in text and len(numeric) >= 2:
        try:
            return float(numeric[0]) * float(numeric[1]) / 1_000_000.0
        except ValueError:
            return None
    if not numeric:
        return None
    try:
        value = float(numeric[0])
    except ValueError:
        return None
    if "k" in text or (value <= 8):
        # 2K = 2048×2048 之类的方形近似，与官网“≤260万 / ≤462万像素”口径对齐。
        return (value * 1024.0) ** 2 / 1_000_000.0
    if value >= 100000:
        return value / 1_000_000.0
    return None


def pick_tier_amount(tiers: List[Tuple[float, float]], res: Any) -> Optional[float]:
    """按分辨率挑档；分辨率无法解析时返回 None（保留区间）。"""
    if not tiers:
        return None
    megapixels = _res_megapixels(res)
    if megapixels is None:
        return None
    chosen = tiers[0][1]
    for floor, amount in tiers:
        if megapixels >= floor:
            chosen = amount
    return chosen


def _unused_placeholder():
    return None


def extract_consume_money(raw_usage: Any) -> Optional[float]:
    """从 RunningHub 任务返回的 usage 里取本次实扣金额（元）。

    标准模型走 consumeMoney，三方渠道走 thirdPartyConsumeMoney；失败任务两者
    常为 None/0，这里一律过滤成 None，避免把 0 当实测样本污染统计。
    """
    if not isinstance(raw_usage, dict):
        return None
    for key in ("consumeMoney", "thirdPartyConsumeMoney"):
        money = _as_money(raw_usage.get(key))
        if money is not None and money > 0:
            return money
    return None
