/*!
 * resource-preview-panel.js
 *
 * 统一的「资源预览面板」组件 —— 复用的标准实现。
 *
 * 用 createResourcePreviewPanel(opts) 创建一个实例，返回 { open, close, refresh, isOpen }。
 * 组件只负责「展示 + 交互」，数据与业务通过 opts 注入，因此可在任何用到资源预览的地方复用。
 *
 * opts:
 *   mount     Element  组件挂载的容器（默认 document.body，画布场景传 shell）。
 *   getItems  (ctx)=>{items,targetIdentity,kindLabel}  数据来源；ctx 为 open 时传入的上下文。
 *   onSelect  (item, ctx)=>void  选中某个资源时回调。
 *   onRemove  (item)=>boolean    从历史移除回调；返回 true 表示已移除（用于刷新）。
 *   renderThumb  (item, ctx)=>string  缩略图 HTML（默认生成 <img>/<video>/<audio>）。
 *   previewSrc   (item)=>string      大图预览的 src（默认 item.url）。
 *   sourceLabel  (item)=>string      资源来源文本（用于搜索匹配；默认 ''）。
 *   text      (key, vars)=>string    文案；默认用内置中英文字典。可传 (k,v)=> v?trf(k,v):tr(k)。
 *   panelWidth number                面板最大宽度；默认 620。
 */
