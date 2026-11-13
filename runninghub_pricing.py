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


def _result(amount, low, high, confidence, source, samples=0, note=""):
    return {
        "amount": None if amount is None else round(float(amount), 4),
        "low": None if low is None else round(float(low), 4),
        "high": None if high is None else round(float(high), 4),
        "confidence": confidence,
        "source": source,
        "samples": int(samples or 0),
        "note": note,
    }


def _empty_result():
    return _result(None, None, None, CONFIDENCE_UNKNOWN, SOURCE_UNKNOWN, 0, "")


def empty_estimate() -> Dict[str, Any]:
    """对外公开的 unknown 结果构造器，供路由在参数非法时返回同构结果。"""
    return _empty_result()


def _history_result(stats_entry: Optional[Dict[str, Any]]):
    """基于历史实扣样本给结论；无有效样本返回 None 交给下一档。"""
    if not isinstance(stats_entry, dict):
        return None
    samples = [_as_money(item) for item in (stats_entry.get("samples") or [])]
    samples = [s for s in samples if s is not None and s > 0]
    if not samples:
        return None
    ordered = sorted(samples)
    mid = median_of(ordered)
    if len(samples) >= HISTORY_RELIABLE_SAMPLES:
        low = ordered[0] if len(samples) < 4 else _quantile(ordered, 0.25)
        high = ordered[-1] if len(samples) < 4 else _quantile(ordered, 0.75)
        return _result(mid, low, high, CONFIDENCE_HIGH, SOURCE_HISTORY, len(samples))
    return _result(
        mid, ordered[0], ordered[-1], CONFIDENCE_MEDIUM, SOURCE_HISTORY_SPARSE, len(samples)
    )


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


def _table_result(table: Dict[str, Any], kind: str, entry_id: str, quantity: Any):
    """固定单价 × 数量；无表项或单价非法返回 None。"""
    if not isinstance(table, dict):
        return None
    if kind == "model":
        entry = (table.get("models") or {}).get(entry_id)
    elif kind == "app":
        entry = (table.get("apps") or {}).get(entry_id)
    else:
        entry = (table.get("workflows") or {}).get(entry_id)
    if not isinstance(entry, dict):
        return None
    # 应用/工作流走 manual_cny（手工单次总价），模型走 unit_cny × 数量。
    if "manual_cny" in entry and entry.get("manual_cny") is not None:
        manual = _as_money(entry.get("manual_cny"))
        if manual is not None and manual > 0:
            return _result(manual, manual, manual, CONFIDENCE_MEDIUM, SOURCE_MANUAL)
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
    return _result(
        amount,
        low_amount,
        high_amount,
        confidence,
        source,
        0,
        str(entry.get("note") or "")[:120],
    )


def _fallback_result(table: Dict[str, Any], kind: str, quantity: Any):
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
    return _result(amount, amount, amount, CONFIDENCE_LOW, SOURCE_FALLBACK)


def estimate_price(
    table: Optional[Dict[str, Any]],
    stats_entry: Optional[Dict[str, Any]],
    kind: str,
    entry_id: str,
    quantity: Any = None,
) -> Dict[str, Any]:
    """按三档优先级给出本次调用的预估金额与置信度。

    返回 ``{amount, low, high, confidence, source, samples, note}``；金额单位人
    民币元。amount 为最可能值，low/high 为区间端点；unknown 时全为 None。
    """
    kind = str(kind or "").strip().lower()
    if kind not in KINDS or not str(entry_id or "").strip():
        return _empty_result()

    history = _history_result(stats_entry)
    if history:
        return history

    priced = _table_result(table or {}, kind, entry_id, quantity)
    if priced:
        return priced

    fallback = _fallback_result(table or {}, kind, quantity)
    if fallback:
        return fallback

    return _empty_result()


def apply_observed(
    stats_entry: Optional[Dict[str, Any]],
    amount: Any,
    observed_at: float,
) -> Optional[Dict[str, Any]]:
    """把一次实扣金额并入历史统计并返回更新后的条目（不改入参）。

    金额 <=0 或非法返回 None，表示不应记录（失败任务实扣为空/0）。更新后会顺
    带重算 median/p25/p75，供读取端直接取用。
    """
    money = _as_money(amount)
    if money is None or money <= 0:
        return None
    entry = dict(stats_entry or {})
    samples = [_as_money(item) for item in (entry.get("samples") or [])]
    samples = [s for s in samples if s is not None and s > 0]
    samples.append(money)
    if len(samples) > HISTORY_SAMPLE_CAP:
        samples = samples[-HISTORY_SAMPLE_CAP:]
    entry["samples"] = samples
    entry["count"] = len(samples)
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
        "unit_quantity_param": "",
        "default_quantity": 1,
        "note": "本项目实测 ¥0.168~0.21（2026-11-11/12），取中位数",
    },
}


def merge_seed_models(table: Dict[str, Any]) -> Dict[str, Any]:
    """把已知实测价补进价格表视图；管理员已配置的同键条目优先。"""
    merged = dict(table or {})
    models = dict(merged.get("models") or {})
    for key, entry in SEED_MODELS.items():
        if key not in models:
            models[key] = dict(entry)
    merged["models"] = models
    return merged


def normalize_official_prices(raw: Any) -> Dict[str, Dict[str, Any]]:
    """把官方价目表清洗成可直接并进价格表 models 的条目。

    官方价来自 RunningHub 的 SKU 目录（tools/sync_runninghub_official_prices.py
    抓取），分档价（1.5K/2K/3K 不同单价）保留成 low_cny/high_cny 区间；带 token
    单位的单价依赖实际用量，只能给 medium 置信度。
    """
    models: Dict[str, Dict[str, Any]] = {}
    source = raw if isinstance(raw, dict) else {}
    entries = source.get("models") if isinstance(source.get("models"), dict) else {}
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
        clean: Dict[str, Any] = {
            "billing": billing,
            "unit_cny": unit,
            "unit_quantity_param": "",
            "default_quantity": 1,
            "source": SOURCE_OFFICIAL,
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
        models[key] = clean
    return models


def merge_official_prices(table: Dict[str, Any], official: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """官方标价并入价格表视图；管理员已配置的同键条目优先。"""
    merged = dict(table or {})
    models = dict(merged.get("models") or {})
    for key, entry in (official or {}).items():
        if key not in models:
            models[key] = dict(entry)
    merged["models"] = models
    return merged


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
