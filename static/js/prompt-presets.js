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

    function setLibraryTarget(nodeId = '') {
        libraryTargetId = String(nodeId || '');
        window.__promptLibraryTargetId = libraryTargetId;
        return libraryTargetId;
    }

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

    // The text area stores only the author's own text. Presets remain metadata and
    // are applied only when a final prompt is composed for display or submission.
    function legacyAppliedText(item) {
        return value(item?.appliedText);
    }

    function removeTrackedLegacyText(text, block) {
        if (!block || !text) return text;
        const candidates = [`${block}\n\n`, `\n\n${block}`, block];
        const match = candidates.map(candidate => ({ candidate, index: text.indexOf(candidate) })).find(entry => entry.index >= 0);
        if (!match) return text;
        return `${text.slice(0, match.index)}${text.slice(match.index + match.candidate.length)}`
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    function removeBoundaryWrappedText(text, prefix, suffix) {
        const source = String(text || '').trim();
        const start = value(prefix);
        const end = value(suffix);
        if (!start || !end || !source.startsWith(start) || !source.endsWith(end)) return source;
        const middle = source.slice(start.length, source.length - end.length);
        // Only remove a wrapper when it is separated from the body by whitespace.
        // This prevents a preset from deleting similarly named author text in place.
        if (!/^\s/.test(middle) || !/\s$/.test(middle)) return source;
        return middle.trim();
    }

    // Older builds either appended a tracked `appliedText` block or persisted
    // prefix/body/suffix directly in node.text. Migration v3 only removes an exact
    // wrapper at the text boundaries; ordinary author text is never searched for.
    function migratePromptPresetNodeText(node) {
        if (!isPromptNode(node) || !Array.isArray(node?.promptPresets) || !node.promptPresets.length) return false;
        const items = list(node);
        const hasTrackedText = items.some(item => Object.prototype.hasOwnProperty.call(item || {}, 'appliedText'));
        if (node.promptPresetTextModelVersion === 3 && !hasTrackedText) return false;
        let changed = false;
        let text = String(node.text || '');
        items.forEach(item => {
            const tracked = legacyAppliedText(item);
            if (!tracked) return;
            const nextText = removeTrackedLegacyText(text, tracked);
            if (nextText !== text) {
                text = nextText;
                changed = true;
            }
        });
        if (node.promptPresetTextModelVersion !== 3) {
            // Current composition order is all prefixes, then body, then all suffixes.
            const groupedText = removeBoundaryWrappedText(
                text,
                items.map(item => value(item.prefix)).filter(Boolean).join('\n\n'),
                items.map(item => value(item.suffix)).filter(Boolean).join('\n\n')
            );
            if (groupedText !== String(text || '').trim()) {
                text = groupedText;
                changed = true;
            } else {
                // A node can have acquired more tags after its old body was written;
                // in that case only the original single preset wraps the body.
                for (const item of items) {
                    const unwrapped = removeBoundaryWrappedText(text, item.prefix, item.suffix);
                    if (unwrapped !== String(text || '').trim()) {
                        text = unwrapped;
                        changed = true;
                        break;
                    }
                }
            }
        }
        node.promptPresets = items.map(item => {
            if (!Object.prototype.hasOwnProperty.call(item || {}, 'appliedText')) return item;
            const { appliedText, ...preset } = item;
            changed = true;
            return preset;
        });
        if (text !== String(node.text || '')) {
            node.text = text;
            changed = true;
        }
        if (node.promptPresetTextModelVersion !== 3) {
            node.promptPresetTextModelVersion = 3;
            changed = true;
        }
        return changed;
    }

    function presetBarHtml(nodeId, variant = 'node', part = 'all') {
        const node = liveNode(nodeId);
        const items = list(node);
        const locked = upstreamControlsNode(node);
        const text = variant === 'composer' ? value(promptInput?.innerText) : value(node?.text);
        const finalText = locked ? '' : compose(node, text);
        const chips = items.map((item, index) => `<span class="prompt-preset-chip" draggable="true" tabindex="0" role="listitem" data-preset-index="${index}" title="${escapeAttr ? escapeAttr(item.name) : item.name}" aria-label="${escapeAttr ? escapeAttr(`${item.name}，可拖动排序；按 Alt 加左右方向键排序`) : item.name}"><span>${escapeHtml ? escapeHtml(item.name) : item.name}</span><button type="button" data-preset-remove="${index}" title="移除 ${escapeAttr ? escapeAttr(item.name) : item.name}" aria-label="移除 ${escapeAttr ? escapeAttr(item.name) : item.name}"><i data-lucide="x"></i></button></span>`).join('');
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
        ['pointerdown', 'mousedown', 'click', 'dblclick'].forEach(type => root.addEventListener(type, event => event.stopPropagation()));
        root.querySelectorAll('[data-preset-open]').forEach(button => button.onclick = event => {
            event.preventDefault(); event.stopPropagation();
            const node = liveNode(nodeId);
            if (!isEditableTarget(node)) { window.toast?.('当前输入由上游提示词节点控制'); return; }
            window.openPromptLibraryForTarget?.(nodeId);
        });
        root.querySelectorAll('[data-preset-clear]').forEach(button => button.onclick = event => {
            event.preventDefault(); event.stopPropagation();
            mutate(nodeId, node => {
                node.promptPresets = [];
            });
        });
        root.querySelectorAll('[data-preset-remove]').forEach(button => button.onclick = event => {
            event.preventDefault(); event.stopPropagation();
            const index = Number(button.dataset.presetRemove);
            mutate(nodeId, node => {
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
            ['pointerdown', 'mousedown', 'click', 'dblclick'].forEach(type => chip.addEventListener(type, event => event.stopPropagation()));
            chip.addEventListener('dragstart', event => {
                dragIndex = Number(chip.dataset.presetIndex);
                chip.classList.add('dragging');
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', String(dragIndex));
            });
            chip.addEventListener('dragend', () => {
                dragIndex = -1;
                root.querySelectorAll('[data-preset-index]').forEach(item => item.classList.remove('dragging', 'drop-before', 'drop-after'));
            });
            chip.addEventListener('dragenter', event => {
                event.preventDefault();
                if (Number(chip.dataset.presetIndex) === dragIndex) return;
                const before = event.clientX < chip.getBoundingClientRect().left + chip.offsetWidth / 2;
                chip.classList.toggle('drop-before', before);
                chip.classList.toggle('drop-after', !before);
            });
            chip.addEventListener('dragover', event => {
                event.preventDefault();
                event.stopPropagation();
                event.dataTransfer.dropEffect = 'move';
                if (Number(chip.dataset.presetIndex) === dragIndex) return;
                const before = event.clientX < chip.getBoundingClientRect().left + chip.offsetWidth / 2;
                chip.classList.toggle('drop-before', before);
                chip.classList.toggle('drop-after', !before);
            });
            chip.addEventListener('dragleave', () => chip.classList.remove('drop-before', 'drop-after'));
            chip.addEventListener('drop', event => {
                event.preventDefault();
                event.stopPropagation();
                const target = Number(chip.dataset.presetIndex);
                const from = Number.isFinite(dragIndex) ? dragIndex : Number(event.dataTransfer.getData('text/plain'));
                if (!Number.isFinite(from) || !Number.isFinite(target) || from === target) return;
                const before = event.clientX < chip.getBoundingClientRect().left + chip.offsetWidth / 2;
                move(nodeId, from, before ? target - (from < target ? 1 : 0) : target + (from < target ? 0 : 1));
            });
            chip.addEventListener('keydown', event => {
                if (!event.altKey || !['ArrowLeft', 'ArrowRight'].includes(event.key) || event.target.closest('button')) return;
                const from = Number(chip.dataset.presetIndex);
                const to = from + (event.key === 'ArrowLeft' ? -1 : 1);
                if (to < 0 || to >= list(liveNode(nodeId)).length) return;
                event.preventDefault();
                event.stopPropagation();
                move(nodeId, from, to);
                requestAnimationFrame(() => document.querySelector(`[data-preset-target="${CSS.escape(nodeId)}"] [data-preset-index="${to}"]`)?.focus());
            });
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
        if (lockedTargetId && !node) return { ok: false, message: '目标节点已不可编辑，请重新选择' };
        if (node && !isEditableTarget(node)) return { ok: false, message: '当前输入由上游提示词节点控制' };
        if (!node) {
            if (typeof createPromptNode !== 'function') return { ok: false, message: '请先打开画布' };
            const point = typeof viewportCenter === 'function' ? viewportCenter() : { x: 420, y: 280 };
            node = createPromptNode(Math.round(point.x - 158), Math.round(point.y - 100));
            setLibraryTarget(node.id);
        }
        const preset = normalizePreset(card);
        if (isPromptNode(node)) {
            const existingIndex = list(node).findIndex(item => item.id && item.id === preset.id);
            if (typeof pushUndo === 'function') pushUndo();
            node.promptPresets = list(node).map(item => {
                const { appliedText, ...saved } = item || {};
                return saved;
            });
            if (existingIndex >= 0) node.promptPresets.splice(existingIndex, 1);
            else node.promptPresets.push({ ...preset });
            node.promptPresetTextModelVersion = 3;
            if (typeof render === 'function') render();
            if (typeof scheduleSave === 'function') scheduleSave();
            window.toast?.(existingIndex >= 0 ? '已移除提示词预设' : '已添加提示词预设');
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
    window.migratePromptPresetNodeText = migratePromptPresetNodeText;
    window.promptPresetNodeControlsHtml = node => presetBarHtml(node.id, 'node', 'row');
    window.promptPresetNodeFinalHtml = node => presetBarHtml(node.id, 'node', 'final');
    window.bindPromptPresetNodeControls = (el, node) => el.querySelectorAll('.prompt-preset-shell').forEach(root => bindControls(root, node.id));
    window.syncPromptPresetComposer = syncComposer;
    window.openPromptLibraryForTarget = nodeId => {
        setLibraryTarget(nodeId);
        window.openPromptLibrary?.({ targetId: libraryTargetId });
    };
    window.getPromptLibraryTargetId = () => libraryTargetId || String(window.__promptLibraryTargetId || '');
    window.setPromptLibraryTarget = setLibraryTarget;
    window.clearPromptLibraryTarget = () => setLibraryTarget('');
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
