/*
 * Infinite Canvas —— 全屏遮罩编辑器
 *
 * 原生 ES2019，无依赖、无构建器、无 import/export，整体挂在 window.MaskEditor。
 * 交互与笔刷手感移植自 Goohaitools 的 ComfyUI 遮罩编辑器，但完全去掉了
 * ComfyUI / app / api / LiteGraph 节点、上传、扩展注册与预览观察器逻辑。
 *
 * 对外 API：
 *   window.MaskEditor.open({
 *       imageUrl,        // 必填：原图地址（同源 /assets、/output 或 blob:）
 *       maskUrl,         // 可选：旧遮罩（不透明黑白 PNG，白色 = 已涂抹）
 *       labels,          // 必填：全部界面文案，由调用方传入
 *       hideColorMode,   // 可选：true = 只做遮罩，不渲染「仅遮罩 / 颜色叠加」模式切换
 *       promptBox,       // 可选：{ guide, placeholder, value }，在底部中央放一个提示词输入框
 *       onSave           // async (result) => {}，被 await；抛错则留在编辑器
 *   }) → Promise<boolean>，true = 已保存，false = 取消 / 关闭
 *
 * result = {
 *   maskBlob,   // 与原图同尺寸的 PNG，涂抹区 RGB=255 其余 0，alpha 恒 255
 *   colorBlob,  // 'color' 模式必有：原图 + 半透明色块，全不透明
 *   mode, colorName, opacity, filled, inverted, width, height,
 *   prompt      // 只有传了 promptBox 才有：输入框内容（已 trim）
 * }
 *
 * 撤销/重做使用"矢量操作列表 + 重放"，不保存整幅 ImageData 快照，
 * 避免 1536² 每步约 9MB 的内存膨胀。
 */
