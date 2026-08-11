(function () {
    'use strict';
    const WARN_LENGTH = 2200;
    let libraryTargetId = '';

    const value = item => String(item || '').trim();
    const liveNode = id => (typeof nodes !== 'undefined' ? nodes.find(node => node?.id === id) : null) || null;
    const list = node => Array.isArray(node?.promptPresets) ? node.promptPresets : [];
    const isPromptNode = node => node?.type === 'smart-prompt';
    const isLoopNode = node => node?.type === 'smart-loop' && node?.showPrompt === true;
    const isRunnablePromptNode = node => typeof isSmartRunnableNode === 'function' && isSmartRunnableNode(node);
    const upstreamControlsNode = node => isRunnablePromptNode(node) && typeof promptInputNodesFor === 'function' && promptInputNodesFor(node).length > 0;
    const isEditableTarget = node => Boolean(node && (isPromptNode(node) || isLoopNode(node) || isRunnablePromptNode(node)) && !upstreamControlsNode(node));

    function normalizePreset(card) {
        return {
            id: value(card?.id),
            name: value(card?.name) || '未命名预设',
            category: value(card?.category) || 'custom',
            subcategory: value(card?.subcategory),
            cover_url: value(card?.cover_url),
            prefix: value(card?.prefix || card?.positive),
            suffix: value(card?.suffix || card?.negative),
        };
    }

    function compose(node, rawText) {
        const items = list(node);
        const prefix = items.map(item => value(item.prefix)).filter(Boolean);
        const suffix = items.map(item => value(item.suffix)).filter(Boolean);
        return [...prefix, value(rawText), ...suffix].filter(Boolean).join('\n\n');
    }

    function presetText(item) {
        return value(item?.appliedText) || [value(item?.prefix), value(item?.suffix)].filter(Boolean).join('\n\n');
    }

    function removeVisiblePresetText(node, item) {
        const block = presetText(item);
        let text = String(node?.text || '');
        if (!block || !text) return;
        const candidates = [`${block}\n\n`, `\n\n${block}`, block];
        const match = candidates.map(candidate => ({ candidate, index: text.indexOf(candidate) })).find(entry => entry.index >= 0);
        if (!match) return;
        text = `${text.slice(0, match.index)}${text.slice(match.index + match.candidate.length)}`;
        node.text = text.replace(/\n{3,}/g, '\n\n').trim();
    }

    function presetBarHtml(nodeId, variant = 'node', part = 'all') {
        const node = liveNode(nodeId);
        const items = list(node);
        const locked = upstreamControlsNode(node);
        const text = variant === 'composer' ? value(promptInput?.innerText) : value(node?.text);
        const finalText = locked ? '' : compose(node, text);
        const chips = items.map((item, index) => `<span class="prompt-preset-chip" draggable="true" data-preset-index="${index}" title="${escapeAttr ? escapeAttr(item.name) : item.name}"><span>${escapeHtml ? escapeHtml(item.name) : item.name}</span><button type="button" data-preset-remove="${index}" aria-label="移除 ${escapeAttr ? escapeAttr(item.name) : item.name}"><i data-lucide="x"></i></button></span>`).join('');
        const summary = locked
            ? '<span class="prompt-preset-upstream">由上游提示词节点控制</span>'
            : `<span class="prompt-preset-length ${finalText.length > WARN_LENGTH ? 'warn' : ''}">${finalText.length} 字符${finalText.length > WARN_LENGTH ? ' · 部分平台可能截断' : ''}</span>`;
        const row = `<div class="prompt-preset-row"><div class="prompt-preset-chips">${chips || '<span class="prompt-preset-empty">未应用预设</span>'}</div>
                <div class="prompt-preset-actions"><button type="button" class="prompt-preset-clear" data-preset-clear ${items.length ? '' : 'disabled'} title="清空预设"><i data-lucide="rotate-ccw"></i></button><button type="button" class="prompt-preset-open" data-preset-open ${locked ? 'disabled title="由上游提示词控制"' : 'title="打开提示词库"'}><i data-lucide="library-big"></i><span>提示词库</span></button></div></div>`;
        const final = items.length ? `<details class="prompt-preset-final" ${variant === 'node' && node?.promptFinalPreviewOpen ? 'open' : ''}><summary>最终发送提示词 <span>${summary}</span></summary><div class="prompt-preset-final-body"><pre>${escapeHtml ? escapeHtml(finalText) : finalText}</pre><button type="button" class="smart-text-copy-btn prompt-preset-copy-final" data-preset-copy-final title="复制最终提示词" aria-label="复制最终提示词"><i data-lucide="copy"></i></button></div></details>` : '';
        const content = part === 'row' ? row : part === 'final' ? final : `${row}${final}`;
        if (!content) return '';
        return `<div class="prompt-preset-shell ${part === 'final' ? 'prompt-preset-final-only' : ''} ${locked ? 'is-locked' : ''}" data-preset-target="${nodeId}" data-preset-variant="${variant}">
            ${content}
        </div>`;
    }

    function mutate(nodeId, fn) {
        const node = liveNode(nodeId);
        if (!isEditableTarget(node)) return false;
        if (typeof pushUndo === 'function') pushUndo();
        node.promptPresets = list(node).map(item => ({ ...item }));
        fn(node);
        if (typeof render === 'function') render();
        if (typeof updateComposer === 'function') updateComposer();
        if (typeof scheduleSave === 'function') scheduleSave();
        return true;
    }

    function move(nodeId, from, to) {
        mutate(nodeId, node => {
            const items = node.promptPresets;
            if (from < 0 || from >= items.length || to < 0 || to >= items.length || from === to) return;
            items.splice(to, 0, items.splice(from, 1)[0]);
        });
    }

    function bindControls(root, nodeId) {
        if (!root) return;
        let dragIndex = -1;
        root.querySelectorAll('[data-preset-open]').forEach(button => button.onclick = event => {
            event.preventDefault(); event.stopPropagation();
            const node = liveNode(nodeId);
            if (!isEditableTarget(node)) { window.toast?.('当前输入由上游提示词节点控制'); return; }
            window.openPromptLibraryForTarget?.(nodeId);
        });
        root.querySelectorAll('[data-preset-clear]').forEach(button => button.onclick = event => {
            event.preventDefault(); event.stopPropagation();
            mutate(nodeId, node => {
                if (isPromptNode(node)) list(node).forEach(item => removeVisiblePresetText(node, item));
                node.promptPresets = [];
            });
        });
        root.querySelectorAll('[data-preset-remove]').forEach(button => button.onclick = event => {
            event.preventDefault(); event.stopPropagation();
            const index = Number(button.dataset.presetRemove);
            mutate(nodeId, node => {
                if (isPromptNode(node)) removeVisiblePresetText(node, node.promptPresets[index]);
                node.promptPresets.splice(index, 1);
            });
        });
        root.querySelectorAll('[data-preset-copy-final]').forEach(button => button.onclick = async event => {
            event.preventDefault(); event.stopPropagation();
            const node = liveNode(nodeId);
            const raw = root.dataset.presetVariant === 'composer' ? value(promptInput?.innerText) : value(node?.text);
            try { await navigator.clipboard.writeText(compose(node, raw)); window.toast?.('已复制最终提示词'); } catch (_) { window.toast?.('复制失败'); }
        });
        root.querySelectorAll('.prompt-preset-final').forEach(details => {
            ['pointerdown', 'mousedown', 'dblclick'].forEach(type => details.addEventListener(type, event => event.stopPropagation()));
            details.addEventListener('click', event => event.stopPropagation());
            details.querySelector('summary')?.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                const open = !details.open;
                if (root.dataset.presetVariant === 'node') {
                    const node = liveNode(nodeId);
                    if (!node) return;
                    node.promptFinalPreviewOpen = open;
                    if (typeof render === 'function') render();
                    if (typeof scheduleSave === 'function') scheduleSave();
                } else {
                    details.open = open;
                }
            });
        });
        root.querySelectorAll('[data-preset-index]').forEach(chip => {
            chip.addEventListener('dragstart', event => { dragIndex = Number(chip.dataset.presetIndex); chip.classList.add('dragging'); event.dataTransfer.effectAllowed = 'move'; });
            chip.addEventListener('dragend', () => { dragIndex = -1; chip.classList.remove('dragging'); });
            chip.addEventListener('dragover', event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; });
            chip.addEventListener('drop', event => { event.preventDefault(); const to = Number(chip.dataset.presetIndex); if (Number.isFinite(dragIndex)) move(nodeId, dragIndex, to); });
        });
    }

    function syncComposer() {
        const host = document.getElementById('composerPresetControls');
        if (!host) return;
        const node = typeof activeComposerNode === 'function' ? activeComposerNode() : null;
        if (!node) { host.innerHTML = ''; return; }
        // The composer uses the same embedded chip strip as a text node. The
        // surrounding prompt row supplies its own fixed prompt-library button.
        host.innerHTML = presetBarHtml(node.id, 'node', 'row');
        bindControls(host, node.id);
        window.lucide?.createIcons();
    }

    function addCard(card, targetId = '') {
        const lockedTargetId = String(targetId || libraryTargetId || '');
        let node = liveNode(lockedTargetId);
        if (lockedTargetId && !node) return { ok: false, message: '\u76ee\u6807\u8282\u70b9\u5df2\u4e0d\u53ef\u7f16\u8f91\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9' };
        if (node && !isEditableTarget(node)) return { ok: false, message: '\u5f53\u524d\u8f93\u5165\u7531\u4e0a\u6e38\u63d0\u793a\u8bcd\u8282\u70b9\u63a7\u5236' };
        if (!node) {
            if (typeof createPromptNode !== 'function') return { ok: false, message: '\u8bf7\u5148\u6253\u5f00\u753b\u5e03' };
            const point = typeof viewportCenter === 'function' ? viewportCenter() : { x: 420, y: 280 };
            node = createPromptNode(Math.round(point.x - 158), Math.round(point.y - 100));
        }
        const preset = normalizePreset(card);
        if (isPromptNode(node)) {
            const appliedText = presetText(preset);
            const existingIndex = list(node).findIndex(item => item.id && item.id === preset.id);
            if (typeof pushUndo === 'function') pushUndo();
            node.promptPresets = list(node).map(item => ({ ...item }));
            if (existingIndex >= 0) {
                removeVisiblePresetText(node, node.promptPresets[existingIndex]);
                node.promptPresets.splice(existingIndex, 1);
            } else {
                node.promptPresets.push({ ...preset, appliedText });
                node.text = [value(node.text), appliedText].filter(Boolean).join('\n\n');
            }
            if (typeof render === 'function') render();
            if (typeof scheduleSave === 'function') scheduleSave();
            window.toast?.(existingIndex >= 0 ? '已从文本移除提示词' : '已添加到文本');
            return { ok: true, targetId: node.id, selected: existingIndex < 0 };
        }
        const exists = list(node).some(item => item.id && item.id === preset.id);
        mutate(node.id, target => {
            const items = list(target).map(item => ({ ...item }));
            target.promptPresets = exists ? items.filter(item => item.id !== preset.id) : [...items, preset];
        });
        return { ok: true, targetId: node.id, selected: !exists };
    }

    window.promptPresetComposeText = compose;
    window.promptPresetNodeControlsHtml = node => presetBarHtml(node.id, 'node', 'row');
    window.promptPresetNodeFinalHtml = node => presetBarHtml(node.id, 'node', 'final');
    window.bindPromptPresetNodeControls = (el, node) => el.querySelectorAll('.prompt-preset-shell').forEach(root => bindControls(root, node.id));
    window.syncPromptPresetComposer = syncComposer;
    window.openPromptLibraryForTarget = nodeId => {
        libraryTargetId = String(nodeId || '');
        window.__promptLibraryTargetId = libraryTargetId;
        window.openPromptLibrary?.({ targetId: libraryTargetId });
    };
    window.getPromptLibraryTargetId = () => libraryTargetId || String(window.__promptLibraryTargetId || '');
    window.clearPromptLibraryTarget = () => { libraryTargetId = ''; window.__promptLibraryTargetId = ''; };
    window.applyPromptPresetCard = addCard;
    window.isPromptPresetTargetEditable = isEditableTarget;
    document.addEventListener('DOMContentLoaded', () => {
        syncComposer();
        let queued = false;
        promptInput?.addEventListener('input', () => {
            if (queued) return;
            queued = true;
            requestAnimationFrame(() => { queued = false; syncComposer(); });
        });
    });
})();
