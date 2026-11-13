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

    function money(value) {
        const number = Number(value);
        if (!Number.isFinite(number) || number <= 0) return "";
        if (number >= 10) return `¥${number.toFixed(0)}`;
        if (number >= 1) return `¥${number.toFixed(2)}`;
        return `¥${number.toFixed(2)}`;
    }

    // 后端估价身份：kind|id|quantity（不含 multiplier，次数只在客户端乘算）。
    function backendKey(spec) {
        return `${spec.kind}|${spec.id}|${spec.quantity == null ? "" : spec.quantity}`;
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
        const request = fetch(`/api/runninghub/estimate?${params.toString()}`, { credentials: "same-origin" })
            .then(response => response.ok ? response.json() : null)
            .then(body => {
                const estimate = (body && body.estimate) || { confidence: "unknown" };
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
        const amount = money(Number(estimate.amount || 0) * factor);
        if (!amount) return { label: tr("smart.price.pending", "费用待定"), tone: "muted", title: "" };
        const low = money(Number(estimate.low || 0) * factor);
        const high = money(Number(estimate.high || 0) * factor);
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
        refresh();
        return { refresh: schedule };
    }

    window.RHPriceBadge = { attach, refreshAnchor(anchor) { if (anchor && anchor._rhPriceRefresh) anchor._rhPriceRefresh(); } };
})();