(function () {
    'use strict';

    // ===== 常量 =====
    const MAX_EDIT_LONG_SIDE = 1536; // 编辑画布长边上限，保存时按原图尺寸最近邻放大
    const MAX_OPS = 200;             // 操作列表上限，超出丢弃最旧（基线永远保留）
    const MIN_SCALE = 0.05;          // 5%
    const MAX_SCALE = 12;            // 1200%
    const MAX_FIT_SCALE = 8;         // 自动适应时的最大放大倍数
    const MIN_BRUSH = 2;
    const MAX_BRUSH = 300;
    const DEFAULT_BRUSH = 30;
    const DEFAULT_OPACITY = 55;
    const PAINT_ALPHA = 5;           // alpha 大于该值视为"已涂抹"
    const MASK_TINT = '#22d3ee';     // 仅遮罩模式的单色叠加色（青色，深浅底图都可见）
    const ROOT_Z_INDEX = '2147483000';
    const IMAGE_LOAD_TIMEOUT = 30000; // 底图载入兜底超时（毫秒），超时按 loadFail 处理

    // 9 个叠加色；每个颜色是一张独立图层，任一像素只属于其中一层
    const COLORS = {
        white: '#ffffff',
        red: '#ef4444',
        orange: '#f97316',
        yellow: '#facc15',
        green: '#22c55e',
        cyan: '#06b6d4',
        blue: '#3b82f6',
        purple: '#a855f7',
        gray: '#94a3b8'
    };
    const COLOR_ORDER = ['white', 'red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'purple', 'gray'];
    const COLOR_LABEL_KEYS = {
        white: 'colorWhite',
        red: 'colorRed',
        orange: 'colorOrange',
        yellow: 'colorYellow',
        green: 'colorGreen',
        cyan: 'colorCyan',
        blue: 'colorBlue',
        purple: 'colorPurple',
        gray: 'colorGray'
    };

    // 内联 SVG 图标（不依赖 lucide），统一 16×16、currentColor 描边
    const SVG_OPEN = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
        'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" ' +
        'stroke-linejoin="round" aria-hidden="true">';
    const ICONS = {
        brush: SVG_OPEN +
            '<path d="M3.5 20.5l4.6-1.2 10.6-10.6a2.1 2.1 0 0 0-3-3L5.1 16.3l-1.6 4.2z"/>' +
            '<path d="M14.6 6.4l3 3"/></svg>',
        eraser: SVG_OPEN +
            '<path d="M14 20H8.5L4 15.5a2 2 0 0 1 0-2.9l7.6-7.6a2 2 0 0 1 2.9 0l4 4a2 2 0 0 1 0 2.9L14 20z"/>' +
            '<path d="M9 9.5l6 6"/></svg>',
        marquee: SVG_OPEN +
            '<rect x="3.2" y="4.2" width="17.6" height="15.6" rx="2" stroke-dasharray="4 3"/></svg>',
        undo: SVG_OPEN + '<path d="M9.5 14.5L5 10l4.5-4.5"/><path d="M5 10h9a5 5 0 0 1 0 10h-3"/></svg>',
        redo: SVG_OPEN + '<path d="M14.5 14.5L19 10l-4.5-4.5"/><path d="M19 10h-9a5 5 0 0 0 0 10h3"/></svg>',
        invert: SVG_OPEN +
            '<circle cx="12" cy="12" r="8.6"/>' +
            '<path d="M12 3.4a8.6 8.6 0 0 1 0 17.2z" fill="currentColor" stroke="none"/></svg>',
        trash: SVG_OPEN +
            '<path d="M4.2 7h15.6"/><path d="M9.4 7V4.8h5.2V7"/>' +
            '<path d="M6.6 7l.9 12.2h9l.9-12.2"/></svg>',
        close: SVG_OPEN + '<path d="M6.4 6.4l11.2 11.2M17.6 6.4L6.4 17.6"/></svg>',
        check: SVG_OPEN + '<path d="M4.8 12.6l4.6 4.6L19.4 7.2"/></svg>',
        fit: SVG_OPEN +
            '<path d="M4 9.2V4h5.2M20 9.2V4h-5.2M4 14.8V20h5.2M20 14.8V20h-5.2"/></svg>',
        plus: SVG_OPEN + '<path d="M12 5.2v13.6M5.2 12h13.6"/></svg>',
        minus: SVG_OPEN + '<path d="M5.2 12h13.6"/></svg>'
    };

    // ===== 小工具 =====
    function clamp(value, min, max) {
        if (!Number.isFinite(value)) return min;
        return value < min ? min : (value > max ? max : value);
    }

    function createCanvas(width, height) {
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(width));
        canvas.height = Math.max(1, Math.round(height));
        return canvas;
    }

    function hexToRgb(hex) {
        const value = String(hex || '').replace('#', '');
        return [0, 2, 4].map(function (index) {
            const channel = parseInt(value.slice(index, index + 2), 16);
            return Number.isFinite(channel) ? channel : 0;
        });
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    function isEditableTarget(target) {
        if (!target || !(target instanceof HTMLElement)) return false;
        const tag = target.tagName;
        return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
    }

    /**
     * 等待图片可解码。
     * 只监听 load/error 在个别环境下会丢事件（图片已进入 complete 状态却没派发事件，
     * 编辑器就会永远卡在"未就绪"），因此额外用轮询兜底，并给一个总超时。
     */
    function waitForImage(image) {
        return new Promise(function (resolve, reject) {
            let settled = false;
            let pollTimer = 0;
            let timeoutTimer = 0;

            function cleanup() {
                settled = true;
                if (pollTimer) clearInterval(pollTimer);
                if (timeoutTimer) clearTimeout(timeoutTimer);
                pollTimer = 0;
                timeoutTimer = 0;
                image.removeEventListener('load', onLoad);
                image.removeEventListener('error', onError);
            }

            function decide() {
                if (settled || !image.complete) return;
                if (image.naturalWidth > 0) {
                    cleanup();
                    resolve(image);
                } else {
                    cleanup();
                    reject(new Error('image load failed'));
                }
            }

            function onLoad() {
                decide();
            }

            function onError() {
                if (settled) return;
                cleanup();
                reject(new Error('image load failed'));
            }

            image.addEventListener('load', onLoad);
            image.addEventListener('error', onError);
            decide();
            if (settled) return;
            pollTimer = setInterval(decide, 60);
            timeoutTimer = setTimeout(function () {
                if (settled) return;
                cleanup();
                reject(new Error('image load timeout'));
            }, IMAGE_LOAD_TIMEOUT);
        });
    }

    function canvasToBlob(canvas) {
        return new Promise(function (resolve, reject) {
            try {
                canvas.toBlob(function (blob) {
                    if (blob) resolve(blob);
                    else reject(new Error('canvas.toBlob failed'));
                }, 'image/png');
            } catch (error) {
                reject(error);
            }
        });
    }

    /**
     * 单个颜色层的"自动填充漏洞"。
     * 以该层已画像素为墙，从包围盒四边 BFS 标出外部；未被外部连通的封闭空洞填实。
     * 返回新 canvas（不修改真值层）；整层为空时返回 null。
     */
    function fillHolesInLayer(sourceCanvas, rgb) {
        const width = sourceCanvas.width;
        const height = sourceCanvas.height;
        const sourceCtx = sourceCanvas.getContext('2d', { willReadFrequently: true });
        const source = sourceCtx.getImageData(0, 0, width, height);
        const pixels = source.data;
        const total = width * height;
        const wall = new Uint8Array(total);
        let minX = width;
        let minY = height;
        let maxX = -1;
        let maxY = -1;
        for (let index = 0; index < total; index++) {
            if (pixels[index * 4 + 3] <= PAINT_ALPHA) continue;
            wall[index] = 1;
            const x = index % width;
            const y = (index / width) | 0;
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
        }
        if (maxX < 0) return null;
        // 只在有笔迹的包围盒（外扩 1px）内做 BFS，避免全画布扫描
        minX = Math.max(0, minX - 1);
        minY = Math.max(0, minY - 1);
        maxX = Math.min(width - 1, maxX + 1);
        maxY = Math.min(height - 1, maxY + 1);
        const spanW = maxX - minX + 1;
        const spanH = maxY - minY + 1;
        const outside = new Uint8Array(total);
        const queue = new Int32Array(spanW * spanH);
        let head = 0;
        let tail = 0;
        function visit(x, y) {
            if (x < minX || y < minY || x > maxX || y > maxY) return;
            const index = y * width + x;
            if (wall[index] || outside[index]) return;
            outside[index] = 1;
            queue[tail++] = index;
        }
        for (let x = minX; x <= maxX; x++) {
            visit(x, minY);
            visit(x, maxY);
        }
        for (let y = minY; y <= maxY; y++) {
            visit(minX, y);
            visit(maxX, y);
        }
        while (head < tail) {
            const index = queue[head++];
            const x = index % width;
            const y = (index / width) | 0;
            visit(x - 1, y);
            visit(x + 1, y);
            visit(x, y - 1);
            visit(x, y + 1);
        }
        const output = sourceCtx.createImageData(width, height);
        const data = output.data;
        for (let y = minY; y <= maxY; y++) {
            for (let x = minX; x <= maxX; x++) {
                const index = y * width + x;
                // 墙本身 + 未被外部连通的封闭空洞都要填
                if (!wall[index] && outside[index]) continue;
                const offset = index * 4;
                data[offset] = rgb[0];
                data[offset + 1] = rgb[1];
                data[offset + 2] = rgb[2];
                data[offset + 3] = 255;
            }
        }
        const canvas = createCanvas(width, height);
        canvas.getContext('2d').putImageData(output, 0, 0);
        return canvas;
    }

    /**
     * 从种子像素出发，收集四连通、alpha > PAINT_ALPHA 的像素。
     * 返回 Uint8Array 掩码（长度 = width * height），便于后续按索引遍历。
     */
    function collectPaintedComponent(canvas, seedX, seedY) {
        const width = canvas.width;
        const height = canvas.height;
        if (seedX < 0 || seedY < 0 || seedX >= width || seedY >= height) return null;
        const total = width * height;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        const data = context.getImageData(0, 0, width, height).data;
        const start = seedY * width + seedX;
        if (data[start * 4 + 3] <= PAINT_ALPHA) return null;
        const visited = new Uint8Array(total);
        const stack = new Int32Array(total);
        let top = 0;
        stack[top++] = start;
        visited[start] = 1;
        while (top > 0) {
            const index = stack[--top];
            const x = index % width;
            const y = (index / width) | 0;
            if (x > 0) {
                const next = index - 1;
                if (!visited[next] && data[next * 4 + 3] > PAINT_ALPHA) {
                    visited[next] = 1;
                    stack[top++] = next;
                }
            }
            if (x < width - 1) {
                const next = index + 1;
                if (!visited[next] && data[next * 4 + 3] > PAINT_ALPHA) {
                    visited[next] = 1;
                    stack[top++] = next;
                }
            }
            if (y > 0) {
                const next = index - width;
                if (!visited[next] && data[next * 4 + 3] > PAINT_ALPHA) {
                    visited[next] = 1;
                    stack[top++] = next;
                }
            }
            if (y < height - 1) {
                const next = index + width;
                if (!visited[next] && data[next * 4 + 3] > PAINT_ALPHA) {
                    visited[next] = 1;
                    stack[top++] = next;
                }
            }
        }
        return visited;
    }

    // ===== 编辑器会话 =====
    class MaskEditorSession {
        constructor(opts) {
            this.opts = opts || {};
            this.labels = this.opts.labels || {};
            this.imageUrl = this.opts.imageUrl;
            this.maskUrl = this.opts.maskUrl || '';
            // 颜色叠加产物：colorUrl 是"原图 + 色块"的效果图，paintUrl 是只含纯色块的小图，
            // 重新打开时用 paintUrl 精确还原每个像素属于哪种颜色（并用于回显上次的叠加设置）。
            this.colorUrl = this.opts.colorUrl || '';
            this.paintUrl = this.opts.paintUrl || '';
            this.onSave = typeof this.opts.onSave === 'function' ? this.opts.onSave : null;
            // 特化场景（如「移除重绘」）只需要遮罩，不提供颜色叠加模式，也不渲染模式切换。
            this.hideColorMode = this.opts.hideColorMode === true;
            // 可选：底部居中的提示词输入框 { guide, placeholder, value }，提交时随结果回传给调用方。
            this.promptBox = this.opts.promptBox && typeof this.opts.promptBox === 'object' ? this.opts.promptBox : null;

            // 尺寸
            this.naturalWidth = 0;
            this.naturalHeight = 0;
            this.editWidth = 0;
            this.editHeight = 0;

            // 图层真值：颜色名 -> { canvas, ctx }
            this.layers = new Map();
            this.filledLayers = new Map(); // 颜色名 -> canvas（自动填充后的显示/导出视图）
            this.dirtyFill = new Set();
            this.fillReady = false;

            // 矢量操作历史
            this.ops = [];
            this.redoOps = [];

            // 工具与输出设置
            this.tool = 'brush';
            this.brushSize = DEFAULT_BRUSH;
            this.colorName = 'red';
            this.mode = this.hideColorMode ? 'mask' : (this.opts.mode === 'color' ? 'color' : 'mask');
            this.opacity = Number.isFinite(Number(this.opts.opacity))
                ? clamp(Number(this.opts.opacity), 0, 100)
                : DEFAULT_OPACITY;
            this.fillHoles = true;
            this.inverted = false;

            // 视图
            this.scale = 1;
            this.offset = { x: 0, y: 0 };
            this.userAdjusted = false;

            // 交互瞬时状态
            this.spaceDown = false;
            this.altDown = false;
            this.shiftDown = false;
            this.drawing = null;
            this.marquee = null;
            this.pan = null;
            this.rightDrag = null;
            this.hover = null;
            this.activePointerId = null;

            this.busy = false;
            this.closed = false;
            this.frameRequest = 0;
            this.toastTimer = 0;
            this.colorMenu = null;
            this.colorMenuTimer = 0;

            this.resolvePromise = null;
            this.promise = new Promise((resolve) => {
                this.resolvePromise = resolve;
            });
        }

        // ---- 文案 ----
        label(key) {
            const value = this.labels ? this.labels[key] : '';
            return value == null ? '' : String(value);
        }

        // ---- 生命周期 ----
        async start() {
            this.buildDom();
            this.bindEvents();
            this.updateToolbar();
            this.updatePanel();
            this.updateHistory();
            try {
                await this.loadSource();
                await this.loadExistingMask();
                this.markAllDirty();
                this.render();
                this.fitToStage();
                this.root.classList.add('is-ready');
            } catch (error) {
                this.showError(this.label('loadFail'));
                if (window.console && console.warn) console.warn('MaskEditor: 图片加载失败', error);
            }
            return this.promise;
        }

        buildDom() {
            const labels = this.labels;
            const root = document.createElement('div');
            root.className = 'me-root';
            root.tabIndex = -1;
            root.setAttribute('role', 'dialog');
            root.setAttribute('aria-modal', 'true');
            root.setAttribute('aria-label', this.label('title'));
            root.style.zIndex = ROOT_Z_INDEX;

            const toolButtons = [
                { tool: 'brush', icon: ICONS.brush, text: labels.toolBrush, key: 'B' },
                { tool: 'eraser', icon: ICONS.eraser, text: labels.toolEraser, key: 'E' },
                { tool: 'marquee', icon: ICONS.marquee, text: labels.toolMarquee, key: 'M' }
            ].map(function (item) {
                return '<button type="button" class="me-tool" data-tool="' + item.tool + '" ' +
                    'title="' + escapeHtml(item.text) + ' (' + item.key + ')">' +
                    item.icon + '<span class="me-tool-text">' + escapeHtml(item.text) + '</span></button>';
            }).join('');

            const swatches = COLOR_ORDER.map(function (name) {
                return '<button type="button" class="me-swatch" data-color="' + name + '" ' +
                    'title="' + escapeHtml(labels[COLOR_LABEL_KEYS[name]]) + '" ' +
                    'aria-label="' + escapeHtml(labels[COLOR_LABEL_KEYS[name]]) + '"></button>';
            }).join('');

            // 「移除重绘」这类只需遮罩的场景：不渲染颜色叠加模式切换
            const modeSeg = this.hideColorMode ? '' :
                '<div class="me-seg">' +
                    '<button type="button" class="me-seg-btn" data-mode="mask">' +
                        escapeHtml(labels.modeMask) + '</button>' +
                    '<button type="button" class="me-seg-btn" data-mode="color">' +
                        escapeHtml(labels.modeColor) + '</button>' +
                '</div>';

            // 底部居中的提示词输入框（调用方决定是否出现，以及文案）
            const promptMarkup = this.promptBox
                ? '<div class="me-prompt">' +
                    '<div class="me-prompt-guide">' + escapeHtml(this.promptBox.guide) + '</div>' +
                    '<textarea class="me-prompt-input" rows="3" spellcheck="false" placeholder="' +
                        escapeHtml(this.promptBox.placeholder) + '"></textarea>' +
                  '</div>'
                : '';

            root.innerHTML =
                '<div class="me-toolbar">' +
                    '<div class="me-title">' + escapeHtml(labels.title) + '</div>' +
                    '<div class="me-tool-group">' + toolButtons + '</div>' +
                    '<div class="me-size">' +
                        '<span class="me-size-text">' + escapeHtml(labels.sizeText) + '</span>' +
                        '<input type="range" class="me-size-range" min="' + MIN_BRUSH + '" max="' + MAX_BRUSH +
                            '" step="1" value="' + DEFAULT_BRUSH + '" aria-label="' +
                            escapeHtml(labels.sizeText) + '">' +
                        '<span class="me-size-value">' + DEFAULT_BRUSH + 'px</span>' +
                    '</div>' +
                    '<div class="me-actions">' +
                        '<button type="button" class="me-icon-btn" data-act="undo" title="' +
                            escapeHtml(labels.undo) + ' (Ctrl+Z)">' + ICONS.undo + '</button>' +
                        '<button type="button" class="me-icon-btn" data-act="redo" title="' +
                            escapeHtml(labels.redo) + ' (Ctrl+Shift+Z)">' + ICONS.redo + '</button>' +
                        '<button type="button" class="me-icon-btn" data-act="invert" title="' +
                            escapeHtml(labels.invert) + ' (Ctrl+I)">' + ICONS.invert + '</button>' +
                        '<button type="button" class="me-icon-btn" data-act="clear" title="' +
                            escapeHtml(labels.clear) + ' (X)">' + ICONS.trash + '</button>' +
                        '<span class="me-divider" aria-hidden="true"></span>' +
                        '<button type="button" class="me-text-btn" data-act="cancel" title="Esc">' +
                            escapeHtml(labels.cancel) + '</button>' +
                        '<button type="button" class="me-primary-btn" data-act="save" title="Enter">' +
                            ICONS.check + '<span class="me-save-text">' + escapeHtml(labels.save) + '</span>' +
                        '</button>' +
                    '</div>' +
                '</div>' +
                '<div class="me-main">' +
                    '<div class="me-stage">' +
                        '<div class="me-viewport">' +
                            '<img class="me-image" alt="" draggable="false">' +
                            '<canvas class="me-paint"></canvas>' +
                        '</div>' +
                        '<canvas class="me-marquee-canvas"></canvas>' +
                        '<div class="me-cursor" aria-hidden="true"></div>' +
                        '<aside class="me-panel">' +
                            modeSeg +
                            '<div class="me-swatches">' + swatches + '</div>' +
                            '<div class="me-opacity-row">' +
                                '<div class="me-row-head">' +
                                    '<span>' + escapeHtml(labels.opacity) + '</span>' +
                                    '<span class="me-opacity-value">' + DEFAULT_OPACITY + '%</span>' +
                                '</div>' +
                                '<input type="range" class="me-opacity-range" min="0" max="100" step="1" ' +
                                    'value="' + DEFAULT_OPACITY + '" aria-label="' +
                                    escapeHtml(labels.opacity) + '">' +
                            '</div>' +
                            '<label class="me-check">' +
                                '<input type="checkbox" class="me-check-input" checked>' +
                                '<span class="me-check-track" aria-hidden="true"></span>' +
                                '<span class="me-check-text">' + escapeHtml(labels.fillHoles) + '</span>' +
                            '</label>' +
                            '<div class="me-zoom">' +
                                '<button type="button" class="me-panel-btn" data-act="fit" title="' +
                                    escapeHtml(labels.fit) + '">' + ICONS.fit +
                                    '<span>' + escapeHtml(labels.fit) + '</span></button>' +
                                '<div class="me-zoom-ctl">' +
                                    '<button type="button" class="me-icon-btn" data-act="zoom-out" title="' +
                                        escapeHtml(labels.zoomOut) + '">' + ICONS.minus + '</button>' +
                                    '<span class="me-zoom-value">100%</span>' +
                                    '<button type="button" class="me-icon-btn" data-act="zoom-in" title="' +
                                        escapeHtml(labels.zoomIn) + '">' + ICONS.plus + '</button>' +
                                '</div>' +
                            '</div>' +
                        '</aside>' +
                        promptMarkup +
                        (this.promptBox ? '' : '<div class="me-empty-hint">' + escapeHtml(labels.emptyHint) + '</div>') +
                        '<div class="me-error-box" hidden>' +
                            '<div class="me-error-text"></div>' +
                            '<button type="button" class="me-text-btn me-error-close">' +
                                escapeHtml(labels.cancel) + '</button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="me-footer">' +
                    '<span class="me-footer-hint">' + escapeHtml(labels.hint) + '</span>' +
                    '<span class="me-footer-shortcut">' + escapeHtml(labels.shortcutHint) + '</span>' +
                '</div>' +
                '<div class="me-toast" hidden></div>';

            document.body.appendChild(root);
            this.root = root;

            this.imgEl = root.querySelector('.me-image');
            this.imgEl.src = this.imageUrl;
            this.paint = root.querySelector('.me-paint');
            this.stage = root.querySelector('.me-stage');
            this.viewport = root.querySelector('.me-viewport');
            this.marqueeCanvas = root.querySelector('.me-marquee-canvas');
            this.cursorEl = root.querySelector('.me-cursor');
            this.panel = root.querySelector('.me-panel');
            this.emptyHint = root.querySelector('.me-empty-hint');
            this.errorBox = root.querySelector('.me-error-box');
            this.errorText = root.querySelector('.me-error-text');
            this.toast = root.querySelector('.me-toast');

            this.sizeInput = root.querySelector('.me-size-range');
            this.sizeValue = root.querySelector('.me-size-value');
            this.opacityInput = root.querySelector('.me-opacity-range');
            this.opacityValue = root.querySelector('.me-opacity-value');
            this.fillInput = root.querySelector('.me-check-input');
            this.zoomValue = root.querySelector('.me-zoom-value');
            this.saveBtn = root.querySelector('[data-act="save"]');
            this.saveText = root.querySelector('.me-save-text');
            this.undoBtn = root.querySelector('[data-act="undo"]');
            this.redoBtn = root.querySelector('[data-act="redo"]');
            this.invertBtn = root.querySelector('[data-act="invert"]');
            this.promptInput = root.querySelector('.me-prompt-input');
            if (this.promptInput) this.promptInput.value = this.promptBox && this.promptBox.value ? String(this.promptBox.value) : '';

            COLOR_ORDER.forEach((name) => {
                const button = root.querySelector('[data-color="' + name + '"]');
                if (button) button.style.background = COLORS[name];
            });

            this.updateToolbar();
            this.updatePanel();
        }

        bindEvents() {
            this.onRootClick = (event) => this.handleClick(event);
            this.onRootContextMenu = (event) => event.preventDefault();
            this.onRootWheel = (event) => {
                // 提示词输入框内允许正常滚动文本
                if (event.target && event.target.closest && event.target.closest('.me-prompt')) return;
                event.preventDefault();
            };
            this.onStagePointerDown = (event) => this.handlePointerDown(event);
            this.onStagePointerMove = (event) => this.handlePointerMove(event);
            this.onStagePointerUp = (event) => this.handlePointerUp(event);
            this.onStagePointerCancel = (event) => this.handlePointerCancel(event);
            this.onStagePointerLeave = () => {
                if (!this.drawing && !this.marquee && !this.pan && !this.rightDrag) {
                    this.hover = null;
                    this.updateCursor();
                }
            };
            this.onStageWheel = (event) => this.handleWheel(event);
            this.onKeyDown = (event) => this.handleKeyDown(event);
            this.onKeyUp = (event) => this.handleKeyUp(event);
            this.onWindowBlur = () => {
                this.spaceDown = false;
                this.altDown = false;
                this.shiftDown = false;
                this.updateCursor();
            };
            this.onResize = () => this.handleResize();

            this.root.addEventListener('click', this.onRootClick);
            this.root.addEventListener('contextmenu', this.onRootContextMenu);
            this.root.addEventListener('wheel', this.onRootWheel, { passive: false });
            this.stage.addEventListener('pointerdown', this.onStagePointerDown);
            this.stage.addEventListener('pointermove', this.onStagePointerMove);
            this.stage.addEventListener('pointerup', this.onStagePointerUp);
            this.stage.addEventListener('pointercancel', this.onStagePointerCancel);
            this.stage.addEventListener('pointerleave', this.onStagePointerLeave);
            this.stage.addEventListener('wheel', this.onStageWheel, { passive: false });
            window.addEventListener('keydown', this.onKeyDown, true);
            window.addEventListener('keyup', this.onKeyUp, true);
            window.addEventListener('blur', this.onWindowBlur);
            window.addEventListener('resize', this.onResize);

            this.sizeInput.addEventListener('input', () => {
                this.setBrushSize(Number(this.sizeInput.value));
            });
            this.opacityInput.addEventListener('input', () => {
                this.opacity = clamp(Math.round(Number(this.opacityInput.value)), 0, 100);
                this.updatePanel();
                this.scheduleRender();
            });
            this.fillInput.addEventListener('change', () => {
                this.fillHoles = !!this.fillInput.checked;
                this.markAllDirty();
                this.render();
            });
            this.root.querySelector('.me-error-close').addEventListener('click', () => this.close(false));

            if (typeof ResizeObserver === 'function') {
                this.resizeObserver = new ResizeObserver(() => this.handleResize());
                this.resizeObserver.observe(this.stage);
            }

            // 打开后把焦点收到遮罩层，Esc/Enter 才不会落到页面其它控件
            try {
                this.root.focus({ preventScroll: true });
            } catch (error) {
                this.root.focus();
            }
        }

        destroy() {
            this.closed = true;
            if (this.frameRequest) {
                cancelAnimationFrame(this.frameRequest);
                this.frameRequest = 0;
            }
            if (this.toastTimer) clearTimeout(this.toastTimer);
            if (this.colorMenuTimer) clearTimeout(this.colorMenuTimer);
            this.closeColorMenu();
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
                this.resizeObserver = null;
            }
            window.removeEventListener('keydown', this.onKeyDown, true);
            window.removeEventListener('keyup', this.onKeyUp, true);
            window.removeEventListener('blur', this.onWindowBlur);
            window.removeEventListener('resize', this.onResize);
            if (this.root) {
                this.root.removeEventListener('click', this.onRootClick);
                this.root.removeEventListener('contextmenu', this.onRootContextMenu);
                this.root.removeEventListener('wheel', this.onRootWheel);
            }
            if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
            this.root = null;
            // 释放图层画布引用，便于 GC
            this.layers.clear();
            this.filledLayers.clear();
            this.ops.length = 0;
            this.redoOps.length = 0;
            if (activeSession === this) activeSession = null;
        }

        close(saved) {
            if (this.closed) return;
            this.destroy();
            if (this.resolvePromise) {
                const resolve = this.resolvePromise;
                this.resolvePromise = null;
                resolve(!!saved);
            }
        }

        cancel() {
            if (this.busy) return;
            this.close(false);
        }

        // ---- 载入 ----
        async loadSource() {
            this.imageEl = this.imgEl;
            await waitForImage(this.imageEl);
            const naturalWidth = this.imageEl.naturalWidth || this.imageEl.width;
            const naturalHeight = this.imageEl.naturalHeight || this.imageEl.height;
            if (!naturalWidth || !naturalHeight) throw new Error('image has no size');
            this.naturalWidth = naturalWidth;
            this.naturalHeight = naturalHeight;
            const longSide = Math.max(naturalWidth, naturalHeight);
            const factor = longSide > MAX_EDIT_LONG_SIDE ? MAX_EDIT_LONG_SIDE / longSide : 1;
            this.editWidth = Math.max(1, Math.round(naturalWidth * factor));
            this.editHeight = Math.max(1, Math.round(naturalHeight * factor));

            this.paint.width = this.editWidth;
            this.paint.height = this.editHeight;
            this.viewport.style.width = this.editWidth + 'px';
            this.viewport.style.height = this.editHeight + 'px';

            this.compositeCanvas = createCanvas(this.editWidth, this.editHeight);
            this.compositeCtx = this.compositeCanvas.getContext('2d');
            this.paintCtx = this.paint.getContext('2d');
            this.paint.style.opacity = '0.5';
        }

        async loadExistingMask() {
            if (!this.maskUrl) return;
            try {
                const image = new Image();
                image.src = this.maskUrl;
                await waitForImage(image);
                const temp = createCanvas(this.editWidth, this.editHeight);
                const tempCtx = temp.getContext('2d', { willReadFrequently: true });
                tempCtx.imageSmoothingEnabled = false;
                tempCtx.drawImage(image, 0, 0, this.editWidth, this.editHeight);
                const source = tempCtx.getImageData(0, 0, this.editWidth, this.editHeight);
                // 颜色叠加保存的纯色块小图：用它精确还原"每个像素是哪种颜色"，
                // 不用去猜已经和原图混在一起的彩色效果图。
                const paintData = await this.loadPaintData();
                const palettes = COLOR_ORDER.map((name) => ({ name: name, rgb: hexToRgb(COLORS[name]) }));
                const layers = new Map();
                const layerForBaseline = (name) => {
                    let entry = layers.get(name);
                    if (!entry) {
                        const canvas = createCanvas(this.editWidth, this.editHeight);
                        const ctx = canvas.getContext('2d');
                        entry = { canvas: canvas, ctx: ctx, image: ctx.createImageData(this.editWidth, this.editHeight) };
                        layers.set(name, entry);
                    }
                    return entry;
                };
                let painted = false;
                for (let offset = 0; offset < source.data.length; offset += 4) {
                    const alpha = source.data[offset + 3];
                    if (alpha <= 8) continue;
                    // 旧格式是不透明黑白 PNG：亮度 > 128 视为已涂抹
                    const luma = source.data[offset] * 0.299 +
                        source.data[offset + 1] * 0.587 +
                        source.data[offset + 2] * 0.114;
                    if (luma <= 128) continue;
                    let name = this.colorName;
                    if (paintData && paintData[offset + 3] > 8) {
                        const red = paintData[offset];
                        const green = paintData[offset + 1];
                        const blue = paintData[offset + 2];
                        let best = null;
                        let bestDistance = Infinity;
                        palettes.forEach((candidate) => {
                            const dr = red - candidate.rgb[0];
                            const dg = green - candidate.rgb[1];
                            const db = blue - candidate.rgb[2];
                            const distance = dr * dr + dg * dg + db * db;
                            if (distance < bestDistance) {
                                bestDistance = distance;
                                best = candidate;
                            }
                        });
                        if (best) name = best.name;
                    }
                    const rgb = hexToRgb(COLORS[name]);
                    const entry = layerForBaseline(name);
                    entry.image.data[offset] = rgb[0];
                    entry.image.data[offset + 1] = rgb[1];
                    entry.image.data[offset + 2] = rgb[2];
                    entry.image.data[offset + 3] = 255;
                    painted = true;
                }
                if (!painted) return;
                // 基线按颜色分成若干个 op 放回操作列表：撤销到底会回到旧遮罩，而不是空画布
                layers.forEach((entry, name) => {
                    entry.ctx.putImageData(entry.image, 0, 0);
                    const op = { kind: 'baseline', color: name, canvas: entry.canvas };
                    this.ops.push(op);
                    this.applyOp(op);
                });
            } catch (error) {
                if (window.console && console.warn) console.warn('MaskEditor: 旧遮罩载入失败', error);
            }
        }

        // 读取保存时留下的纯色块小图；没有或者读不到就当没有（退回单色遮罩）。
        async loadPaintData() {
            if (!this.paintUrl) return null;
            try {
                const image = new Image();
                image.src = this.paintUrl;
                await waitForImage(image);
                const temp = createCanvas(this.editWidth, this.editHeight);
                const ctx = temp.getContext('2d', { willReadFrequently: true });
                ctx.imageSmoothingEnabled = false;
                ctx.drawImage(image, 0, 0, this.editWidth, this.editHeight);
                return ctx.getImageData(0, 0, this.editWidth, this.editHeight).data;
            } catch (error) {
                return null;
            }
        }

        // ---- 图层 ----
        layerFor(name, create) {
            let entry = this.layers.get(name);
            if (!entry && create) {
                const canvas = createCanvas(this.editWidth, this.editHeight);
                entry = { canvas: canvas, ctx: canvas.getContext('2d', { willReadFrequently: true }) };
                entry.ctx.imageSmoothingEnabled = false;
                this.layers.set(name, entry);
            }
            return entry || null;
        }

        filledLayerFor(name, create) {
            let canvas = this.filledLayers.get(name);
            if (!canvas && create) {
                const source = this.layers.get(name);
                if (!source) return null;
                const rgb = this.mode === 'color' ? hexToRgb(COLORS[name]) : hexToRgb(MASK_TINT);
                canvas = fillHolesInLayer(source.canvas, rgb);
                if (canvas) this.filledLayers.set(name, canvas);
                else this.filledLayers.delete(name);
            }
            return canvas || null;
        }

        ensureFilled() {
            if (!this.fillHoles) return;
            if (this.fillReady) return;
            this.dirtyFill.forEach((name) => {
                if (!this.layers.has(name)) {
                    this.filledLayers.delete(name);
                    return;
                }
                const rgb = this.mode === 'color' ? hexToRgb(COLORS[name]) : hexToRgb(MASK_TINT);
                const source = this.layers.get(name);
                const filled = fillHolesInLayer(source.canvas, rgb);
                if (filled) this.filledLayers.set(name, filled);
                else this.filledLayers.delete(name);
            });
            this.dirtyFill.clear();
            this.fillReady = true;
        }

        markDirty(names) {
            if (!names || !names.length) return;
            names.forEach((name) => this.dirtyFill.add(name));
            this.fillReady = false;
        }

        markAllDirty() {
            COLOR_ORDER.forEach((name) => this.dirtyFill.add(name));
            this.fillReady = false;
        }

        clearAllLayers() {
            this.layers.forEach((entry) => {
                entry.ctx.clearRect(0, 0, this.editWidth, this.editHeight);
            });
            this.filledLayers.clear();
            this.dirtyFill.clear();
            this.fillReady = false;
        }

        layerNames() {
            return Array.from(this.layers.keys());
        }

        // ---- 渲染 ----
        scheduleRender() {
            if (this.frameRequest) return;
            this.frameRequest = requestAnimationFrame(() => {
                this.frameRequest = 0;
                if (!this.closed) this.render();
            });
        }

        render() {
            if (!this.editWidth || !this.paintCtx) return;
            if (this.frameRequest) {
                cancelAnimationFrame(this.frameRequest);
                this.frameRequest = 0;
            }
            this.ensureFilled();
            const width = this.editWidth;
            const height = this.editHeight;
            const work = this.compositeCtx;
            work.setTransform(1, 0, 0, 1, 0, 0);
            work.globalAlpha = 1;
            work.globalCompositeOperation = 'source-over';
            work.clearRect(0, 0, width, height);

            const useFilled = this.fillHoles;
            let any = false;
            COLOR_ORDER.forEach((name) => {
                let layerCanvas = null;
                if (useFilled) {
                    this.ensureFilled();
                    layerCanvas = this.filledLayers.get(name) || null;
                } else {
                    const entry = this.layers.get(name);
                    layerCanvas = entry ? entry.canvas : null;
                }
                if (!layerCanvas) return;
                work.drawImage(layerCanvas, 0, 0);
                any = true;
            });

            // 仅遮罩模式：所有颜色图层统一压成单色显示
            if (this.mode !== 'color' && any) {
                work.globalCompositeOperation = 'source-in';
                work.fillStyle = MASK_TINT;
                work.fillRect(0, 0, width, height);
                work.globalCompositeOperation = 'source-over';
            }

            const ctx = this.paintCtx;
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.globalAlpha = 1;
            ctx.globalCompositeOperation = 'source-over';
            ctx.clearRect(0, 0, width, height);
            if (this.inverted) {
                // 反选只是显示/导出视图：整幅涂上当前色，再把已画区域挖掉
                ctx.fillStyle = this.mode === 'color' ? COLORS[this.colorName] : MASK_TINT;
                ctx.fillRect(0, 0, width, height);
                ctx.globalCompositeOperation = 'destination-out';
                ctx.drawImage(this.compositeCanvas, 0, 0);
                ctx.globalCompositeOperation = 'source-over';
            } else {
                ctx.drawImage(this.compositeCanvas, 0, 0);
            }

            this.paint.style.opacity = this.mode === 'color' ? String(this.opacity / 100) : '0.5';
            this.updateEmptyHint();
            this.updateHistory();
            this.updateZoomLabel();
        }

        updateEmptyHint() {
            if (!this.emptyHint) return;
            this.emptyHint.hidden = this.hasContent();
        }

        hasContent() {
            let content = false;
            for (let index = 0; index < this.ops.length; index++) {
                const op = this.ops[index];
                if (op.kind === 'clear') content = false;
                else if (op.kind === 'stroke') content = op.points.length > 0;
                else content = true;
            }
            return content;
        }

        // ---- 操作列表 ----
        baselineCount() {
            // 颜色叠加的旧结果会按颜色拆成多个基线 op，全部都在撤销保护范围内
            let count = 0;
            while (count < this.ops.length && this.ops[count].kind === 'baseline') count += 1;
            return count;
        }

        pushOp(op) {
            if (!op) return;
            this.ops.push(op);
            const keep = this.baselineCount();
            while (this.ops.length > MAX_OPS) this.ops.splice(keep, 1);
            if (this.redoOps.length) this.redoOps.length = 0;
            this.updateHistory();
        }

        applyOp(op) {
            switch (op.kind) {
                case 'baseline':
                    this.applyBaselineOp(op);
                    break;
                case 'stroke':
                    this.applyStrokeOp(op);
                    break;
                case 'rect':
                    this.applyRectOp(op);
                    break;
                case 'clear':
                    this.clearAllLayers();
                    break;
                case 'recolor':
                    this.applyRecolorOp(op);
                    break;
                default:
                    break;
            }
        }

        applyBaselineOp(op) {
            const entry = this.layerFor(op.color, true);
            entry.ctx.save();
            entry.ctx.globalCompositeOperation = 'source-over';
            entry.ctx.imageSmoothingEnabled = false;
            entry.ctx.drawImage(op.canvas, 0, 0);
            entry.ctx.restore();
        }

        applyStrokeOp(op) {
            if (!op.points || !op.points.length) return;
            const erase = !!op.erase;
            const color = COLORS[op.color] || COLORS.red;
            const size = clamp(Number(op.size) || DEFAULT_BRUSH, MIN_BRUSH, MAX_BRUSH);
            const mask = this.buildStrokeMask(op.points, size);
            if (!mask) return;
            // 只把笔迹包围盒搬回图层，避免整幅 1536² 反复合成（回放几百笔时差别明显）
            const box = this.strokeBox(op.points, size);
            if (!box) return;
            if (erase) {
                // 橡皮：仅遮罩模式擦所有层（视觉上是一张单色遮罩）；颜色模式只擦当前色层
                const names = op.scope === 'all' ? this.layerNames() : [op.color];
                names.forEach((name) => {
                    const entry = this.layerFor(name, false);
                    if (entry) this.eraseWithMask(entry, mask, box);
                });
                return;
            }
            // 画笔：先把同位置的其它颜色层挖掉，保证"同一像素只属一层"
            Array.from(this.layers.keys()).forEach((name) => {
                if (name === op.color) return;
                this.eraseWithMask(this.layers.get(name), mask, box);
            });
            const entry = this.layerFor(op.color, true);
            const ctx = entry.ctx;
            ctx.save();
            ctx.globalCompositeOperation = 'source-over';
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.lineWidth = size;
            ctx.strokeStyle = color;
            ctx.fillStyle = color;
            const points = op.points;
            if (points.length === 1) {
                ctx.beginPath();
                ctx.arc(points[0].x, points[0].y, size / 2, 0, Math.PI * 2);
                ctx.fill();
            } else {
                ctx.beginPath();
                ctx.moveTo(points[0].x, points[0].y);
                for (let index = 1; index < points.length; index++) {
                    ctx.lineTo(points[index].x, points[index].y);
                }
                ctx.stroke();
            }
            ctx.restore();
        }

        buildStrokeMask(points, size) {
            if (!points || !points.length) return null;
            const mask = createCanvas(this.editWidth, this.editHeight);
            const ctx = mask.getContext('2d');
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.lineWidth = size;
            ctx.strokeStyle = '#fff';
            ctx.fillStyle = '#fff';
            if (points.length === 1) {
                ctx.beginPath();
                ctx.arc(points[0].x, points[0].y, size / 2, 0, Math.PI * 2);
                ctx.fill();
            } else {
                ctx.beginPath();
                ctx.moveTo(points[0].x, points[0].y);
                for (let index = 1; index < points.length; index++) {
                    ctx.lineTo(points[index].x, points[index].y);
                }
                ctx.stroke();
            }
            return mask;
        }

        eraseWithMask(entry, mask, box) {
            if (!entry || !box) return;
            entry.ctx.save();
            entry.ctx.globalCompositeOperation = 'destination-out';
            entry.ctx.imageSmoothingEnabled = false;
            entry.ctx.drawImage(mask, box.x, box.y, box.w, box.h, box.x, box.y, box.w, box.h);
            entry.ctx.restore();
        }

        /** 整条笔迹的包围盒（含笔刷半径），用于按区域回放 */
        strokeBox(points, size) {
            const padding = Math.ceil(size / 2) + 2;
            let minX = Infinity;
            let minY = Infinity;
            let maxX = -Infinity;
            let maxY = -Infinity;
            for (let index = 0; index < points.length; index++) {
                const point = points[index];
                if (point.x < minX) minX = point.x;
                if (point.x > maxX) maxX = point.x;
                if (point.y < minY) minY = point.y;
                if (point.y > maxY) maxY = point.y;
            }
            if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
            const x0 = clamp(Math.floor(minX - padding), 0, this.editWidth);
            const y0 = clamp(Math.floor(minY - padding), 0, this.editHeight);
            const x1 = clamp(Math.ceil(maxX + padding), 0, this.editWidth);
            const y1 = clamp(Math.ceil(maxY + padding), 0, this.editHeight);
            const width = x1 - x0;
            const height = y1 - y0;
            if (width <= 0 || height <= 0) return null;
            return { x: x0, y: y0, w: width, h: height };
        }

        applyRectOp(op) {
            const x = clamp(Math.round(op.x), 0, this.editWidth);
            const y = clamp(Math.round(op.y), 0, this.editHeight);
            const width = clamp(Math.round(op.w), 0, this.editWidth - x);
            const height = clamp(Math.round(op.h), 0, this.editHeight - y);
            if (!width || !height) return;
            if (op.erase) {
                const names = op.scope === 'all' ? this.layerNames() : [op.color];
                names.forEach((name) => {
                    const entry = this.layerFor(name, false);
                    if (!entry) return;
                    entry.ctx.save();
                    entry.ctx.globalCompositeOperation = 'destination-out';
                    entry.ctx.fillStyle = '#000';
                    entry.ctx.fillRect(x, y, width, height);
                    entry.ctx.restore();
                });
                return;
            }
            Array.from(this.layers.keys()).forEach((name) => {
                if (name === op.color) return;
                const entry = this.layers.get(name);
                entry.ctx.save();
                entry.ctx.globalCompositeOperation = 'destination-out';
                entry.ctx.fillStyle = '#000';
                entry.ctx.fillRect(x, y, width, height);
                entry.ctx.restore();
            });
            const target = this.layerFor(op.color, true);
            target.ctx.save();
            target.ctx.globalCompositeOperation = 'source-over';
            target.ctx.fillStyle = COLORS[op.color] || COLORS.red;
            target.ctx.fillRect(x, y, width, height);
            target.ctx.restore();
        }

        applyRecolorOp(op) {
            if (!op || !op.seed || op.to === op.from) return;
            const source = this.layerFor(op.from, false);
            if (!source) return;
            const visited = collectPaintedComponent(source.canvas, op.seed.x, op.seed.y);
            if (!visited) return;
            const total = this.editWidth * this.editHeight;
            const sourceImage = source.ctx.getImageData(0, 0, this.editWidth, this.editHeight);
            const sourceData = sourceImage.data;
            const target = op.to ? this.layerFor(op.to, true) : null;
            const targetImage = target ? target.ctx.getImageData(0, 0, this.editWidth, this.editHeight) : null;
            const targetData = targetImage ? targetImage.data : null;
            const rgb = op.to ? hexToRgb(COLORS[op.to]) : null;
            let moved = false;
            for (let index = 0; index < total; index++) {
                if (!visited[index]) continue;
                const offset = index * 4;
                if (sourceData[offset + 3] <= PAINT_ALPHA) continue;
                moved = true;
                if (targetData && rgb) {
                    targetData[offset] = rgb[0];
                    targetData[offset + 1] = rgb[1];
                    targetData[offset + 2] = rgb[2];
                    targetData[offset + 3] = sourceData[offset + 3];
                }
                sourceData[offset] = 0;
                sourceData[offset + 1] = 0;
                sourceData[offset + 2] = 0;
                sourceData[offset + 3] = 0;
            }
            if (!moved) return;
            source.ctx.putImageData(sourceImage, 0, 0);
            if (target && targetImage) target.ctx.putImageData(targetImage, 0, 0);
        }

        affectedColors(op) {
            const names = new Set();
            if (op.kind === 'stroke' || op.kind === 'rect') {
                if (op.erase) {
                    if (op.scope === 'all') this.layerNames().forEach((name) => names.add(name));
                    else names.add(op.color);
                } else {
                    this.layerNames().forEach((name) => names.add(name));
                    names.add(op.color);
                }
            } else if (op.kind === 'recolor') {
                names.add(op.from);
                if (op.to) names.add(op.to);
            } else if (op.kind === 'baseline') {
                names.add(op.color);
            } else if (op.kind === 'clear') {
                COLOR_ORDER.forEach((name) => names.add(name));
            }
            return Array.from(names);
        }

        undo() {
            if (this.busy) return;
            const keep = this.baselineCount();
            if (this.ops.length <= keep) return;
            this.redoOps.push(this.ops.pop());
            this.replay();
        }

        redo() {
            if (this.busy) return;
            const op = this.redoOps.pop();
            if (!op) return;
            this.ops.push(op);
            this.applyOp(op);
            this.markDirty(this.affectedColors(op));
            this.render();
            this.updateHistory();
        }

        replay() {
            this.clearAllLayers();
            this.ops.forEach((op) => this.applyOp(op));
            this.markAllDirty();
            this.render();
            this.updateHistory();
        }

        clear() {
            if (this.busy) return;
            this.pushOp({ kind: 'clear' });
            this.clearAllLayers();
            this.markAllDirty();
            this.render();
        }

        invert() {
            if (this.busy) return;
            this.inverted = !this.inverted;
            this.render();
            this.updateToolbar();
        }

        // ---- 工具 ----
        setTool(tool) {
            if (['brush', 'eraser', 'marquee'].indexOf(tool) < 0) return;
            if (this.tool === tool) return;
            if (this.marquee) {
                this.marquee = null;
                this.clearMarqueePreview();
                this.releasePointer();
            }
            this.tool = tool;
            this.updateToolbar();
            this.updateCursor();
        }

        setBrushSize(value) {
            const next = clamp(Math.round(Number(value) || DEFAULT_BRUSH), MIN_BRUSH, MAX_BRUSH);
            this.brushSize = next;
            if (this.sizeInput) this.sizeInput.value = String(next);
            if (this.sizeValue) this.sizeValue.textContent = next + 'px';
            this.updateCursor();
        }

        setColor(name) {
            if (!COLORS[name]) return;
            this.colorName = name;
            this.updatePanel();
            this.render();
            this.updateCursor();
        }

        setMode(mode) {
            const next = mode === 'color' ? 'color' : 'mask';
            if (this.mode === next) return;
            // 已画内容全部保留在颜色图层里：彩色层原样保留，单色笔迹本来就落在当前选中色层
            this.mode = next;
            this.markAllDirty();
            this.render();
            this.updatePanel();
        }

        setBusy(busy) {
            this.busy = !!busy;
            if (!this.root) return;
            this.root.classList.toggle('is-busy', this.busy);
            if (this.saveBtn) this.saveBtn.disabled = this.busy;
            if (this.saveText) {
                this.saveText.textContent = this.busy ? this.label('saving') : this.label('save');
            }
        }

        // ---- UI 同步 ----
        updateToolbar() {
            if (!this.root) return;
            this.root.dataset.tool = this.tool;
            this.root.querySelectorAll('.me-tool').forEach((button) => {
                button.classList.toggle('is-active', button.dataset.tool === this.tool);
            });
            if (this.sizeInput) this.sizeInput.value = String(this.brushSize);
            if (this.sizeValue) this.sizeValue.textContent = this.brushSize + 'px';
            if (this.invertBtn) {
                this.invertBtn.classList.toggle('is-active', this.inverted);
                this.invertBtn.setAttribute('aria-pressed', this.inverted ? 'true' : 'false');
            }
            this.updateHistory();
        }

        updatePanel() {
            if (!this.root) return;
            this.root.dataset.mode = this.mode;
            this.root.querySelectorAll('.me-seg-btn').forEach((button) => {
                button.classList.toggle('is-active', button.dataset.mode === this.mode);
            });
            this.root.querySelectorAll('.me-swatch').forEach((button) => {
                button.classList.toggle('is-active', button.dataset.color === this.colorName);
            });
            if (this.opacityInput) this.opacityInput.value = String(this.opacity);
            if (this.opacityValue) this.opacityValue.textContent = this.opacity + '%';
            if (this.fillInput) this.fillInput.checked = this.fillHoles;
        }

        updateHistory() {
            if (!this.undoBtn || !this.redoBtn) return;
            this.undoBtn.disabled = this.ops.length <= this.baselineCount();
            this.redoBtn.disabled = this.redoOps.length === 0;
        }

        updateZoomLabel() {
            if (this.zoomValue) this.zoomValue.textContent = Math.round(this.scale * 100) + '%';
        }

        showToast(text) {
            if (!this.toast) return;
            this.toast.textContent = text || '';
            this.toast.hidden = false;
            if (this.toastTimer) clearTimeout(this.toastTimer);
            this.toastTimer = setTimeout(() => {
                if (this.toast) this.toast.hidden = true;
            }, 4200);
        }

        showError(text) {
            if (!this.errorBox) return;
            if (this.errorText) this.errorText.textContent = text || this.label('loadFail');
            this.errorBox.hidden = false;
            if (this.root) this.root.classList.add('is-error');
        }

        handleClick(event) {
            const target = event.target;
            if (!target || !target.closest) return;
            // 注意：root 自身也带 data-tool / data-mode（供 CSS 用），
            // 因此这里必须用"具体控件"的类选择器，不能直接 closest('[data-tool]')，
            // 否则任何点击都会先命中 root 而被当成工具切换。
            const toolButton = target.closest('.me-tool');
            if (toolButton) {
                this.setTool(toolButton.dataset.tool);
                return;
            }
            const modeButton = target.closest('.me-seg-btn');
            if (modeButton) {
                this.setMode(modeButton.dataset.mode);
                return;
            }
            const colorButton = target.closest('.me-swatch');
            if (colorButton) {
                this.setColor(colorButton.dataset.color);
                return;
            }
            const actionButton = target.closest('button[data-act]');
            if (!actionButton) return;
            const action = actionButton.dataset.act;
            if (action === 'undo') this.undo();
            else if (action === 'redo') this.redo();
            else if (action === 'invert') this.invert();
            else if (action === 'clear') this.clear();
            else if (action === 'cancel') this.cancel();
            else if (action === 'save') this.save();
            else if (action === 'fit') this.fitToStage();
            else if (action === 'zoom-in') this.zoomBy(1.25);
            else if (action === 'zoom-out') this.zoomBy(0.8);
        }

        // ---- 视图变换 ----
        applyTransform() {
            if (!this.viewport) return;
            this.viewport.style.transform =
                'translate(' + this.offset.x + 'px, ' + this.offset.y + 'px) scale(' + this.scale + ')';
            this.updateZoomLabel();
            this.drawMarqueePreview();
            this.updateCursor();
        }

        fitToStage() {
            if (!this.editWidth) return;
            const rect = this.stage.getBoundingClientRect();
            if (!rect.width || !rect.height) return;
            const padding = 48;
            const scale = clamp(
                Math.min((rect.width - padding * 2) / this.editWidth,
                    (rect.height - padding * 2) / this.editHeight),
                MIN_SCALE,
                MAX_FIT_SCALE
            );
            this.scale = scale;
            this.offset = {
                x: (rect.width - this.editWidth * scale) / 2,
                y: (rect.height - this.editHeight * scale) / 2
            };
            this.userAdjusted = false;
            this.applyTransform();
        }

        zoomBy(factor) {
            const rect = this.stage.getBoundingClientRect();
            this.zoomAt(rect.width / 2, rect.height / 2, factor);
        }

        zoomAt(stageX, stageY, factor) {
            const next = clamp(this.scale * factor, MIN_SCALE, MAX_SCALE);
            if (next === this.scale) return;
            const imageX = (stageX - this.offset.x) / this.scale;
            const imageY = (stageY - this.offset.y) / this.scale;
            this.scale = next;
            this.offset = {
                x: stageX - imageX * next,
                y: stageY - imageY * next
            };
            this.userAdjusted = true;
            this.applyTransform();
        }

        handleWheel(event) {
            if (this.busy) return;
            // 提示词输入框区域交还给浏览器处理滚动
            if (event.target && event.target.closest && event.target.closest('.me-prompt')) return;
            event.preventDefault();
            event.stopPropagation();
            // Ctrl（macOS 上也可用 Cmd）+ 滚轮快速调画笔大小，普通滚轮保持缩放。
            if (event.ctrlKey || event.metaKey) {
                event.stopImmediatePropagation();
                this.adjustBrushSize(event.deltaY < 0 ? 1 : -1);
                return;
            }
            const rect = this.stage.getBoundingClientRect();
            const direction = event.deltaY < 0 ? 1.12 : 0.89;
            this.zoomAt(event.clientX - rect.left, event.clientY - rect.top, direction);
        }

        adjustBrushSize(direction) {
            const step = Math.max(1, Math.round(this.brushSize * 0.12));
            this.setBrushSize(this.brushSize + (direction > 0 ? step : -step));
        }

        handleResize() {
            if (this.closed || !this.stage) return;
            this.drawMarqueePreview();
            if (!this.userAdjusted) this.fitToStage();
            else this.updateCursor();
        }

        imagePos(event) {
            const rect = this.paint.getBoundingClientRect();
            return {
                x: (event.clientX - rect.left) * this.editWidth / Math.max(1, rect.width),
                y: (event.clientY - rect.top) * this.editHeight / Math.max(1, rect.height)
            };
        }

        // ---- 指针 ----
        releasePointer() {
            this.activePointerId = null;
        }

        capturePointer(event) {
            try {
                this.stage.setPointerCapture(event.pointerId);
            } catch (error) {
                // 某些浏览器在指针已释放时会抛错，忽略
            }
        }

        handlePointerDown(event) {
            if (this.busy || this.closed || !this.editWidth) return;
            if (this.activePointerId !== null) return;
            const target = event.target;
            if (target && target.closest && target.closest('.me-panel, .me-error-box, .me-prompt')) return;
            this.closeColorMenu();
            this.hover = { x: event.clientX, y: event.clientY };
            this.altDown = !!event.altKey;
            this.shiftDown = !!event.shiftKey;

            if (event.button === 1 || this.spaceDown) {
                event.preventDefault();
                this.pan = {
                    x: event.clientX,
                    y: event.clientY,
                    offsetX: this.offset.x,
                    offsetY: this.offset.y
                };
                this.activePointerId = event.pointerId;
                this.capturePointer(event);
                this.stage.classList.add('is-panning');
                this.updateCursor();
                return;
            }

            if (event.button === 2) {
                event.preventDefault();
                const pos = this.imagePos(event);
                const hit = this.mode === 'color' && this.tool !== 'marquee'
                    ? this.hitColorPixel(pos) : null;
                this.rightDrag = {
                    startX: event.clientX,
                    startSize: this.brushSize,
                    moved: false,
                    hit: hit
                };
                this.activePointerId = event.pointerId;
                this.capturePointer(event);
                return;
            }

            if (event.button !== 0) return;
            const pos = this.imagePos(event);
            this.activePointerId = event.pointerId;
            this.capturePointer(event);
            if (this.tool === 'marquee') {
                this.beginMarquee(event, pos);
                return;
            }
            this.beginStroke(event, pos);
        }

        handlePointerMove(event) {
            if (this.closed) return;
            if (this.activePointerId !== null && event.pointerId !== this.activePointerId) return;
            this.hover = { x: event.clientX, y: event.clientY };

            if (this.rightDrag) {
                event.preventDefault();
                const dx = event.clientX - this.rightDrag.startX;
                if (!this.rightDrag.moved && Math.abs(dx) >= 5) this.rightDrag.moved = true;
                if (this.rightDrag.moved) {
                    this.setBrushSize(this.rightDrag.startSize + Math.round(dx / 2));
                }
                return;
            }

            if (this.pan) {
                this.offset = {
                    x: this.pan.offsetX + event.clientX - this.pan.x,
                    y: this.pan.offsetY + event.clientY - this.pan.y
                };
                this.userAdjusted = true;
                this.applyTransform();
                return;
            }

            if (this.marquee) {
                const pos = this.imagePos(event);
                this.marquee.currentX = pos.x;
                this.marquee.currentY = pos.y;
                this.drawMarqueePreview();
                this.updateCursor();
                return;
            }

            if (this.drawing) {
                const raw = this.collectPoints(event);
                this.appendStrokePoints(this.drawing, raw);
                this.updateCursor();
                return;
            }

            this.updateCursor();
        }

        handlePointerUp(event) {
            if (this.closed) return;
            if (this.activePointerId !== null && event.pointerId !== this.activePointerId) return;

            if (this.rightDrag) {
                const gesture = this.rightDrag;
                this.rightDrag = null;
                this.releasePointer();
                this.updateCursor();
                if (!gesture.moved && gesture.hit) {
                    this.openColorMenu(event.clientX, event.clientY, gesture.hit);
                }
                return;
            }

            if (this.pan) {
                this.pan = null;
                this.releasePointer();
                this.stage.classList.remove('is-panning');
                this.updateCursor();
                return;
            }

            if (this.marquee) {
                this.finishMarquee();
                this.releasePointer();
                this.updateCursor();
                return;
            }

            if (this.drawing) {
                this.finishStroke();
                this.releasePointer();
                this.updateCursor();
            }
        }

        handlePointerCancel(event) {
            if (this.closed) return;
            if (this.activePointerId !== null && event.pointerId !== this.activePointerId) return;
            if (this.rightDrag) {
                this.rightDrag = null;
                this.releasePointer();
                return;
            }
            if (this.pan) {
                this.pan = null;
                this.releasePointer();
                this.stage.classList.remove('is-panning');
                return;
            }
            if (this.marquee) {
                this.marquee = null;
                this.clearMarqueePreview();
                this.releasePointer();
                return;
            }
            if (this.drawing) {
                // 已经画到图层上的笔迹照常提交，避免"画了一笔又消失"
                this.finishStroke();
                this.releasePointer();
            }
        }

        updateCursor() {
            if (!this.stage || !this.cursorEl) return;
            const point = this.hover;
            if (!point) {
                this.cursorEl.style.display = 'none';
                this.stage.style.cursor = '';
                return;
            }
            if (this.pan) {
                this.cursorEl.style.display = 'none';
                this.stage.style.cursor = 'grabbing';
                return;
            }
            if (this.spaceDown) {
                this.cursorEl.style.display = 'none';
                this.stage.style.cursor = 'grab';
                return;
            }
            if (this.tool === 'marquee') {
                this.cursorEl.style.display = 'none';
                this.stage.style.cursor = 'crosshair';
                return;
            }
            const rect = this.stage.getBoundingClientRect();
            const size = Math.max(6, this.brushSize * this.scale);
            this.stage.style.cursor = 'none';
            this.cursorEl.style.display = 'block';
            this.cursorEl.style.width = size + 'px';
            this.cursorEl.style.height = size + 'px';
            this.cursorEl.style.left = (point.x - rect.left - size / 2) + 'px';
            this.cursorEl.style.top = (point.y - rect.top - size / 2) + 'px';
            const erasing = this.tool === 'eraser' || this.altDown;
            this.cursorEl.classList.toggle('is-erase', erasing);
        }

        // ---- 笔刷 ----
        collectPoints(event) {
            const points = [];
            const target = typeof event.getCoalescedEvents === 'function'
                ? event.getCoalescedEvents()
                : null;
            if (target && target.length) {
                for (let index = 0; index < target.length; index++) {
                    points.push(this.imagePos(target[index]));
                }
            } else {
                points.push(this.imagePos(event));
            }
            return points;
        }

        beginStroke(event, pos) {
            const erase = this.tool === 'eraser' || !!event.altKey;
            const op = {
                kind: 'stroke',
                erase: erase,
                scope: this.mode === 'mask' ? 'all' : 'color',
                size: this.brushSize,
                color: this.colorName,
                points: [{ x: pos.x, y: pos.y }]
            };
            const maskCanvas = createCanvas(this.editWidth, this.editHeight);
            this.drawing = {
                op: op,
                last: { x: pos.x, y: pos.y },
                maskCanvas: maskCanvas,
                maskCtx: maskCanvas.getContext('2d')
            };
            this.drawing.maskCtx.lineCap = 'round';
            this.drawing.maskCtx.lineJoin = 'round';
            this.drawing.maskCtx.strokeStyle = '#fff';
            this.drawing.maskCtx.fillStyle = '#fff';
            this.applyStrokeSegment(this.drawing, pos, pos);
        }

        appendStrokePoints(state, points) {
            // 采样阈值按屏幕像素折算，放大到 1200% 时也能保持细腻
            const minDistance = Math.max(0.35, 1.4 / Math.max(this.scale, 0.0001));
            for (let index = 0; index < points.length; index++) {
                const point = points[index];
                const last = state.last;
                const dx = point.x - last.x;
                const dy = point.y - last.y;
                if (Math.hypot(dx, dy) < minDistance) continue;
                state.op.points.push({ x: point.x, y: point.y });
                this.applyStrokeSegment(state, last, point);
                state.last = { x: point.x, y: point.y };
            }
        }

        applyStrokeSegment(state, from, to) {
            const op = state.op;
            const size = op.size;
            const box = this.segmentBox(from, to, size);
            if (!box) return;

            // 1) 累积到本次笔迹的蒙版（用于挖掉其它颜色层）
            const maskCtx = state.maskCtx;
            maskCtx.lineWidth = size;
            maskCtx.beginPath();
            if (from.x === to.x && from.y === to.y) {
                maskCtx.arc(to.x, to.y, size / 2, 0, Math.PI * 2);
                maskCtx.fill();
            } else {
                maskCtx.moveTo(from.x, from.y);
                maskCtx.lineTo(to.x, to.y);
                maskCtx.stroke();
            }

            // 2) 应用到图层
            if (op.erase) {
                const names = op.scope === 'all' ? this.layerNames() : [op.color];
                names.forEach((name) => {
                    const entry = this.layerFor(name, false);
                    if (entry) this.eraseRegion(entry, state.maskCanvas, box);
                });
            } else {
                Array.from(this.layers.keys()).forEach((name) => {
                    if (name === op.color) return;
                    this.eraseRegion(this.layers.get(name), state.maskCanvas, box);
                });
                const entry = this.layerFor(op.color, true);
                const ctx = entry.ctx;
                ctx.save();
                ctx.globalCompositeOperation = 'source-over';
                ctx.lineCap = 'round';
                ctx.lineJoin = 'round';
                ctx.strokeStyle = COLORS[op.color] || COLORS.red;
                ctx.fillStyle = COLORS[op.color] || COLORS.red;
                ctx.lineWidth = size;
                ctx.beginPath();
                if (from.x === to.x && from.y === to.y) {
                    ctx.arc(to.x, to.y, size / 2, 0, Math.PI * 2);
                    ctx.fill();
                } else {
                    ctx.moveTo(from.x, from.y);
                    ctx.lineTo(to.x, to.y);
                    ctx.stroke();
                }
                ctx.restore();
            }

            // 3) 实时画笔迹（不重算填充，落笔后统一重绘修正）
            this.paintLiveSegment(from, to, size, op.erase, op.color);
        }

        segmentBox(from, to, size) {
            const padding = Math.ceil(size / 2) + 2;
            const x0 = clamp(Math.floor(Math.min(from.x, to.x) - padding), 0, this.editWidth);
            const y0 = clamp(Math.floor(Math.min(from.y, to.y) - padding), 0, this.editHeight);
            const x1 = clamp(Math.ceil(Math.max(from.x, to.x) + padding), 0, this.editWidth);
            const y1 = clamp(Math.ceil(Math.max(from.y, to.y) + padding), 0, this.editHeight);
            const width = x1 - x0;
            const height = y1 - y0;
            if (width <= 0 || height <= 0) return null;
            return { x: x0, y: y0, w: width, h: height };
        }

        eraseRegion(entry, maskCanvas, box) {
            const ctx = entry.ctx;
            ctx.save();
            ctx.globalCompositeOperation = 'destination-out';
            ctx.imageSmoothingEnabled = false;
            ctx.drawImage(maskCanvas, box.x, box.y, box.w, box.h, box.x, box.y, box.w, box.h);
            ctx.restore();
        }

        paintLiveSegment(from, to, size, erase, colorName) {
            const ctx = this.paintCtx;
            if (!ctx) return;
            // 反选时显示是"反过来"的：画笔是从视图里挖掉，橡皮是往视图里补
            const remove = this.inverted ? !erase : erase;
            const tint = this.mode === 'color' ? (COLORS[colorName] || COLORS.red) : MASK_TINT;
            ctx.save();
            ctx.globalCompositeOperation = remove ? 'destination-out' : 'source-over';
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.lineWidth = size;
            ctx.strokeStyle = tint;
            ctx.fillStyle = tint;
            ctx.beginPath();
            if (from.x === to.x && from.y === to.y) {
                ctx.arc(to.x, to.y, size / 2, 0, Math.PI * 2);
                ctx.fill();
            } else {
                ctx.moveTo(from.x, from.y);
                ctx.lineTo(to.x, to.y);
                ctx.stroke();
            }
            ctx.restore();
        }

        finishStroke() {
            const state = this.drawing;
            this.drawing = null;
            if (!state) return;
            state.maskCanvas = null;
            state.maskCtx = null;
            this.pushOp(state.op);
            this.markDirty(this.affectedColors(state.op));
            this.scheduleRender();
            this.updateHistory();
        }

        // ---- 选框 ----
        beginMarquee(event, pos) {
            this.marquee = {
                startX: pos.x,
                startY: pos.y,
                currentX: pos.x,
                currentY: pos.y,
                subtract: !!event.altKey,
                square: !!event.shiftKey
            };
            this.drawMarqueePreview();
            this.updateCursor();
        }

        marqueeRect() {
            const state = this.marquee;
            if (!state) return null;
            let dx = state.currentX - state.startX;
            let dy = state.currentY - state.startY;
            if (state.square) {
                const side = Math.max(Math.abs(dx), Math.abs(dy));
                dx = (dx < 0 ? -1 : 1) * side;
                dy = (dy < 0 ? -1 : 1) * side;
            }
            let x = state.startX;
            let y = state.startY;
            let width = dx;
            let height = dy;
            if (width < 0) {
                x += width;
                width = -width;
            }
            if (height < 0) {
                y += height;
                height = -height;
            }
            const x0 = clamp(x, 0, this.editWidth);
            const y0 = clamp(y, 0, this.editHeight);
            const x1 = clamp(x + width, 0, this.editWidth);
            const y1 = clamp(y + height, 0, this.editHeight);
            return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
        }

        clearMarqueePreview() {
            if (!this.marqueeCanvas) return;
            const ctx = this.marqueeCanvas.getContext('2d');
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, this.marqueeCanvas.width, this.marqueeCanvas.height);
        }

        drawMarqueePreview() {
            if (!this.marqueeCanvas || !this.editWidth) return;
            const stageRect = this.stage.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            const pixelWidth = Math.max(1, Math.round(stageRect.width * dpr));
            const pixelHeight = Math.max(1, Math.round(stageRect.height * dpr));
            if (this.marqueeCanvas.width !== pixelWidth || this.marqueeCanvas.height !== pixelHeight) {
                this.marqueeCanvas.width = pixelWidth;
                this.marqueeCanvas.height = pixelHeight;
            }
            const ctx = this.marqueeCanvas.getContext('2d');
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, pixelWidth, pixelHeight);
            if (!this.marquee) return;
            const rect = this.marqueeRect();
            if (!rect || rect.w <= 0 || rect.h <= 0) return;
            const imageRect = this.paint.getBoundingClientRect();
            const scaleX = imageRect.width / this.editWidth;
            const scaleY = imageRect.height / this.editHeight;
            const x = imageRect.left - stageRect.left + rect.x * scaleX;
            const y = imageRect.top - stageRect.top + rect.y * scaleY;
            const width = rect.w * scaleX;
            const height = rect.h * scaleY;
            ctx.save();
            ctx.scale(dpr, dpr);
            ctx.fillStyle = this.marquee.subtract ? 'rgba(248,113,113,0.18)' : 'rgba(56,189,248,0.16)';
            ctx.fillRect(x, y, width, height);
            ctx.setLineDash([]);
            ctx.lineWidth = 3;
            ctx.strokeStyle = 'rgba(0,0,0,0.55)';
            ctx.strokeRect(x, y, width, height);
            ctx.setLineDash([7, 5]);
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = '#ffffff';
            ctx.strokeRect(x, y, width, height);
            ctx.restore();
        }

        finishMarquee() {
            const state = this.marquee;
            if (!state) return;
            // 先按当前选框算矩形，再清空状态（marqueeRect() 依赖 this.marquee）
            const rect = this.marqueeRect();
            this.marquee = null;
            this.clearMarqueePreview();
            if (!rect || rect.w < 1 || rect.h < 1) return;
            const op = {
                kind: 'rect',
                x: rect.x,
                y: rect.y,
                w: rect.w,
                h: rect.h,
                color: this.colorName,
                erase: !!state.subtract,
                scope: this.mode === 'mask' ? 'all' : 'color'
            };
            this.pushOp(op);
            this.applyRectOp(op);
            this.markDirty(this.affectedColors(op));
            this.scheduleRender();
            this.updateHistory();
        }

        // ---- 颜色区域 ----
        hitColorPixel(pos) {
            const x = Math.floor(pos.x);
            const y = Math.floor(pos.y);
            if (x < 0 || y < 0 || x >= this.editWidth || y >= this.editHeight) return null;
            this.ensureFilled();
            for (let index = COLOR_ORDER.length - 1; index >= 0; index--) {
                const name = COLOR_ORDER[index];
                const canvas = this.fillHoles ? this.filledLayers.get(name) : null;
                const entry = this.layers.get(name);
                const source = canvas || (entry ? entry.canvas : null);
                if (!source) continue;
                const pixel = source.getContext('2d', { willReadFrequently: true })
                    .getImageData(x, y, 1, 1).data;
                if (pixel[3] <= PAINT_ALPHA) continue;
                const seed = this.resolveSeed(name, x, y);
                if (seed) return { name: name, x: seed.x, y: seed.y };
            }
            return null;
        }

        resolveSeed(name, x, y) {
            const entry = this.layers.get(name);
            if (!entry) return null;
            const exact = entry.ctx.getImageData(x, y, 1, 1).data;
            if (exact[3] > PAINT_ALPHA) return { x: x, y: y };
            // 点在自动填充产生的空洞里：在附近找真实笔迹作为种子，重放时才可复现
            const radius = 14;
            const x0 = Math.max(0, x - radius);
            const y0 = Math.max(0, y - radius);
            const x1 = Math.min(this.editWidth, x + radius + 1);
            const y1 = Math.min(this.editHeight, y + radius + 1);
            const width = x1 - x0;
            const height = y1 - y0;
            if (width <= 0 || height <= 0) return null;
            const data = entry.ctx.getImageData(x0, y0, width, height).data;
            let best = null;
            let bestDistance = Infinity;
            for (let row = 0; row < height; row++) {
                for (let column = 0; column < width; column++) {
                    const offset = (row * width + column) * 4;
                    if (data[offset + 3] <= PAINT_ALPHA) continue;
                    const dx = x0 + column - x;
                    const dy = y0 + row - y;
                    const distance = dx * dx + dy * dy;
                    if (distance < bestDistance) {
                        bestDistance = distance;
                        best = { x: x0 + column, y: y0 + row };
                    }
                }
            }
            return best;
        }

        openColorMenu(clientX, clientY, hit) {
            this.closeColorMenu();
            const menu = document.createElement('div');
            menu.className = 'me-color-menu';
            COLOR_ORDER.forEach((name) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'me-color-menu-dot';
                button.style.background = COLORS[name];
                button.title = this.label(COLOR_LABEL_KEYS[name]);
                button.setAttribute('aria-label', this.label(COLOR_LABEL_KEYS[name]));
                button.addEventListener('click', () => {
                    this.changeComponentColor(hit, name);
                    this.closeColorMenu();
                });
                menu.appendChild(button);
            });
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'me-color-menu-delete';
            remove.textContent = '\u00d7';
            remove.title = this.label('clear');
            remove.addEventListener('click', () => {
                this.changeComponentColor(hit, null);
                this.closeColorMenu();
            });
            menu.appendChild(remove);
            this.root.appendChild(menu);
            const rect = menu.getBoundingClientRect();
            menu.style.left = clamp(clientX, 8, Math.max(8, window.innerWidth - rect.width - 8)) + 'px';
            menu.style.top = clamp(clientY, 8, Math.max(8, window.innerHeight - rect.height - 8)) + 'px';
            this.colorMenu = menu;
            // 下一轮事件循环再挂"点外面关闭"，避免被当前 pointerup 立刻关掉
            this.colorMenuTimer = setTimeout(() => {
                this.onColorMenuOutside = (event) => {
                    if (!menu.contains(event.target)) this.closeColorMenu();
                };
                window.addEventListener('pointerdown', this.onColorMenuOutside, true);
            }, 0);
        }

        closeColorMenu() {
            if (this.colorMenuTimer) {
                clearTimeout(this.colorMenuTimer);
                this.colorMenuTimer = 0;
            }
            if (this.onColorMenuOutside) {
                window.removeEventListener('pointerdown', this.onColorMenuOutside, true);
                this.onColorMenuOutside = null;
            }
            if (this.colorMenu && this.colorMenu.parentNode) {
                this.colorMenu.parentNode.removeChild(this.colorMenu);
            }
            this.colorMenu = null;
        }

        changeComponentColor(hit, to) {
            if (!hit) return;
            const op = {
                kind: 'recolor',
                from: hit.name,
                to: to,
                seed: { x: hit.x, y: hit.y }
            };
            this.pushOp(op);
            this.applyOp(op);
            this.markDirty(this.affectedColors(op));
            this.render();
            this.updateHistory();
        }

        // ---- 快捷键 ----
        handleKeyDown(event) {
            if (this.closed) return;
            const key = event.key || '';
            const code = event.code || '';
            const ctrl = !!(event.ctrlKey || event.metaKey);
            if (this.busy) return;
            if (key === 'Escape' || code === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                if (event.stopImmediatePropagation) event.stopImmediatePropagation();
                this.cancel();
                return;
            }
            if (isEditableTarget(event.target)) return;
            let handled = true;
            if (ctrl && !event.shiftKey && code === 'KeyZ') this.undo();
            else if (ctrl && event.shiftKey && (code === 'KeyZ' || code === 'KeyY')) this.redo();
            else if (ctrl && !event.shiftKey && code === 'KeyI') this.invert();
            else if (!ctrl && code === 'KeyB') this.setTool('brush');
            else if (!ctrl && code === 'KeyE') this.setTool('eraser');
            else if (!ctrl && code === 'KeyM') this.setTool('marquee');
            else if (!ctrl && code === 'KeyX') this.clear();
            else if (!ctrl && (code === 'Enter' || code === 'NumpadEnter')) this.save();
            else if (!ctrl && code === 'BracketLeft') this.setBrushSize(this.brushSize - 5);
            else if (!ctrl && code === 'BracketRight') this.setBrushSize(this.brushSize + 5);
            else if (code === 'Space') {
                this.spaceDown = true;
                this.updateCursor();
            } else if (key === 'Alt' || code === 'AltLeft' || code === 'AltRight') {
                this.altDown = true;
                this.updateCursor();
            } else if (key === 'Shift' || code === 'ShiftLeft' || code === 'ShiftRight') {
                this.shiftDown = true;
            } else {
                handled = false;
            }
            if (!handled) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.stopImmediatePropagation) event.stopImmediatePropagation();
        }

        handleKeyUp(event) {
            if (this.closed) return;
            const key = event.key || '';
            const code = event.code || '';
            if (code === 'Space') this.spaceDown = false;
            if (key === 'Alt' || code === 'AltLeft' || code === 'AltRight') this.altDown = false;
            if (key === 'Shift' || code === 'ShiftLeft' || code === 'ShiftRight') this.shiftDown = false;
            this.updateCursor();
        }

        // ---- 保存 ----
        async save() {
            if (this.busy || this.closed || !this.editWidth) return;
            this.setBusy(true);
            try {
                // 同步补齐自动填充，保证导出与屏幕显示一致
                this.render();
                const result = await this.buildResult();
                if (this.onSave) await this.onSave(result);
                this.close(true);
            } catch (error) {
                if (window.console && console.warn) console.warn('MaskEditor: 保存失败', error);
                this.setBusy(false);
                // 调用方若给了具体原因就显示出来，比统一的“保存失败”更有用
                const message = error && error.message ? String(error.message) : '';
                this.showToast(message || this.label('saveFail'));
            }
        }

        async buildResult() {
            const maskCanvas = this.buildMaskCanvas();
            const maskBlob = await canvasToBlob(maskCanvas);
            let colorBlob = null;
            let paintBlob = null;
            if (this.mode === 'color') {
                colorBlob = await canvasToBlob(this.buildColorCanvas());
                // 纯色块小图：只为了下次打开编辑器时能原样恢复每种颜色，不参与生成
                paintBlob = await canvasToBlob(this.buildPaintCanvas());
            }
            return {
                maskBlob: maskBlob,
                colorBlob: colorBlob,
                paintBlob: paintBlob,
                mode: this.mode,
                colorName: this.colorName,
                opacity: this.opacity,
                filled: this.fillHoles,
                inverted: this.inverted,
                width: this.naturalWidth,
                height: this.naturalHeight,
                // 只有调用方给了 promptBox 才回传提示词
                prompt: this.promptInput ? String(this.promptInput.value || '').trim() : ''
            };
        }

        /**
         * 导出遮罩：按原图尺寸最近邻放大后阈值化。
         * 涂抹区 RGB=255、其余 RGB=0、alpha 恒 255（保持旧版不透明黑白 PNG 格式）。
         * 自动填充与反选都是"视图"，这里把它们烘焙进结果（同时把 filled/inverted 标志回传）。
         */
        buildMaskCanvas() {
            const canvas = createCanvas(this.naturalWidth, this.naturalHeight);
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            ctx.imageSmoothingEnabled = false;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.drawImage(this.compositeCanvas, 0, 0, canvas.width, canvas.height);
            const inverted = this.inverted;
            const band = 256;
            for (let y = 0; y < canvas.height; y += band) {
                const height = Math.min(band, canvas.height - y);
                const image = ctx.getImageData(0, y, canvas.width, height);
                const data = image.data;
                for (let offset = 0; offset < data.length; offset += 4) {
                    const painted = data[offset + 3] > PAINT_ALPHA;
                    const value = painted !== inverted ? 255 : 0;
                    data[offset] = value;
                    data[offset + 1] = value;
                    data[offset + 2] = value;
                    data[offset + 3] = 255;
                }
                ctx.putImageData(image, 0, y);
            }
            return canvas;
        }

        /**
         * 导出纯色块小图：编辑分辨率下，涂抹区是所选颜色的纯色，其余透明。
         * 仅用于重新打开编辑器时还原"哪块是哪一色"，不会被当成图片素材使用。
         */
        buildPaintCanvas() {
            const canvas = createCanvas(this.editWidth, this.editHeight);
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            ctx.imageSmoothingEnabled = false;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            // 直接取当前叠加视图：它已经把自动填充漏洞、反选都算进去了，
            // 重新打开时按这里记录的颜色还原，和保存前看到的完全一致。
            ctx.drawImage(this.paint, 0, 0);
            const band = 256;
            for (let y = 0; y < canvas.height; y += band) {
                const height = Math.min(band, canvas.height - y);
                const image = ctx.getImageData(0, y, canvas.width, height);
                const data = image.data;
                for (let offset = 0; offset < data.length; offset += 4) {
                    if (data[offset + 3] > 8) data[offset + 3] = 255;
                    else data[offset + 3] = 0;
                }
                ctx.putImageData(image, 0, y);
            }
            return canvas;
        }

        /**
         * 导出颜色叠加：原图 + 当前叠加视图（按 opacity 烘焙），并强制全不透明。
         */
        buildColorCanvas() {
            const canvas = createCanvas(this.naturalWidth, this.naturalHeight);
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            ctx.imageSmoothingEnabled = false;
            ctx.drawImage(this.imageEl, 0, 0, canvas.width, canvas.height);

            const overlay = createCanvas(this.naturalWidth, this.naturalHeight);
            const overlayCtx = overlay.getContext('2d');
            overlayCtx.imageSmoothingEnabled = false;
            overlayCtx.drawImage(this.paint, 0, 0, this.editWidth, this.editHeight,
                0, 0, canvas.width, canvas.height);
            ctx.save();
            ctx.globalAlpha = clamp(this.opacity, 0, 100) / 100;
            ctx.drawImage(overlay, 0, 0);
            ctx.restore();

            const band = 256;
            for (let y = 0; y < canvas.height; y += band) {
                const height = Math.min(band, canvas.height - y);
                const image = ctx.getImageData(0, y, canvas.width, height);
                const data = image.data;
                for (let offset = 0; offset < data.length; offset += 4) data[offset + 3] = 255;
                ctx.putImageData(image, 0, y);
            }
            return canvas;
        }
    }

    // 单实例：同时只允许一个遮罩编辑器
    let activeSession = null;

    const MaskEditor = {
        open: function (opts) {
            if (!opts || !opts.imageUrl) {
                return Promise.reject(new Error('MaskEditor.open: imageUrl is required'));
            }
            if (activeSession) activeSession.close(false);
            const session = new MaskEditorSession(opts);
            activeSession = session;
            return session.start();
        }
    };

    window.MaskEditor = MaskEditor;
})();