(function (global) {
    'use strict';

    const CHUNK = 36;
    const GAP = 8;
    const TARGET_H = 96;

    const DEFAULT_TEXT = {
        'smart.resourceSearch': { zh: '搜索当前画布资源', en: 'Search current canvas media' },
        'smart.resourceAll': { zh: '全部', en: 'All' },
        'smart.resourceImported': { zh: '已导入', en: 'Imported' },
        'smart.resourceGenerated': { zh: '已生成', en: 'Generated' },
        'smart.resourcePickerLabel': { zh: '从当前画布选择{kind}', en: 'Choose a {kind} from this canvas' },
        'smart.resourcePickerRange': { zh: '当前画布媒体范围', en: 'Current canvas media range' },
        'smart.resourceGridView': { zh: '网格视图', en: 'Grid view' },
        'smart.resourceListView': { zh: '列表视图', en: 'List view' },
        'smart.resourceCanvasMediaCount': { zh: '{n} 个当前画布{kind}', en: '{n} canvas {kind} items' },
        'smart.resourceTabCount': { zh: '，{n} 个资源', en: ', {n} items' },
        'smart.resourceSortRecent': { zh: '当前按最近使用；点击按文件名排序', en: 'Sorted by recent use; click to sort by name' },
        'smart.resourceSortNameAsc': { zh: '当前按文件名升序；点击改为降序', en: 'Sorted by name ascending; click for descending' },
        'smart.resourceSortNameDesc': { zh: '当前按文件名降序；点击改为最近使用', en: 'Sorted by name descending; click for recent use' },
        'smart.resourceScopeAll': { zh: '当前显示：全部画布媒体（含历史）；点击切换', en: 'Currently showing: all canvas media. Click to switch.' },
        'smart.resourceScopeActive': { zh: '当前显示：正在使用；点击切换', en: 'Currently showing: in use. Click to switch.' },
        'smart.resourceScopeHistory': { zh: '当前显示：历史媒体；点击切换', en: 'Currently showing: history. Click to switch.' },
        'smart.resourceNoMatchTitle': { zh: '没有匹配的资源', en: 'No matching media' },
        'smart.resourceNoMatchBody': { zh: '试试更短的文件名，或切换资源范围。', en: 'Try a shorter file name or switch the media range.' },
        'smart.resourceNoGeneratedTitle': { zh: '当前画布暂无已生成资源', en: 'No generated media here' },
        'smart.resourceNoGeneratedBody': { zh: '先运行生成节点，结果会出现在这里。', en: 'Run a generation node and its results will appear here.' },
        'smart.resourceNoImportedTitle': { zh: '当前画布暂无已导入资源', en: 'No imported media here' },
        'smart.resourceNoImportedBody': { zh: '使用上传按钮，或拖拽资源到画布。', en: 'Use Upload above or drag media onto the canvas.' },
        'smart.resourceEmptyTitle': { zh: '当前画布还没有资源', en: 'There is no media here yet' },
        'smart.resourceEmptyBody': { zh: '上传资源，或先运行生成节点。', en: 'Upload media or run a generation node first.' },
        'smart.resourceReplaceWith': { zh: '替换为 {name}', en: 'Replace with {name}' },
        'smart.resourceReplaced': { zh: '已替换当前资源', en: 'Current media replaced' },
        'smart.resourceRemoveHistory': { zh: '从本画布历史中移除', en: 'Remove from this canvas history' },
        'smart.resourceRemoveHistoryConfirm': { zh: '从本画布历史中移除“{name}”？原始文件不会删除，其他节点和画布不受影响。', en: 'Remove “{name}” from this canvas history? Its source file and other nodes will not be affected.' },
        'smart.resourceRemovedHistory': { zh: '已从本画布历史中移除', en: 'Removed from this canvas history' },
        'smart.resourceMore': { zh: '加载更多（{count}）', en: 'Load more ({count})' },
        'smart.resourceMoreLoad': { zh: '加载更多资源', en: 'Load more media' },
        'smart.resourcePreview': { zh: '放大预览', en: 'Enlarge preview' }
    };

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function fmt(tpl, vars) {
        return String(tpl || '').replace(/\{(\w+)\}/g, function (m, k) {
            return vars && vars[k] != null ? String(vars[k]) : m;
        });
    }
    function defaultText(key, vars) {
        var lang = ((global.document && global.document.documentElement && global.document.documentElement.lang) || '').toLowerCase();
        var entry = DEFAULT_TEXT[key] || {};
        var base = lang.indexOf('zh') === 0 ? (entry.zh || entry.en) : (entry.en || entry.zh);
        return fmt(base || key, vars);
    }
    function defaultKind() { return 'image'; }
    function defaultThumb(item) {
        if (!item) return '';
        var kind = String((item && item.kind) || 'image');
        if (kind === 'video') {
            return '<video src="' + esc(item.url || '') + '" muted loop playsinline preload="metadata"></video>';
        }
        if (kind === 'audio') {
            return '<div class="upload-resource-thumb-audio"><i data-lucide="file-audio"></i></div>';
        }
        return '<img src="' + esc(item.url || '') + '" loading="lazy" decoding="async" alt="">';
    }
    function mediaKind(item) {
        var k = String((item && item.kind) || '').toLowerCase();
        if (k === 'video' || k === 'audio') return k;
        return 'image';
    }

    function createResourcePreviewPanel(opts) {
        opts = opts || {};
        var mount = opts.mount || global.document.body;
        var getItems = typeof opts.getItems === 'function' ? opts.getItems : function () { return { items: [], targetIdentity: '' }; };
        var onSelect = typeof opts.onSelect === 'function' ? opts.onSelect : function () {};
        var onRemove = typeof opts.onRemove === 'function' ? opts.onRemove : function () { return false; };
        var renderThumb = typeof opts.renderThumb === 'function' ? opts.renderThumb : defaultThumb;
        var previewSrc = typeof opts.previewSrc === 'function' ? opts.previewSrc : function (item) { return item && item.url; };
        var sourceLabel = typeof opts.sourceLabel === 'function' ? opts.sourceLabel : function () { return ''; };
        var text = typeof opts.text === 'function' ? opts.text : defaultText;
        var panelWidth = Number(opts.panelWidth) || 620;

        var state = { open: false, nodeId: '', imageIndex: -1, trigger: null, tab: 'all', scope: 'all', query: '', view: 'grid', sort: 'recent', visibleCount: 0 };
        var panelEl = null;
        var previewEl = null;
        var items = [];
        var targetIdentity = '';
        var baseItems = [];
        var kindLabel = defaultKind();
        var searchTimer = null;
        var relayoutTimer = null;
        var layoutWidth = 0;
        var measuredAspectByUrl = new Map();
        var measuredSizeByUrl = new Map();
        var measuredDurationByUrl = new Map();

        // ---- helpers ------------------------------------------------- (private)
        function escapeA(s) { return esc(s); }

        function ctx() { return { nodeId: state.nodeId, imageIndex: state.imageIndex, trigger: state.trigger }; }

        function refreshBase() {
            var result = getItems(ctx());
            baseItems = (result && result.items) || [];
            targetIdentity = (result && result.targetIdentity) || '';
            kindLabel = (result && result.kindLabel) || defaultKind();
        }
        function filteredItems() {
            refreshBase();
            var keyword = String(state.query || '').trim().toLocaleLowerCase();
            var out = baseItems.filter(function (item) {
                return state.tab === 'all' || item.source === state.tab;
            }).filter(function (item) {
                return state.scope === 'all' || (state.scope === 'active' ? item.active : !item.active);
            }).filter(function (item) {
                if (!keyword) return true;
                var hay = ((item.name || '') + ' ' + (sourceLabel(item) || '')).toLocaleLowerCase();
                return hay.indexOf(keyword) >= 0;
            });
            if (state.sort === 'name-asc') out.sort(function (a, b) { return String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'); });
            else if (state.sort === 'name-desc') out.sort(function (a, b) { return String(b.name || '').localeCompare(String(a.name || ''), 'zh-CN'); });
            else out = out.reverse();
            return out;
        }
        function emptyCopy() {
            if ((state.query || '').trim()) return { title: text('smart.resourceNoMatchTitle'), body: text('smart.resourceNoMatchBody') };
            if (state.tab === 'generated') return { title: text('smart.resourceNoGeneratedTitle'), body: text('smart.resourceNoGeneratedBody') };
            if (state.tab === 'imported') return { title: text('smart.resourceNoImportedTitle'), body: text('smart.resourceNoImportedBody') };
            return { title: text('smart.resourceEmptyTitle'), body: text('smart.resourceEmptyBody') };
        }
        function sortIcon() {
            return state.sort === 'name-asc' ? 'arrow-down-a-z' : state.sort === 'name-desc' ? 'arrow-up-a-z' : 'arrow-down-up';
        }
        function sortLabel() {
            return state.sort === 'name-asc' ? text('smart.resourceSortNameAsc') : state.sort === 'name-desc' ? text('smart.resourceSortNameDesc') : text('smart.resourceSortRecent');
        }
        function scopeLabel() {
            return state.scope === 'active' ? text('smart.resourceScopeActive') : state.scope === 'history' ? text('smart.resourceScopeHistory') : text('smart.resourceScopeAll');
        }
        function countBy(source) {
            return baseItems.filter(function (item) { return source === 'all' || item.source === source; }).length;
        }

        // ---- container -------------------------------------------------
        function ensurePanel() {
            if (panelEl && panelEl.isConnected) return panelEl;
            panelEl = global.document.createElement('section');
            panelEl.id = 'uploadResourcePicker';
            panelEl.className = 'upload-resource-picker';
            panelEl.setAttribute('role', 'dialog');
            panelEl.setAttribute('aria-label', text('smart.resourcePickerLabel', { kind: kindLabel }));
            panelEl.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
            panelEl.addEventListener('mousedown', function (e) { e.stopPropagation(); });
            panelEl.addEventListener('click', function (e) { e.stopPropagation(); });
            panelEl.addEventListener('wheel', function (e) { e.stopPropagation(); }, { passive: true });
            mount.appendChild(panelEl);
            bindEvents(panelEl);
            return panelEl;
        }

        // ---- layout ----------------------------------------------------
        function contentWidth() {
            var w = 0;
            var gridEl = panelEl && panelEl.querySelector('.upload-resource-grid');
            if (gridEl) {
                var cs = global.getComputedStyle(gridEl);
                var pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
                w = gridEl.clientWidth - pad;
            }
            if (w <= 0 && panelEl) w = panelEl.clientWidth - 28;
            if (w > 0) layoutWidth = w;
            return Math.max(w > 0 ? w : (layoutWidth || 560), 160);
        }
        function itemAspect(item) {
            var w = Number(item && (item.natural_w || item.width || item.w) || 0);
            var h = Number(item && (item.natural_h || item.height || item.h) || 0);
            if (w > 0 && h > 0) return w / h;
            var measured = measuredAspectByUrl.get(String(item && item.url || ''));
            if (measured > 0) return measured;
            return 4 / 3;
        }
        function resolution(item) {
            var w = Number(item && (item.natural_w || item.width || item.w) || 0);
            var h = Number(item && (item.natural_h || item.height || item.h) || 0);
            if (w > 0 && h > 0) return Math.round(w) + ' x ' + Math.round(h);
            var size = measuredSizeByUrl.get(String(item && item.url || ''));
            if (size && size.w > 0 && size.h > 0) return Math.round(size.w) + ' x ' + Math.round(size.h);
            return '';
        }
        function durationLabel(seconds) {
            var v = Math.max(0, Number(seconds) || 0);
            var h = Math.floor(v / 3600);
            var m = Math.floor((v % 3600) / 60);
            var s = Math.floor(v % 60);
            var mm = String(m).padStart(2, '0');
            var ss = String(s).padStart(2, '0');
            return h > 0 ? h + ':' + mm + ':' + ss : mm + ':' + ss;
        }
        function itemMeta(item) {
            var kind = mediaKind(item);
            if (kind === 'video' || kind === 'audio') {
                var url = String(item && item.url || '');
                var d = Number(item && (item.duration || item.duration_sec) || 0) || measuredDurationByUrl.get(url) || 0;
                return d > 0 ? durationLabel(d) : '';
            }
            return resolution(item);
        }

        function previewButtonHtml(index) {
            return '<button class="upload-resource-preview-btn" type="button" data-upload-resource-preview="' + index + '" title="' + escapeA(text('smart.resourcePreview')) + '" aria-label="' + escapeA(text('smart.resourcePreview')) + '"><i data-lucide="zoom-in"></i></button>';
        }
        function cellHtml(item, index, width, height) {
            var selected = item.identity === targetIdentity;
            var remove = (state.scope === 'history' && !item.active)
                ? '<button class="upload-resource-remove" type="button" data-upload-resource-remove="' + index + '" title="' + escapeA(text('smart.resourceRemoveHistory')) + '" aria-label="' + escapeA(text('smart.resourceRemoveHistory')) + '"><i data-lucide="trash-2"></i></button>'
                : '';
            return '<div class="upload-resource-item upload-resource-justified-item ' + (selected ? 'selected' : '') + '" style="width:' + width + 'px">' +
                '<div class="upload-resource-select-item" role="option" tabindex="0" aria-selected="' + selected + '" data-upload-resource-select="' + index + '" title="' + escapeA(text('smart.resourceReplaceWith', { name: item.name })) + '">' +
                '<span class="upload-resource-thumb" style="height:' + height + 'px;aspect-ratio:auto">' + (renderThumb(item, ctx())) + previewButtonHtml(index) + '</span>' +
                '<span class="upload-resource-item-name">' + esc(item.name) + '</span>' +
                '<span class="upload-resource-item-source">' + esc(itemMeta(item)) + '</span>' +
                '</div>' + remove + '</div>';
        }
        function listCellHtml(item, index) {
            var selected = item.identity === targetIdentity;
            var remove = (state.scope === 'history' && !item.active)
                ? '<button class="upload-resource-remove" type="button" data-upload-resource-remove="' + index + '" title="' + escapeA(text('smart.resourceRemoveHistory')) + '" aria-label="' + escapeA(text('smart.resourceRemoveHistory')) + '"><i data-lucide="trash-2"></i></button>'
                : '';
            return '<div class="upload-resource-item ' + (selected ? 'selected' : '') + '">' +
                '<div class="upload-resource-select-item" role="option" tabindex="0" aria-selected="' + selected + '" data-upload-resource-select="' + index + '" title="' + escapeA(text('smart.resourceReplaceWith', { name: item.name })) + '">' +
                '<span class="upload-resource-thumb">' + (renderThumb(item, ctx())) + previewButtonHtml(index) + '</span>' +
                '<span class="upload-resource-item-name">' + esc(item.name) + '</span>' +
                '<span class="upload-resource-item-source">' + esc(itemMeta(item)) + '</span>' +
                '</div>' + remove + '</div>';
        }
        function rowsHtml(visible, listView) {
            if (listView) return visible.map(function (item, index) { return listCellHtml(item, index); }).join('');
            var contentW = Math.max(contentWidth(), 160);
            var rows = [];
            var row = [];
            var aspectSum = 0;
            function flush() {
                if (!row.length) return;
                var n = row.length;
                var rowH = Math.max(24, (contentW - GAP * (n - 1)) / aspectSum);
                rows.push('<div class="upload-resource-row">' + row.map(function (entry) {
                    return cellHtml(entry.item, entry.index, Math.round(Math.max(8, itemAspect(entry.item) * rowH)), Math.round(rowH));
                }).join('') + '</div>');
                row = [];
                aspectSum = 0;
            }
            visible.forEach(function (item, index) {
                var a = itemAspect(item);
                if (row.length && (aspectSum + a) * TARGET_H + GAP * row.length > contentW) flush();
                row.push({ item: item, index: index });
                aspectSum += a;
            });
            flush();
            return rows.join('');
        }
        function gridHtml(listView) {
            var visibleCount = Math.max(CHUNK, Number(state.visibleCount) || 0);
            var visible = items.slice(0, visibleCount);
            var remaining = items.length - visible.length;
            var body;
            if (!visible.length) {
                var empty = emptyCopy();
                body = '<div class="upload-resource-empty"><strong>' + esc(empty.title) + '</strong><span>' + esc(empty.body) + '</span></div>';
            } else {
                body = rowsHtml(visible, listView);
            }
            return body + (remaining > 0 ? '<button class="upload-resource-more" type="button" data-upload-resource-more="1" title="' + escapeA(text('smart.resourceMoreLoad')) + '"><span>' + esc(text('smart.resourceMore', { count: remaining })) + '</span><i data-lucide="chevron-down"></i></button>' : '');
        }

        // ---- render ----------------------------------------------------
        function render(opts) {
            opts = opts || {};
            var gridOnly = !!opts.gridOnly;
            var focusSearch = !!opts.focusSearch;
            items = filteredItems();
            var tabs = [['all', text('smart.resourceAll')], ['imported', text('smart.resourceImported')], ['generated', text('smart.resourceGenerated')]];
            var picker = ensurePanel();
            if (!gridOnly) {
                picker.setAttribute('aria-label', text('smart.resourcePickerLabel', { kind: kindLabel }));
                picker.innerHTML =
                    '<div class="upload-resource-picker-head">' +
                    '<div class="upload-resource-tabs" role="tablist" aria-label="' + escapeA(text('smart.resourcePickerRange')) + '">' +
                    tabs.map(function (t) {
                        return '<button class="upload-resource-tab ' + (state.tab === t[0] ? 'active' : '') + '" type="button" role="tab" data-upload-resource-tab="' + t[0] + '" aria-selected="' + (state.tab === t[0]) + '">' + esc(t[1]) + '<span class="sr-only">' + esc(text('smart.resourceTabCount', { n: countBy(t[0]) })) + '</span></button>';
                    }).join('') +
                    '</div></div>' +
                    '<div class="upload-resource-toolbar">' +
                    '<label class="upload-resource-search"><i data-lucide="search"></i><input data-upload-resource-search type="search" value="' + escapeA(state.query) + '" placeholder="' + escapeA(text('smart.resourceSearch')) + '" aria-label="' + escapeA(text('smart.resourceSearch')) + '"></label>' +
                    '<button class="upload-resource-tool" type="button" data-upload-resource-sort title="' + escapeA(sortLabel()) + '" aria-label="' + escapeA(sortLabel()) + '"><i data-lucide="' + sortIcon() + '"></i></button>' +
                    '<button class="upload-resource-tool" type="button" data-upload-resource-scope title="' + escapeA(scopeLabel()) + '" aria-label="' + escapeA(scopeLabel()) + '"><i data-lucide="' + (state.scope === 'history' ? 'history' : state.scope === 'active' ? 'eye' : 'layers') + '"></i></button>' +
                    '<button class="upload-resource-tool ' + (state.view === 'grid' ? 'active' : '') + '" type="button" data-upload-resource-view="grid" title="' + escapeA(text('smart.resourceGridView')) + '" aria-label="' + escapeA(text('smart.resourceGridView')) + '" aria-pressed="' + (state.view === 'grid') + '"><i data-lucide="grid-2x2"></i></button>' +
                    '<button class="upload-resource-tool ' + (state.view === 'list' ? 'active' : '') + '" type="button" data-upload-resource-view="list" title="' + escapeA(text('smart.resourceListView')) + '" aria-label="' + escapeA(text('smart.resourceListView')) + '" aria-pressed="' + (state.view === 'list') + '"><i data-lucide="list"></i></button>' +
                    '</div>' +
                    '<div class="upload-resource-count" aria-live="polite">' + esc(text('smart.resourceCanvasMediaCount', { n: items.length, kind: kindLabel })) + '</div>' +
                    '<div class="upload-resource-grid ' + (state.view === 'list' ? 'is-list' : '') + '" role="listbox" aria-label="' + escapeA(text('smart.resourcePickerLabel', { kind: kindLabel })) + '">' + gridHtml(state.view === 'list') + '</div>';
            } else {
                var countEl = picker.querySelector('.upload-resource-count');
                if (countEl) countEl.innerHTML = esc(text('smart.resourceCanvasMediaCount', { n: items.length, kind: kindLabel }));
                var gridEl = picker.querySelector('.upload-resource-grid');
                if (gridEl) {
                    var prevScroll = gridEl.scrollTop;
                    gridEl.innerHTML = gridHtml(state.view === 'list');
                    gridEl.scrollTop = prevScroll;
                }
                var paneLabel = picker.querySelector('[role="option"]');
                if (paneLabel) void paneLabel;
            }
            bindAspectMeasure(picker);
            refreshIcons(panelEl);
            global.requestAnimationFrame(function () {
                positionPanel();
                if (focusSearch) picker.querySelector('[data-upload-resource-search]') && picker.querySelector('[data-upload-resource-search]').focus();
                scheduleRelayout();
            });
        }

        // ---- refresh icons (scoped) -----------------------------------
        function refreshIcons(scopeEl) {
            if (global.lucide && global.lucide.createIcons) {
                try { global.lucide.createIcons(); } catch (e) { /* ignore */ }
            }
        }

        // ---- sizing self-correction -----------------------------------
        function scheduleRelayout(force) {
            clearTimeout(relayoutTimer);
            relayoutTimer = setTimeout(function () {
                if (!state.open) return;
                if (force) { render({ gridOnly: true }); return; }
                var gridEl = panelEl && panelEl.querySelector('.upload-resource-grid');
                if (!gridEl) return;
                var cs = global.getComputedStyle(gridEl);
                var pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
                var real = Math.max(gridEl.clientWidth - pad, 160);
                if (Math.abs(real - layoutWidth) > 6) {
                    layoutWidth = real;
                    render({ gridOnly: true });
                }
            }, force ? 120 : 0);
        }

        // ---- aspect / duration measurement ---------------------------
        function bindAspectMeasure(picker) {
            picker.querySelectorAll('.upload-resource-select-item').forEach(function (sel) {
                var index = Number(sel.dataset.uploadResourceSelect);
                if (!Number.isInteger(index)) return;
                var item = items[index];
                if (!item) return;
                var url = String(item.url || '');
                var kind = mediaKind(item);
                if (kind === 'video' || kind === 'audio') {
                    if (measuredDurationByUrl.has(url)) return;
                    var stored = Number(item.duration || item.duration_sec || 0);
                    if (stored > 0) { measuredDurationByUrl.set(url, stored); return; }
                    measuredDurationByUrl.set(url, 0);
                    probeDuration(item.url, kind).then(function (d) {
                        if (d > 0) { measuredDurationByUrl.set(url, d); scheduleRelayout(true); }
                    });
                    return;
                }
                if (item.natural_w && item.natural_h) return;
                if (measuredAspectByUrl.has(url)) return;
                var img = sel.querySelector('.upload-resource-thumb img');
                if (!img || img.dataset.aspectMeasured) return;
                img.dataset.aspectMeasured = '1';
                function recordAspect() {
                    if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                        measuredAspectByUrl.set(url, img.naturalWidth / img.naturalHeight);
                        measuredSizeByUrl.set(url, { w: img.naturalWidth, h: img.naturalHeight });
                        scheduleRelayout(true);
                    }
                }
                img.addEventListener('load', recordAspect, { once: true });
                if (img.complete && img.naturalWidth > 0) recordAspect();
            });
        }
        function probeDuration(url, kind) {
            return new Promise(function (resolve) {
                if (!url) { resolve(null); return; }
                var done = false;
                var el;
                function finish(value) {
                    if (done) return;
                    done = true;
                    try { el.src = ''; } catch (e) { /* ignore */ }
                    resolve(value);
                }
                try { el = global.document.createElement(kind === 'video' ? 'video' : 'audio'); } catch (e) { resolve(null); return; }
                el.preload = 'metadata';
                el.addEventListener('loadedmetadata', function () { var d = Number(el.duration); finish(Number.isFinite(d) && d > 0 ? d : null); });
                el.addEventListener('error', function () { finish(null); });
                setTimeout(function () { finish(null); }, 8000);
                try { el.src = url; } catch (e) { finish(null); }
            });
        }

        // ---- preview overlay ------------------------------------------
        function ensurePreview() {
            if (previewEl && previewEl.isConnected) return previewEl;
            previewEl = global.document.createElement('div');
            previewEl.className = 'upload-resource-preview';
            previewEl.hidden = true;
            previewEl.style.display = 'none';
            mount.appendChild(previewEl);
            return previewEl;
        }
        function positionPreview(event) {
            var el = previewEl;
            if (!el || el.hidden || el.style.display === 'none') return;
            var pad = 14;
            var w = el.offsetWidth || 420;
            var h = el.offsetHeight || 420;
            var left = event.clientX - w - 16;
            if (left < pad) left = event.clientX + 16;
            left = Math.max(pad, Math.min(global.window.innerWidth - w - pad, left));
            var top = Math.max(pad, Math.min(global.window.innerHeight - h - pad, event.clientY + 12));
            el.style.left = left + 'px';
            el.style.top = top + 'px';
        }
        function showPreview(event, item) {
            if (!item || !item.url) return;
            var el = ensurePreview();
            var kind = mediaKind(item);
            var wantVideo = kind === 'video';
            var media = el.querySelector('img,video');
            if (!media || (wantVideo && media.tagName.toLowerCase() !== 'video') || (!wantVideo && media.tagName.toLowerCase() !== 'img')) {
                if (media) media.remove();
                media = global.document.createElement(wantVideo ? 'video' : 'img');
                el.appendChild(media);
            }
            var src = previewSrc(item);
            if (wantVideo) {
                media.muted = true; media.loop = true; media.playsInline = true; media.preload = 'metadata'; media.controls = false; media.disablePictureInPicture = true;
                media.setAttribute('disablepictureinpicture', ''); media.setAttribute('controlslist', 'nodownload noplaybackrate noremoteplayback');
                media.src = item.url; media.play && media.play().catch(function () {});
            } else {
                media.loading = 'lazy'; media.decoding = 'async';
                media.src = src; media.alt = 'preview';
            }
            el.hidden = false; el.style.display = 'block';
            positionPreview(event);
        }
        function hidePreview() {
            if (!previewEl) return;
            previewEl.style.display = 'none';
            previewEl.hidden = true;
            var media = previewEl.querySelector('img,video');
            media && media.pause && media.pause();
            media && media.removeAttribute('src');
            media && media.load && media.load();
        }

        function resetPaging() { state.visibleCount = CHUNK; }

        // ---- events ----------------------------------------------------
        function bindEvents(picker) {
            picker.addEventListener('click', function (event) {
                var tab = event.target.closest('[data-upload-resource-tab]');
                if (tab) { resetPaging(); state.tab = tab.dataset.uploadResourceTab || 'all'; render(); return; }
                var sort = event.target.closest('[data-upload-resource-sort]');
                if (sort) { resetPaging(); state.sort = state.sort === 'recent' ? 'name-asc' : (state.sort === 'name-asc' ? 'name-desc' : 'recent'); render(); return; }
                var scope = event.target.closest('[data-upload-resource-scope]');
                if (scope) { resetPaging(); state.scope = state.scope === 'all' ? 'active' : (state.scope === 'active' ? 'history' : 'all'); render(); return; }
                var view = event.target.closest('[data-upload-resource-view]');
                if (view) { resetPaging(); state.view = view.dataset.uploadResourceView === 'list' ? 'list' : 'grid'; render(); return; }
                var more = event.target.closest('[data-upload-resource-more]');
                if (more) { state.visibleCount += CHUNK; render({ gridOnly: true }); return; }
                if (event.target.closest('[data-upload-resource-preview]')) return;
                var selectBtn = event.target.closest('[data-upload-resource-select]');
                if (selectBtn) {
                    var selected = items[Number(selectBtn.dataset.uploadResourceSelect)];
                    if (!selected || !selected.url) return;
                    var panelCtx = ctx();
                    close({ restoreFocus: false });
                    onSelect(selected, panelCtx);
                    return;
                }
                var removeBtn = event.target.closest('[data-upload-resource-remove]');
                if (removeBtn) {
                    event.preventDefault();
                    event.stopPropagation();
                    var item = items[Number(removeBtn.dataset.uploadResourceRemove)];
                    if (!item || item.active) return;
                    if (!global.window.confirm(text('smart.resourceRemoveHistoryConfirm', { name: item.name }))) return;
                    if (onRemove(item)) render();
                }
            });
            picker.addEventListener('input', function (event) {
                if (!event.target.matches('[data-upload-resource-search]')) return;
                state.query = event.target.value || '';
                clearTimeout(searchTimer);
                searchTimer = setTimeout(function () { render({ gridOnly: true }); }, 160);
            });
            picker.addEventListener('mouseover', function (event) {
                var btn = event.target.closest('[data-upload-resource-preview]');
                if (!btn) return;
                var item = items[Number(btn.dataset.uploadResourcePreview)];
                if (item && item.url) showPreview(event, item);
            });
            picker.addEventListener('mouseout', function (event) {
                var btn = event.target.closest('[data-upload-resource-preview]');
                if (!btn) return;
                if (!btn.contains(event.relatedTarget)) hidePreview();
            });
            picker.addEventListener('keydown', function (event) {
                var option = event.target.closest && event.target.closest('[data-upload-resource-select]');
                if (option && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    option.click();
                    return;
                }
                if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].indexOf(event.key) === -1) return;
                var buttons = picker.querySelectorAll('[data-upload-resource-select]');
                var arr = Array.prototype.slice.call(buttons);
                var current = arr.indexOf(global.document.activeElement);
                if (current < 0 || !arr.length) return;
                var columns = state.view === 'list' ? 1 : ((mount && mount.clientWidth && mount.clientWidth <= 640) ? 3 : 4);
                var offset = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : event.key === 'ArrowUp' ? -columns : columns;
                var next = Math.max(0, Math.min(arr.length - 1, current + offset));
                if (next === current) return;
                event.preventDefault();
                arr[next].focus();
            });
        }

        // ---- positioning / open / close --------------------------------
        function positionPanel() {
            var picker = panelEl;
            if (!state.open || !picker || !picker.isConnected) return;
            var trigger = state.trigger;
            if (!trigger || !trigger.isConnected) { close({ restoreFocus: false }); return; }
            var shellRect = mount.getBoundingClientRect();
            var triggerRect = trigger.getBoundingClientRect();
            var pw = picker.offsetWidth || Math.min(panelWidth, shellRect.width - 24);
            var ph = picker.offsetHeight || 360;
            var margin = 12;
            var aboveTop = triggerRect.top - shellRect.top - ph - margin;
            var belowTop = triggerRect.bottom - shellRect.top + margin;
            var placeAbove = aboveTop >= margin || belowTop + ph > shellRect.height - margin;
            var top = placeAbove ? Math.max(margin, aboveTop) : Math.min(Math.max(margin, belowTop), Math.max(margin, shellRect.height - ph - margin));
            var preferredLeft = triggerRect.right - shellRect.left - pw;
            picker.style.left = Math.max(margin, Math.min(preferredLeft, shellRect.width - pw - margin)) + 'px';
            picker.style.top = top + 'px';
            picker.dataset.placement = placeAbove ? 'above' : 'below';
        }

        function open(context) {
            context = context || {};
            var nodeId = context.nodeId, imageIndex = context.imageIndex, trigger = context.trigger;
            if (state.open && state.nodeId === nodeId && state.imageIndex === imageIndex) { close(); return; }
            close({ restoreFocus: false });
            state.open = true;
            state.nodeId = nodeId;
            state.imageIndex = imageIndex;
            state.tab = 'all';
            state.scope = 'all';
            state.query = '';
            state.sort = 'recent';
            state.visibleCount = CHUNK;
            state.trigger = trigger || null;
            var picker = ensurePanel();
            picker.classList.add('open');
            if (trigger && trigger.setAttribute) trigger.setAttribute('aria-expanded', 'true');
            render({ focusSearch: true });
        }
        function close(optsBt) {
            var restoreFocus = !optsBt || optsBt.restoreFocus !== false;
            var trigger = state.trigger;
            state.open = false;
            state.nodeId = '';
            state.imageIndex = -1;
            state.trigger = null;
            clearTimeout(searchTimer);
            hidePreview();
            if (panelEl) panelEl.classList.remove('open');
            if (trigger && trigger.setAttribute) trigger.setAttribute('aria-expanded', 'false');
            if (restoreFocus && trigger && trigger.isConnected) global.requestAnimationFrame(function () { trigger.focus(); });
        }
        function refresh() {
            if (state.open) render({ gridOnly: true });
        }

        return {
            open: open,
            close: close,
            refresh: refresh,
            isOpen: function () { return state.open; },
            reposition: function () { positionPanel(); },
            contains: function (el) { return !!(panelEl && panelEl.contains(el)); }
        };
    }

    global.createResourcePreviewPanel = createResourcePreviewPanel;
})(typeof window !== 'undefined' ? window : this);
