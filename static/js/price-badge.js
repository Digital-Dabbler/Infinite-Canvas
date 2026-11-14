/* RunningHub 事前估价徽标。
 *
 * 目标：在任何"运行"意味的按钮旁显示本次调用的预估金额，让用户点之前就知道大概花多少。
 * 只在走 RunningHub 平台时出现，其它平台/离线/无配置组一律静默不显示，绝不禁用按钮。
 *
 * 三档精度（与后端 runninghub_pricing 对齐）：
 *   high   —— 有足够实测历史或固定单价，显示 ≈¥0.17
 *   medium —— 样本少或手工价，显示区间 ≈¥0.2~0.5
 *   low    —— GPU 分钟兜底，显示 ≈¥0.3（弱提示）
 *   unknown—— 无任何依据，显示"费用待定"
 *
 * 用法：RHPriceBadge.attach(anchorEl, () => ({kind, id, quantity}))
 *   anchorEl 是插入锚点（运行按钮），回调返回当前要估价的调用身份；返回 null 表示不走 RH，隐藏。
 *   内部按 spec 指纹缓存、防抖，参数变化时重新拉取。
 */
(function () {
    "use strict";

    const TTL_MS = 60 * 1000;      // 与后端估价缓存同量级，避免频繁请求
    const DEBOUNCE_MS = 300;

    const cache = new Map();       // specKey -> {at, estimate}
    const inflight = new Map();    // specKey -> Promise

    function tr(key, fallback) {
        try {
            if (window.StudioI18n && window.StudioI18n.t) {
                const value = window.StudioI18n.t(key);
                if (value && value !== key) return value;
            }
        } catch (_) { /* 词条未就绪时回落 */ }
        return fallback;
    }

    // 币种跟着账号结算站点走：runninghub.ai 是美元、runninghub.cn 是人民币。
    // 后端把官方价换算成该配置组的结算币种后才返回 currency，这里只负责符号。
    function symbolOf(currency) {
        return String(currency || "").toUpperCase() === "USD" ? "$" : "¥";
    }

    function money(value, currency) {
        const number = Number(value);
        if (!Number.isFinite(number) || number <= 0) return "";
        const sign = symbolOf(currency);
        if (number >= 10) return `${sign}${number.toFixed(1)}`;
        if (number >= 1) return `${sign}${number.toFixed(2)}`;
        // 国际站大量图片模型单价带有效千分位/万分位（如 $0.015、$0.027、$0.009、$0.0077）：
        // 常规 2 位小数金额仍展示两位（如 $0.06、$0.10），有第 3~4 位非零数字时如实保留，
        // 避免 $0.015 被粗暴四舍五入成 $0.02、或 <$0.005 时变成 $0.00 消失。
        const fixed4 = number.toFixed(4);
        const trimmed = fixed4.replace(/0+$/, "");
        const dotIndex = trimmed.indexOf(".");
        const decimals = dotIndex >= 0 ? trimmed.length - dotIndex - 1 : 0;
        if (decimals <= 2) return `${sign}${number.toFixed(2)}`;
        return `${sign}${trimmed}`;
    }

    // 后端估价身份：kind|id|quantity|res（不含 multiplier，次数只在客户端乘算）。
    function backendKey(spec) {
        const res = spec.res == null ? "" : String(spec.res);
        return `${spec.kind}|${spec.id}|${spec.quantity == null ? "" : spec.quantity}|${res}`;
    }
    // 前端渲染身份：再带上 multiplier，次数变化时复用缓存结果重绘、不重复请求。
    function specKey(spec) {
        const mult = Number(spec.multiplier || 1);
        return `${backendKey(spec)}|${mult}`;
    }

    async function fetchEstimate(spec) {
        const key = backendKey(spec);
        const now = Date.now();
        const hit = cache.get(key);
        if (hit && now - hit.at < TTL_MS) return hit.estimate;
        if (inflight.has(key)) return inflight.get(key);
        const params = new URLSearchParams({ kind: spec.kind, id: spec.id });
        if (spec.quantity != null && spec.quantity !== "") params.set("quantity", String(spec.quantity));
        // 分辨率标签（2k / 1371x2589）用于在官方分档价里挑到对应档位。
        if (spec.res != null && spec.res !== "") params.set("res", String(spec.res));
        const request = fetch(`/api/runninghub/estimate?${params.toString()}`, { credentials: "same-origin" })
            .then(response => response.ok ? response.json() : null)
            .then(body => {
                const raw = (body && body.estimate) || { confidence: "unknown" };
                const estimate = Object.assign({}, raw);
                if (!estimate.currency && body && body.currency) {
                    estimate.currency = body.currency;
                }
                cache.set(key, { at: Date.now(), estimate });
                return estimate;
            })
            .catch(() => ({ confidence: "error" }))
            .finally(() => inflight.delete(key));
        inflight.set(key, request);
        return request;
    }

    // 把估价结果转成 {label, tone, title} 三段：徽标文案、配色、悬浮说明。
    // multiplier 是本次点击要提交的次数（图片 n 张 / 循环 n 次），只在客户端乘算，
    // 不发给后端——后端估的是"单次调用"的金额。
    function describe(estimate, multiplier) {
        const factor = Math.max(1, Number(multiplier || 1));
        const confidence = String(estimate && estimate.confidence || "unknown");
        if (confidence === "error") return null; // 网络失败静默，不打扰
        if (confidence === "unknown") {
            return { label: tr("smart.price.pending", "费用待定"), tone: "muted", title: tr("smart.price.pendingTip", "首次运行，暂无历史价格参考") };
        }
        const currency = String(estimate.currency || "CNY");
        const amount = money(Number(estimate.amount || 0) * factor, currency);
        if (!amount) return { label: tr("smart.price.pending", "费用待定"), tone: "muted", title: "" };
        const low = money(Number(estimate.low || 0) * factor, currency);
        const high = money(Number(estimate.high || 0) * factor, currency);
        let label = `≈${amount}`;
        // 官方分档价（1.5K/2K/3K 单价不同）本身就是一个区间，低置信度或只有单值时显示单值。
        if (low && high && low !== high) {
            label = `≈${low}~${high}`;
        }
        let title;
        if (estimate.source === "history" || estimate.source === "history_sparse") {
            const count = Number(estimate.samples || 0);
            title = count >= 3
                ? trfLite("smart.price.historyTip", count, "基于最近 {n} 次实测扣费的中位数")
                : tr("smart.price.sparseTip", "实测样本较少，仅供参考");
        } else if (estimate.source === "official") {
            title = tr("smart.price.officialTip", "按 RunningHub 官方标价估算，实际以扣费为准");
        } else if (estimate.source === "table") {
            title = tr("smart.price.tableTip", "按官方单价 × 本次数量估算");
        } else if (estimate.source === "manual") {
            title = tr("smart.price.manualTip", "按管理员录入的单次价格估算");
        } else if (estimate.source === "fallback") {
            title = tr("smart.price.fallbackTip", "按 GPU 时长粗略兜底，实际以扣费为准");
        } else {
            title = "";
        }
        if (factor > 1) {
            title = trfLite("smart.price.multiplierTip", factor, "本次点击提交 {n} 次") + " · " + title;
        }
        if (estimate.rateMissing) {
            title = tr("smart.price.rateMissingTip", "缺少汇率，按原始标价币种显示");
        }
        const tone = confidence === "low" ? "weak" : "ok";
        return { label, tone, title };
    }

    function trfLite(key, value, fallback) {
        return tr(key, fallback).replaceAll("{n}", String(value));
    }

    function ensureBadge(anchor) {
        const parent = anchor.parentElement;
        if (!parent) return null;
        let badge = parent.querySelector(":scope > .rh-price-badge");
        if (badge) return badge;
        badge = document.createElement("span");
        badge.className = "rh-price-badge";
        badge.setAttribute("role", "note");
        badge.hidden = true;
        // 徽标插在按钮左侧（图2 的形态）：运行按钮多为固定宽度的胶囊/圆形，
        // 塞进按钮内部会挤破布局，紧邻其外最贴近参考 UI。
        parent.insertBefore(badge, anchor);
        return badge;
    }

    function render(badge, described) {
        // 组合胶囊（参考 UI）只有真有钱数时才撑起边框；没有估价就退回单个圆形按钮。
        const pill = badge.closest(".run-pill");
        if (!described) {
            badge.hidden = true;
            badge.textContent = "";
            badge.removeAttribute("title");
            badge.dataset.tone = "";
            if (pill) pill.classList.remove("has-price");
            return;
        }
        badge.hidden = false;
        badge.textContent = described.label;
        badge.dataset.tone = described.tone;
        if (described.title) badge.title = described.title;
        else badge.removeAttribute("title");
        if (pill) pill.classList.add("has-price");
    }

    function attach(anchor, specFn) {
        if (!anchor || typeof specFn !== "function") return { refresh() {} };
        // 重复挂载时复用同一个徽标：旧版会再插一个 span，两个徽标同抢 .run-pill 的
        // has-price，且旧闭包永远停在最后一次 spec 上。
        const existing = anchor._rhPriceAttached;
        if (existing) return existing;
        const badge = ensureBadge(anchor);
        if (!badge) return { refresh() {} };
        let timer = 0;
        let lastKey = "";
        async function refresh() {
            let spec;
            try {
                spec = specFn();
            } catch (_) {
                spec = null;
            }
            if (!spec || !spec.kind || !spec.id) {
                render(badge, null);
                lastKey = "";
                return;
            }
            const key = specKey(spec);
            if (key === lastKey) return; // 参数没变，不重复请求
            lastKey = key;
            const estimate = await fetchEstimate(spec);
            if (specKey(specFn() || spec) !== key) return; // 期间参数已变，丢弃这次结果
            render(badge, describe(estimate, spec.multiplier));
        }
        function schedule() {
            clearTimeout(timer);
            timer = setTimeout(refresh, DEBOUNCE_MS);
        }
        anchor._rhPriceRefresh = schedule;
        const handle = { refresh: schedule };
        anchor._rhPriceAttached = handle;
        refresh();
        return handle;
    }

    window.RHPriceBadge = { attach, refreshAnchor(anchor) { if (anchor && anchor._rhPriceRefresh) anchor._rhPriceRefresh(); } };
})();
