/*
 * 全屏批注编辑器：与遮罩编辑器（mask-editor.js）同一套外壳与视觉语言，但语义不同。
 *
 * - 输出「原图 + 批注」的扁平 PNG（flattenBlob），挂在原图片条目上做预览与下游输入；
 * - 另外输出只含笔迹的透明 PNG（overlayBlob），作为下次继续批注的底稿；
 * - 撤销/重做使用矢量操作列表重放，不保存 ImageData 快照；
 * - Ctrl+滚轮调整工具大小，普通滚轮缩放；右键左右拖动也能调整大小。
 *
 * 依赖：static/css/mask-editor.css（外壳样式）+ static/css/annotation-editor.css（增量样式）。
 * 该文件必须早于 smart-canvas.js 加载，页面通过 window.AnnotationEditor 打开编辑器。
 */
(function () {
    'use strict';

    // ---- 常量 ----
    const MAX_EDIT_LONG_SIDE = 1536;   // 交互画布长边上限，控制内存与手感
    const MAX_EXPORT_LONG_SIDE = 4096; // 导出长边上限，避免超大原图把浏览器画布撑爆
    const MAX_OPS = 200;
    const MIN_SCALE = 0.05;
    const MAX_SCALE = 12;
    const MAX_FIT_SCALE = 8;
    const MIN_BRUSH = 2;
    const MAX_BRUSH = 200;
    const DEFAULT_BRUSH = 16;
    const MIN_TEXT = 10;
    const MAX_TEXT = 200;
    const MIN_OPACITY = 10;            // 画笔不透明度滑杆下限（%）
    const MAX_OPACITY = 100;
    const DEFAULT_OPACITY = 100;
    const ROOT_Z_INDEX = '2147483000';
    const IMAGE_LOAD_TIMEOUT = 30000;
    const MARK_ALPHA = 8;              // 判定「画过东西」的 alpha 阈值
    const STROKE_TINT = 'rgba(255,255,255,.92)';
    const HANDLE_HIT = 10;             // 选中手柄的命中半径（屏幕像素）

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
    const DEFAULT_COLOR = 'red';

    const TOOL_KEYS = { brush: 'B', eraser: 'E', rect: 'R', ellipse: 'O', label: 'N', text: 'T' };

    const SVG_OPEN = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" ' +
        'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';

    const ICONS = {
        brush: SVG_OPEN + '<path d="M10.4 2.6 13.4 5.6 6.8 12.2 3 13 3.8 9.2z"/><path d="M9.2 3.8 12.2 6.8"/></svg>',
        eraser: SVG_OPEN + '<path d="M6.2 12.6 2.8 9.2a1.4 1.4 0 0 1 0-2L8.4 1.6a1.4 1.4 0 0 1 2 0l2.8 2.8a1.4 1.4 0 0 1 0 2l-6 6z"/><path d="M4.6 10.8 9 6.4"/><path d="M8.6 13.2h4.6"/></svg>',
        rect: SVG_OPEN + '<rect x="2.4" y="3.4" width="11.2" height="9.2" rx="1.2"/></svg>',
        ellipse: SVG_OPEN + '<ellipse cx="8" cy="8" rx="5.8" ry="4.8"/></svg>',
        label: SVG_OPEN + '<circle cx="8" cy="8" r="6"/><path d="M8 5.2v5.6M6.2 10.8h3.6"/></svg>',
        text: SVG_OPEN + '<path d="M3.2 3.6h9.6M8 3.6v9"/><path d="M6 12.6h4"/></svg>',
        undo: SVG_OPEN + '<path d="M5.6 4.2 2.6 7.2l3 3"/><path d="M2.8 7.2h6.2a3.4 3.4 0 0 1 0 6.8H6.2"/></svg>',
        redo: SVG_OPEN + '<path d="M10.4 4.2l3 3-3 3"/><path d="M13.2 7.2H7a3.4 3.4 0 0 0 0 6.8h2.8"/></svg>',
        trash: SVG_OPEN + '<path d="M3.2 4.6h9.6"/><path d="M5.4 4.6V3.2h5.2v1.4"/><path d="M4.6 4.6l.6 8.2h5.6l.6-8.2"/><path d="M6.8 6.8v4M9.2 6.8v4"/></svg>',
        close: SVG_OPEN + '<path d="M4 4l8 8M12 4l-8 8"/></svg>',
        check: SVG_OPEN + '<path d="M3 8.6 6.2 12 13 4.6"/></svg>',
        fit: SVG_OPEN + '<path d="M2.6 6V2.6H6M10 2.6h3.4V6M13.4 10v3.4H10M6 13.4H2.6V10"/></svg>',
        plus: SVG_OPEN + '<path d="M8 3.4v9.2M3.4 8h9.2"/></svg>',
        minus: SVG_OPEN + '<path d="M3.4 8h9.2"/></svg>'
    };

    // ---- 工具函数 ----
    function clamp(value, min, max) {
        const number = Number(value);
        if (!Number.isFinite(number)) return min;
        return Math.min(max, Math.max(min, number));
    }

    function createCanvas(width, height) {
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(width));
        canvas.height = Math.max(1, Math.round(height));
        return canvas;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function waitForImage(img, url) {
        return new Promise(function (resolve, reject) {
            let settled = false;
            const started = Date.now();
            const timer = setInterval(function () {
                if (img.complete && img.naturalWidth > 0) {
                    cleanup();
                    resolve(img);
                } else if (Date.now() - started > IMAGE_LOAD_TIMEOUT) {
                    cleanup();
                    reject(new Error('image load timeout'));
                }
            }, 60);
            function cleanup() {
                if (settled) return;
                settled = true;
                clearInterval(timer);
                img.onload = null;
                img.onerror = null;
            }
            img.onload = function () { cleanup(); resolve(img); };
            img.onerror = function () { cleanup(); reject(new Error('image load failed')); };
            img.src = url;
        });
    }

    function canvasToBlob(canvas) {
        return new Promise(function (resolve) {
            if (!canvas) { resolve(null); return; }
            try {
                canvas.toBlob(function (blob) { resolve(blob || null); }, 'image/png');
            } catch (error) {
                resolve(null);
            }
        });
    }

    function circledNumber(number) {
        const value = Math.max(1, Math.round(Number(number) || 1));
        return value <= 20 ? String.fromCharCode(0x2460 + value - 1) : String(value);
    }

    function textFont(size) {
        return '900 ' + Math.max(1, Math.round(size)) + 'px Arial, sans-serif';
    }

    function textLines(item) {
        return String(item && item.text != null ? item.text : '').split('\n');
    }

    // ---- 编辑器会话 ----
    class AnnotationEditorSession {
        constructor(opts) {
            this.opts = opts || {};
            this.labels = this.opts.labels || {};
            this.imageUrl = this.opts.imageUrl || '';
            this.overlayUrl = this.opts.overlayUrl || '';
            // 归一化裁切范围（0–1）。只用来在整图上画一圈虚线提示，
            // 让用户知道哪些内容已经在裁切之外，不会被画进最终结果。
            this.cropRect = this.opts.cropRect || null;
            this.onSave = typeof this.opts.onSave === 'function' ? this.opts.onSave : null;

            this.root = null;
            this.imgEl = null;
            this.paint = null;
            this.paintCtx = null;
            this.stage = null;
            this.marquee = null;
            this.marqueeCtx = null;
            this.cursor = null;
            this.viewport = null;

            this.naturalWidth = 0;
            this.naturalHeight = 0;
            this.editWidth = 0;
            this.editHeight = 0;

            this.view = { x: 0, y: 0, scale: 1 };
            this.userAdjusted = false;

            this.tool = 'brush';
            this.brushSize = DEFAULT_BRUSH;
            this.brushOpacity = DEFAULT_OPACITY;
            this.colorName = DEFAULT_COLOR;
            this.labelCounter = 1;

            this.ops = [];
            this.redoOps = [];
            this.textItems = new Map();
            this.selectedTextId = '';
            this.hiddenTextIds = new Set();
            this.editingId = '';
            this.editingIsNew = false;
            this.editingItem = null;
            this.textEditorEl = null;
            this.suppressTextCreateAt = 0;

            this.baseline = null;
            this.draft = null;
            this.strokeOp = null;
            this.pan = null;
            this.rightDrag = null;
            this.textDrag = null;

            this.spaceDown = false;
            this.altDown = false;
            this.busy = false;
            this.closed = false;
            this.ready = false;
            this.saved = false;
            this.pointerId = null;

            this.boundHandlers = null;

            const session = this;
            this.promise = new Promise(function (resolve) {
                session.resolve = resolve;
            });
        }

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
                await this.loadOverlay();
                this.render();
                this.handleResize();
                this.fitToStage();
                this.root.classList.add('is-ready');
                this.root.focus({ preventScroll: true });
            } catch (error) {
                this.showError(this.label('loadFail'));
                if (window.console && console.warn) console.warn('AnnotationEditor: 图片加载失败', error);
            }
            return this.promise;
        }

        close(saved) {
            if (this.closed) return;
            this.closed = true;
            this.saved = !!saved;
            this.cancelTextEditing();
            if (this.boundHandlers) {
                document.removeEventListener('keydown', this.boundHandlers.keydown, true);
                document.removeEventListener('keyup', this.boundHandlers.keyup, true);
                window.removeEventListener('resize', this.boundHandlers.resize);
                window.removeEventListener('blur', this.boundHandlers.blur);
            }
            if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
            this.root = null;
            this.paintCtx = null;
            this.marqueeCtx = null;
            this.resolve(!!saved);
        }

        // ---- DOM ----
        buildDom() {
            const labels = this.labels;
            const root = document.createElement('div');
            root.className = 'me-root ae-root';
            root.tabIndex = -1;
            root.setAttribute('role', 'dialog');
            root.setAttribute('aria-modal', 'true');
            root.setAttribute('aria-label', this.label('title'));
            root.style.zIndex = ROOT_Z_INDEX;

            const tools = [
                { tool: 'brush', icon: ICONS.brush, text: labels.toolBrush },
                { tool: 'eraser', icon: ICONS.eraser, text: labels.toolEraser },
                { tool: 'rect', icon: ICONS.rect, text: labels.toolRect },
                { tool: 'ellipse', icon: ICONS.ellipse, text: labels.toolEllipse },
                { tool: 'label', icon: ICONS.label, text: labels.toolLabel },
                { tool: 'text', icon: ICONS.text, text: labels.toolText }
            ].map(function (item) {
                return '<button type="button" class="me-tool" data-tool="' + item.tool + '" ' +
                    'title="' + escapeHtml(item.text) + ' (' + TOOL_KEYS[item.tool] + ')">' +
                    item.icon + '<span class="me-tool-text">' + escapeHtml(item.text) + '</span></button>';
            }).join('');

            const swatches = COLOR_ORDER.map(function (name) {
                return '<button type="button" class="me-swatch" data-color="' + name + '" ' +
                    'title="' + escapeHtml(labels['color' + name.charAt(0).toUpperCase() + name.slice(1)] || name) + '" ' +
                    'aria-label="' + escapeHtml(labels['color' + name.charAt(0).toUpperCase() + name.slice(1)] || name) + '"></button>';
            }).join('');

            root.innerHTML =
                '<div class="me-toolbar">' +
                    '<div class="me-title">' + escapeHtml(this.label('title')) + '</div>' +
                    '<div class="me-tool-group">' + tools + '</div>' +
                    '<div class="me-size">' +
                        '<span class="me-size-text">' + escapeHtml(this.label('sizeText')) + '</span>' +
                        '<input type="range" class="me-size-range" min="' + MIN_BRUSH + '" max="' + MAX_BRUSH +
                            '" step="1" value="' + this.brushSize + '" aria-label="' +
                            escapeHtml(this.label('sizeText')) + '">' +
                        '<span class="me-size-value">' + this.brushSize + 'px</span>' +
                    '</div>' +
                    '<div class="me-size me-toolbar-opacity">' +
                        '<span class="me-size-text">' + escapeHtml(this.label('opacityText')) + '</span>' +
                        '<input type="range" class="me-size-range me-toolbar-opacity-range" min="' + MIN_OPACITY + '" max="' + MAX_OPACITY +
                            '" step="5" value="' + this.brushOpacity + '" aria-label="' +
                            escapeHtml(this.label('opacityText')) + '">' +
                        '<span class="me-size-value">' + this.brushOpacity + '%</span>' +
                    '</div>' +
                    '<div class="me-actions">' +
                        '<button type="button" class="me-icon-btn" data-act="undo" title="' +
                            escapeHtml(this.label('undo')) + ' (Ctrl+Z)">' + ICONS.undo + '</button>' +
                        '<button type="button" class="me-icon-btn" data-act="redo" title="' +
                            escapeHtml(this.label('redo')) + ' (Ctrl+Shift+Z)">' + ICONS.redo + '</button>' +
                        '<button type="button" class="me-icon-btn" data-act="clear" title="' +
                            escapeHtml(this.label('clear')) + ' (X)">' + ICONS.trash + '</button>' +
                        '<span class="me-divider" aria-hidden="true"></span>' +
                        '<button type="button" class="me-text-btn" data-act="cancel" title="Esc">' +
                            escapeHtml(this.label('cancel')) + '</button>' +
                        '<button type="button" class="me-primary-btn" data-act="save" title="Enter">' +
                            ICONS.check + '<span class="me-save-text">' + escapeHtml(this.label('save')) + '</span>' +
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
                        '<aside class="me-panel ae-panel">' +
                            '<div class="me-swatches">' + swatches + '</div>' +
                            '<div class="ae-panel-note">' + escapeHtml(this.label('panelNote')) + '</div>' +
                            '<div class="me-zoom">' +
                                '<button type="button" class="me-panel-btn" data-act="fit" title="' +
                                    escapeHtml(this.label('fit')) + '">' + ICONS.fit +
                                    '<span>' + escapeHtml(this.label('fit')) + '</span></button>' +
                                '<div class="me-zoom-ctl">' +
                                    '<button type="button" class="me-icon-btn" data-act="zoom-out" title="' +
                                        escapeHtml(this.label('zoomOut')) + '">' + ICONS.minus + '</button>' +
                                    '<span class="me-zoom-value">100%</span>' +
                                    '<button type="button" class="me-icon-btn" data-act="zoom-in" title="' +
                                        escapeHtml(this.label('zoomIn')) + '">' + ICONS.plus + '</button>' +
                                '</div>' +
                            '</div>' +
                        '</aside>' +
                        '<div class="me-empty-hint">' + escapeHtml(this.label('emptyHint')) + '</div>' +
                        '<div class="me-error-box" hidden>' +
                            '<div class="me-error-text"></div>' +
                            '<button type="button" class="me-text-btn me-error-close">' +
                                escapeHtml(this.label('cancel')) + '</button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="me-footer">' +
                    '<span class="me-footer-hint">' + escapeHtml(this.label('hint')) + '</span>' +
                    '<span class="me-footer-shortcut">' + escapeHtml(this.label('shortcutHint')) + '</span>' +
                '</div>' +
                '<div class="me-toast" hidden></div>';

            document.body.appendChild(root);
            this.root = root;

            this.imgEl = root.querySelector('.me-image');
            this.paint = root.querySelector('.me-paint');
            this.paintCtx = this.paint.getContext('2d');
            this.stage = root.querySelector('.me-stage');
            this.viewport = root.querySelector('.me-viewport');
            this.marquee = root.querySelector('.me-marquee-canvas');
            this.marqueeCtx = this.marquee.getContext('2d');
            this.cursor = root.querySelector('.me-cursor');
            this.sizeInput = root.querySelector('.me-size-range');
            this.sizeValue = root.querySelector('.me-size-value');
            this.opacityInput = root.querySelector('.me-toolbar-opacity-range');
            this.opacityValue = root.querySelector('.me-toolbar-opacity .me-size-value');
            this.zoomValue = root.querySelector('.me-zoom-value');
            this.emptyHint = root.querySelector('.me-empty-hint');
            this.errorBox = root.querySelector('.me-error-box');
            this.errorText = root.querySelector('.me-error-text');
            this.toastEl = root.querySelector('.me-toast');
            this.saveBtn = root.querySelector('[data-act="save"]');
            this.saveText = root.querySelector('.me-save-text');
            this.undoBtn = root.querySelector('[data-act="undo"]');
            this.redoBtn = root.querySelector('[data-act="redo"]');

            const swatchButtons = root.querySelectorAll('.me-swatch');
            for (let i = 0; i < swatchButtons.length; i += 1) {
                const button = swatchButtons[i];
                const name = button.dataset.color;
                if (COLORS[name]) button.style.background = COLORS[name];
            }
            this.sizeInput.value = String(this.brushSize);
            this.updateSwatches();
        }

        bindEvents() {
            const session = this;

            this.onClick = function (event) { session.handleClick(event); };
            this.onContextMenu = function (event) { event.preventDefault(); };
            this.onWheelRoot = function (event) { session.handleWheel(event); };
            this.onPointerDown = function (event) { session.handlePointerDown(event); };
            this.onPointerMove = function (event) { session.handlePointerMove(event); };
            this.onPointerUp = function (event) { session.handlePointerUp(event); };
            this.onPointerCancel = function (event) { session.handlePointerUp(event); };
            this.onDblClick = function (event) { session.handleDoubleClick(event); };
            this.onSizeInput = function (event) { session.setBrushSize(event.target.value); };
            this.onOpacityInput = function (event) { session.setBrushOpacity(event.target.value); };
            this.onErrorClose = function () { session.hideError(); };

            this.root.addEventListener('click', this.onClick);
            this.root.addEventListener('contextmenu', this.onContextMenu);
            this.root.addEventListener('wheel', this.onWheelRoot, { passive: false });
            this.stage.addEventListener('pointerdown', this.onPointerDown);
            this.stage.addEventListener('pointermove', this.onPointerMove);
            this.stage.addEventListener('pointerup', this.onPointerUp);
            this.stage.addEventListener('pointercancel', this.onPointerCancel);
            this.stage.addEventListener('dblclick', this.onDblClick);
            this.sizeInput.addEventListener('input', this.onSizeInput);
            if (this.opacityInput) this.opacityInput.addEventListener('input', this.onOpacityInput);
            this.errorBox.querySelector('.me-error-close').addEventListener('click', this.onErrorClose);

            this.boundHandlers = {
                keydown: function (event) { session.handleKeyDown(event); },
                keyup: function (event) { session.handleKeyUp(event); },
                resize: function () { session.handleResize(); },
                blur: function () {
                    session.spaceDown = false;
                    session.altDown = false;
                }
            };
            document.addEventListener('keydown', this.boundHandlers.keydown, true);
            document.addEventListener('keyup', this.boundHandlers.keyup, true);
            window.addEventListener('resize', this.boundHandlers.resize);
            window.addEventListener('blur', this.boundHandlers.blur);
        }

        // ---- 载入 ----
        async loadSource() {
            await waitForImage(this.imgEl, this.imageUrl);
            this.naturalWidth = this.imgEl.naturalWidth || this.imgEl.width || 1;
            this.naturalHeight = this.imgEl.naturalHeight || this.imgEl.height || 1;
            const shrink = Math.min(1, MAX_EDIT_LONG_SIDE / Math.max(this.naturalWidth, this.naturalHeight));
            this.editWidth = Math.max(1, Math.round(this.naturalWidth * shrink));
            this.editHeight = Math.max(1, Math.round(this.naturalHeight * shrink));
            this.imgEl.style.width = this.editWidth + 'px';
            this.imgEl.style.height = this.editHeight + 'px';
            this.paint.width = this.editWidth;
            this.paint.height = this.editHeight;
            this.paint.style.width = this.editWidth + 'px';
            this.paint.style.height = this.editHeight + 'px';
        }

        async loadOverlay() {
            if (!this.overlayUrl) return;
            const image = new Image();
            try {
                await waitForImage(image, this.overlayUrl);
                this.baseline = image;
            } catch (error) {
                this.baseline = null;
                if (window.console && console.warn) console.warn('AnnotationEditor: 底稿加载失败，将以空底稿继续', error);
            }
        }

        // ---- 视图 ----
        handleResize() {
            if (!this.stage || !this.marquee) return;
            const dpr = Math.max(1, Math.round(window.devicePixelRatio || 1));
            const width = Math.max(1, Math.round(this.stage.clientWidth));
            const height = Math.max(1, Math.round(this.stage.clientHeight));
            this.marquee.width = width * dpr;
            this.marquee.height = height * dpr;
            this.marquee.style.width = width + 'px';
            this.marquee.style.height = height + 'px';
            this.marqueeCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
            this.stageWidth = width;
            this.stageHeight = height;
            if (!this.userAdjusted) this.fitToStage();
            this.applyTransform();
        }

        applyTransform() {
            if (!this.viewport) return;
            this.viewport.style.transform = 'translate(' + this.view.x + 'px,' + this.view.y + 'px) scale(' + this.view.scale + ')';
            this.updateZoomLabel();
            this.drawSelection();
            this.updateCursor();
        }

        fitToStage() {
            if (!this.editWidth || !this.stageWidth || !this.stageHeight) return;
            const padding = 48;
            const fit = Math.min(
                (this.stageWidth - padding * 2) / this.editWidth,
                (this.stageHeight - padding * 2) / this.editHeight
            );
            const scale = clamp(fit, MIN_SCALE, MAX_FIT_SCALE);
            this.view.scale = scale;
            this.view.x = (this.stageWidth - this.editWidth * scale) / 2;
            this.view.y = (this.stageHeight - this.editHeight * scale) / 2;
            this.userAdjusted = false;
            this.applyTransform();
        }

        zoomBy(factor) {
            if (!this.stageWidth) return;
            this.zoomAt(this.stageWidth / 2, this.stageHeight / 2, factor);
        }

        zoomAt(stageX, stageY, factor) {
            const next = clamp(this.view.scale * factor, MIN_SCALE, MAX_SCALE);
            if (next === this.view.scale) return;
            const ratio = next / this.view.scale;
            this.view.x = stageX - (stageX - this.view.x) * ratio;
            this.view.y = stageY - (stageY - this.view.y) * ratio;
            this.view.scale = next;
            this.userAdjusted = true;
            this.applyTransform();
        }

        updateZoomLabel() {
            if (this.zoomValue) this.zoomValue.textContent = Math.round(this.view.scale * 100) + '%';
        }

        handleWheel(event) {
            if (this.closed) return;
            event.preventDefault();
            event.stopPropagation();
            if (!this.stage) return;
            if (event.ctrlKey || event.metaKey) {
                event.stopImmediatePropagation();
                const step = event.deltaY < 0 ? 1 : -1;
                this.adjustBrushSize(step);
                return;
            }
            const rect = this.stage.getBoundingClientRect();
            const direction = event.deltaY < 0 ? 1.12 : 0.89;
            this.zoomAt(event.clientX - rect.left, event.clientY - rect.top, direction);
        }

        imagePos(event) {
            if (!this.paint) return null;
            const rect = this.paint.getBoundingClientRect();
            if (!rect.width || !rect.height) return null;
            return {
                x: clamp((event.clientX - rect.left) * this.editWidth / rect.width, 0, this.editWidth),
                y: clamp((event.clientY - rect.top) * this.editHeight / rect.height, 0, this.editHeight)
            };
        }

        stagePoint(imageX, imageY) {
            return {
                x: this.view.x + imageX * this.view.scale,
                y: this.view.y + imageY * this.view.scale
            };
        }

        // ---- 工具与状态 ----
        setTool(tool) {
            const allowed = ['brush', 'eraser', 'rect', 'ellipse', 'label', 'text'];
            this.tool = allowed.indexOf(tool) >= 0 ? tool : 'brush';
            if (this.tool !== 'text') this.commitTextEditing();
            if (this.tool !== 'text') this.selectedTextId = '';
            this.updateToolbar();
            this.updateCursor();
            this.render();
        }

        setBrushSize(value) {
            const next = clamp(Math.round(Number(value) || DEFAULT_BRUSH), MIN_BRUSH, MAX_BRUSH);
            this.brushSize = next;
            if (this.sizeInput) this.sizeInput.value = String(next);
            if (this.sizeValue) this.sizeValue.textContent = next + 'px';
            this.updateCursor();
        }

        setBrushOpacity(value) {
            const next = clamp(Math.round(Number(value) || DEFAULT_OPACITY), MIN_OPACITY, MAX_OPACITY);
            this.brushOpacity = next;
            if (this.opacityInput) this.opacityInput.value = String(next);
            if (this.opacityValue) this.opacityValue.textContent = next + '%';
        }

        adjustBrushSize(delta) {
            if (this.tool === 'text' && this.selectedTextId) {
                const item = this.textItems.get(this.selectedTextId);
                if (item) {
                    const step = Math.max(1, Math.round(item.size * 0.1));
                    const next = clamp(Math.round(item.size + (delta > 0 ? step : -step)), MIN_TEXT, MAX_TEXT);
                    if (next === item.size) return;
                    this.setBrushSize(next);
                    this.pushOp({ kind: 'text', id: item.id, text: item.text, x: item.x, y: item.y, size: next, color: item.color });
                    this.render();
                    return;
                }
            }
            const step = Math.max(1, Math.round(this.brushSize * 0.12));
            this.setBrushSize(this.brushSize + (delta > 0 ? step : -step));
        }

        setColor(name) {
            if (!COLORS[name]) return;
            this.colorName = name;
            this.updateSwatches();
            if (this.tool === 'text' && this.selectedTextId) {
                const item = this.textItems.get(this.selectedTextId);
                if (item) {
                    item.color = COLORS[name];
                    this.pushOp({ kind: 'text', id: item.id, text: item.text, x: item.x, y: item.y, size: item.size, color: item.color });
                    this.render();
                }
            }
        }

        colorValue() {
            return COLORS[this.colorName] || COLORS[DEFAULT_COLOR];
        }

        updateToolbar() {
            if (!this.root) return;
            this.root.dataset.tool = this.tool;
            const buttons = this.root.querySelectorAll('.me-tool[data-tool]');
            for (let i = 0; i < buttons.length; i += 1) {
                const active = buttons[i].dataset.tool === this.tool;
                buttons[i].classList.toggle('is-active', active);
                buttons[i].setAttribute('aria-pressed', active ? 'true' : 'false');
            }
            if (this.sizeInput) this.sizeInput.value = String(this.brushSize);
            if (this.sizeValue) this.sizeValue.textContent = this.brushSize + 'px';
            if (this.opacityInput) this.opacityInput.value = String(this.brushOpacity);
            if (this.opacityValue) this.opacityValue.textContent = this.brushOpacity + '%';
        }

        updatePanel() {
            this.updateSwatches();
        }

        updateSwatches() {
            if (!this.root) return;
            const buttons = this.root.querySelectorAll('.me-swatch[data-color]');
            for (let i = 0; i < buttons.length; i += 1) {
                const active = buttons[i].dataset.color === this.colorName;
                buttons[i].classList.toggle('is-active', active);
                buttons[i].setAttribute('aria-pressed', active ? 'true' : 'false');
            }
        }

        updateHistory() {
            if (this.undoBtn) this.undoBtn.disabled = this.ops.length === 0;
            if (this.redoBtn) this.redoBtn.disabled = this.redoOps.length === 0;
        }

        updateEmptyHint() {
            if (!this.emptyHint) return;
            const empty = !this.baseline && this.ops.length === 0;
            this.emptyHint.hidden = !empty;
        }

        updateCursor(event) {
            if (!this.cursor) return;
            const visible = this.tool !== 'text' && !this.altDown && event && event.clientX != null;
            const useErase = this.tool === 'eraser' || this.altDown;
            if (!visible) {
                this.cursor.style.display = 'none';
                return;
            }
            const rect = this.stage.getBoundingClientRect();
            const diameter = Math.max(4, this.brushSize * this.view.scale);
            this.cursor.style.display = 'block';
            this.cursor.style.width = diameter + 'px';
            this.cursor.style.height = diameter + 'px';
            this.cursor.style.left = (event.clientX - rect.left) + 'px';
            this.cursor.style.top = (event.clientY - rect.top) + 'px';
            this.cursor.classList.toggle('is-erase', useErase);
        }

        // ---- 渲染 ----
        render() {
            if (!this.paintCtx || !this.editWidth) return;
            const ctx = this.paintCtx;
            ctx.clearRect(0, 0, this.editWidth, this.editHeight);
            if (this.baseline) ctx.drawImage(this.baseline, 0, 0, this.editWidth, this.editHeight);
            this.textItems = this.replayInto(ctx);
            if (this.draft) this.drawOp(ctx, this.draft);
            this.drawSelection();
            this.updateEmptyHint();
            this.updateHistory();
        }

        replayInto(ctx) {
            const items = new Map();
            for (let i = 0; i < this.ops.length; i += 1) {
                const op = this.ops[i];
                if (op.kind === 'clear') {
                    ctx.clearRect(0, 0, this.editWidth, this.editHeight);
                    items.clear();
                    continue;
                }
                if (op.kind === 'text') {
                    items.set(op.id, { id: op.id, text: op.text, x: op.x, y: op.y, size: op.size, color: op.color });
                    continue;
                }
                if (op.kind === 'text-delete') {
                    items.delete(op.id);
                    continue;
                }
                this.drawOp(ctx, op);
            }
            items.forEach((item) => {
                if (this.hiddenTextIds.has(item.id)) return;
                this.drawTextItem(ctx, item);
            });
            return items;
        }

        drawOp(ctx, op) {
            if (!op) return;
            if (op.kind === 'stroke') this.drawStroke(ctx, op);
            else if (op.kind === 'rect' || op.kind === 'ellipse') this.drawShape(ctx, op);
            else if (op.kind === 'label') this.drawTextItem(ctx, { text: op.text, x: op.x, y: op.y, size: op.size, color: op.color });
            else if (op.kind === 'text') this.drawTextItem(ctx, op);
        }

        drawStroke(ctx, op) {
            const points = op.points || [];
            if (!points.length) return;
            ctx.save();
            if (op.erase) {
                ctx.globalCompositeOperation = 'destination-out';
                ctx.strokeStyle = '#000';
                ctx.fillStyle = '#000';
            } else {
                ctx.strokeStyle = op.color;
                ctx.fillStyle = op.color;
                // 半透明笔画整条一次性描边（单条 path 的 round 接头不重复加深）；
                // 旧记录没有 opacity 字段时视为完全不透明。
                const opacity = clamp(Number(op.opacity) || DEFAULT_OPACITY, MIN_OPACITY, MAX_OPACITY);
                if (opacity < MAX_OPACITY) ctx.globalAlpha = opacity / 100;
            }
            ctx.lineWidth = op.size;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            if (points.length === 1) {
                ctx.beginPath();
                ctx.arc(points[0].x, points[0].y, Math.max(0.6, op.size / 2), 0, Math.PI * 2);
                ctx.fill();
            } else {
                ctx.beginPath();
                ctx.moveTo(points[0].x, points[0].y);
                for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i].x, points[i].y);
                ctx.stroke();
            }
            ctx.restore();
        }

        drawShape(ctx, op) {
            const x = Math.min(op.x, op.x + op.w);
            const y = Math.min(op.y, op.y + op.h);
            const w = Math.abs(op.w);
            const h = Math.abs(op.h);
            if (w < 1 || h < 1) return;
            ctx.save();
            ctx.strokeStyle = op.color;
            ctx.lineWidth = op.size;
            ctx.lineJoin = 'round';
            ctx.beginPath();
            if (op.kind === 'rect') ctx.rect(x, y, w, h);
            else ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.restore();
        }

        measureTextItem(ctx, item) {
            const size = clamp(item.size, MIN_TEXT, MAX_TEXT);
            const lines = textLines(item);
            const lineHeight = size * 1.25;
            ctx.save();
            ctx.font = textFont(size);
            let width = 0;
            for (let i = 0; i < lines.length; i += 1) {
                width = Math.max(width, ctx.measureText(lines[i] || ' ').width);
            }
            ctx.restore();
            return { width: Math.max(width, size * 0.6), height: Math.max(lineHeight, lines.length * lineHeight), lineHeight: lineHeight, lines: lines, size: size };
        }

        textBox(item) {
            const ctx = this.paintCtx;
            const metrics = this.measureTextItem(ctx, item);
            return {
                left: item.x - metrics.width / 2,
                top: item.y - metrics.height / 2,
                right: item.x + metrics.width / 2,
                bottom: item.y + metrics.height / 2,
                width: metrics.width,
                height: metrics.height,
                metrics: metrics
            };
        }

        drawTextItem(ctx, item) {
            const metrics = this.measureTextItem(ctx, item);
            const lines = metrics.lines;
            const size = metrics.size;
            ctx.save();
            ctx.font = textFont(size);
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.lineJoin = 'round';
            ctx.lineWidth = Math.max(2, size / 8);
            ctx.strokeStyle = STROKE_TINT;
            ctx.fillStyle = item.color;
            for (let i = 0; i < lines.length; i += 1) {
                const line = lines[i];
                if (!line) continue;
                const y = item.y + (i - (lines.length - 1) / 2) * metrics.lineHeight;
                ctx.strokeText(line, item.x, y);
                ctx.fillText(line, item.x, y);
            }
            ctx.restore();
        }

        // 裁切范围提示：整图坐标里的归一化矩形，映射到舞台后画虚线框。
        // 只提示、不裁剪——批注永远作用在整张原图上，保存后画布会按这个范围重新烘焙裁切结果。
        drawCropScope(ctx) {
            const rect = this.cropRect;
            if (!rect || !this.editWidth || !this.editHeight) return;
            const x = clamp(Number(rect.x) || 0, 0, 1);
            const y = clamp(Number(rect.y) || 0, 0, 1);
            const w = clamp(Number(rect.w) || 0, 0, 1 - x);
            const h = clamp(Number(rect.h) || 0, 0, 1 - y);
            if (w <= 0 || h <= 0) return;
            const tl = this.stagePoint(x * this.editWidth, y * this.editHeight);
            const br = this.stagePoint((x + w) * this.editWidth, (y + h) * this.editHeight);
            const width = Math.max(0, br.x - tl.x);
            const height = Math.max(0, br.y - tl.y);
            if (width < 2 || height < 2) return;
            ctx.save();
            ctx.strokeStyle = 'rgba(255,255,255,.92)';
            ctx.lineWidth = 1.5;
            ctx.setLineDash([7, 5]);
            ctx.beginPath();
            ctx.rect(tl.x, tl.y, width, height);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.restore();
        }

        drawSelection() {
            if (!this.marqueeCtx || !this.stageWidth) return;
            const ctx = this.marqueeCtx;
            ctx.clearRect(0, 0, this.stageWidth, this.stageHeight);
            this.drawCropScope(ctx);
            if (!this.selectedTextId) return;
            const item = this.textItems.get(this.selectedTextId);
            if (!item) return;
            const box = this.textBox(item);
            const tl = this.stagePoint(box.left, box.top);
            const tr = this.stagePoint(box.right, box.top);
            const br = this.stagePoint(box.right, box.bottom);
            const bl = this.stagePoint(box.left, box.bottom);
            ctx.save();
            ctx.strokeStyle = 'rgba(56,189,248,.92)';
            ctx.lineWidth = 1.5;
            ctx.setLineDash([5, 4]);
            ctx.beginPath();
            ctx.moveTo(tl.x, tl.y);
            ctx.lineTo(tr.x, tr.y);
            ctx.lineTo(br.x, br.y);
            ctx.lineTo(bl.x, bl.y);
            ctx.closePath();
            ctx.stroke();
            ctx.setLineDash([]);
            const corners = [tl, tr, br, bl];
            for (let i = 0; i < corners.length; i += 1) {
                ctx.beginPath();
                ctx.arc(corners[i].x, corners[i].y, 5.5, 0, Math.PI * 2);
                ctx.fillStyle = '#0a0c10';
                ctx.fill();
                ctx.strokeStyle = '#7dd3fc';
                ctx.lineWidth = 1.6;
                ctx.stroke();
            }
            const badge = this.deleteBadgePoint(item);
            ctx.beginPath();
            ctx.arc(badge.x, badge.y, 8, 0, Math.PI * 2);
            ctx.fillStyle = '#ef4444';
            ctx.fill();
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 1.6;
            ctx.beginPath();
            ctx.moveTo(badge.x - 3.2, badge.y - 3.2);
            ctx.lineTo(badge.x + 3.2, badge.y + 3.2);
            ctx.moveTo(badge.x + 3.2, badge.y - 3.2);
            ctx.lineTo(badge.x - 3.2, badge.y + 3.2);
            ctx.stroke();
            ctx.restore();
        }

        deleteBadgePoint(item) {
            const box = this.textBox(item);
            const point = this.stagePoint(box.right, box.top);
            return { x: point.x + 12, y: point.y - 12 };
        }

        textItemAt(point) {
            let hit = null;
            this.textItems.forEach((item) => {
                const box = this.textBox(item);
                if (point.x >= box.left && point.x <= box.right && point.y >= box.top && point.y <= box.bottom) {
                    hit = item;
                }
            });
            return hit;
        }

        textControlHit(point) {
            const item = this.textItems.get(this.selectedTextId);
            if (!item) return null;
            const box = this.textBox(item);
            const radius = HANDLE_HIT / Math.max(0.0001, this.view.scale);
            const corners = [
                { type: 'resize', handle: 'nw', x: box.left, y: box.top },
                { type: 'resize', handle: 'ne', x: box.right, y: box.top },
                { type: 'resize', handle: 'se', x: box.right, y: box.bottom },
                { type: 'resize', handle: 'sw', x: box.left, y: box.bottom }
            ];
            for (let i = 0; i < corners.length; i += 1) {
                if (Math.hypot(point.x - corners[i].x, point.y - corners[i].y) <= radius) return corners[i];
            }
            const badge = this.deleteBadgePoint(item);
            const pointer = this.stagePoint(point.x, point.y);
            if (Math.hypot(pointer.x - badge.x, pointer.y - badge.y) <= HANDLE_HIT + 2) {
                return { type: 'delete' };
            }
            if (point.x >= box.left && point.x <= box.right && point.y >= box.top && point.y <= box.bottom) {
                return { type: 'move' };
            }
            return null;
        }

        // ---- 指针 ----
        handlePointerDown(event) {
            if (this.closed || this.busy) return;
            const target = event.target;
            if (target && target.closest && target.closest('.me-panel, .me-error-box')) return;
            if (!this.editWidth) return;
            if (event.button === 2) {
                event.preventDefault();
                this.rightDrag = { startX: event.clientX, startSize: this.brushSize };
                this.capturePointer(event);
                return;
            }
            if (event.button === 1 || (event.button === 0 && this.spaceDown)) {
                event.preventDefault();
                this.pan = { startX: event.clientX, startY: event.clientY, originX: this.view.x, originY: this.view.y };
                this.stage.classList.add('is-panning');
                this.capturePointer(event);
                return;
            }
            if (event.button !== 0) return;
            const point = this.imagePos(event);
            if (!point) return;

            if (this.selectedTextId) {
                const control = this.textControlHit(point);
                if (control) {
                    if (control.type === 'delete') {
                        this.deleteSelectedText();
                        return;
                    }
                    if (control.type === 'resize') {
                        this.startTextResize(event, point);
                        return;
                    }
                    if (control.type === 'move') {
                        this.startTextMove(event, point);
                        return;
                    }
                }
                const hitSelected = this.textItemAt(point);
                if (this.tool !== 'text' || !hitSelected) {
                    this.selectedTextId = '';
                    this.render();
                    return;
                }
            }

            if (this.tool === 'text') {
                const hit = this.textItemAt(point);
                if (hit) {
                    this.selectedTextId = hit.id;
                    this.startTextMove(event, point, hit);
                    return;
                }
                if (Date.now() < this.suppressTextCreateAt) return;
                this.createTextAt(point);
                return;
            }

            if (this.tool === 'label') {
                const size = Math.max(18, this.brushSize * 2.2);
                this.pushOp({ kind: 'label', text: circledNumber(this.labelCounter++), color: this.colorValue(), size: size, x: point.x, y: point.y });
                this.render();
                return;
            }

            this.capturePointer(event);
            if (this.tool === 'rect' || this.tool === 'ellipse') {
                this.draft = { kind: this.tool, color: this.colorValue(), size: this.brushSize, x: point.x, y: point.y, w: 0, h: 0 };
                this.render();
                return;
            }
            const erase = this.tool === 'eraser' || this.altDown;
            this.strokeOp = {
                kind: 'stroke',
                color: erase ? '#000000' : this.colorValue(),
                size: this.brushSize,
                opacity: erase ? MAX_OPACITY : this.brushOpacity,
                erase: erase,
                points: [{ x: point.x, y: point.y }]
            };
            this.drawStroke(this.paintCtx, this.strokeOp);
        }

        handlePointerMove(event) {
            if (this.closed) return;
            if (this.rightDrag) {
                const dx = event.clientX - this.rightDrag.startX;
                if (Math.abs(dx) >= 4) this.setBrushSize(this.rightDrag.startSize + dx / 2);
                return;
            }
            if (this.pan) {
                this.view.x = this.pan.originX + (event.clientX - this.pan.startX);
                this.view.y = this.pan.originY + (event.clientY - this.pan.startY);
                this.userAdjusted = true;
                this.applyTransform();
                return;
            }
            if (this.textDrag) {
                const point = this.imagePos(event);
                if (point) this.updateTextDrag(point);
                return;
            }
            if (this.draft) {
                const point = this.imagePos(event);
                if (point) {
                    this.draft.w = point.x - this.draft.x;
                    this.draft.h = point.y - this.draft.y;
                    this.render();
                }
                return;
            }
            if (this.strokeOp) {
                const point = this.imagePos(event);
                if (!point) return;
                const points = this.strokeOp.points;
                const last = points[points.length - 1];
                if (Math.hypot(point.x - last.x, point.y - last.y) < 1) return;
                points.push(point);
                const ctx = this.paintCtx;
                ctx.save();
                if (this.strokeOp.erase) {
                    ctx.globalCompositeOperation = 'destination-out';
                    ctx.strokeStyle = '#000';
                } else {
                    ctx.strokeStyle = this.strokeOp.color;
                    // 实时逐段描边在接头处会略深于整条描边，松手 render 重放整条后归一。
                    const opacity = clamp(Number(this.strokeOp.opacity) || DEFAULT_OPACITY, MIN_OPACITY, MAX_OPACITY);
                    if (opacity < MAX_OPACITY) ctx.globalAlpha = opacity / 100;
                }
                ctx.lineWidth = this.strokeOp.size;
                ctx.lineCap = 'round';
                ctx.lineJoin = 'round';
                ctx.beginPath();
                ctx.moveTo(last.x, last.y);
                ctx.lineTo(point.x, point.y);
                ctx.stroke();
                ctx.restore();
                return;
            }
            this.updateCursor(event);
        }

        handlePointerUp(event) {
            if (this.closed) return;
            if (this.pan) {
                this.pan = null;
                if (this.stage) this.stage.classList.remove('is-panning');
                this.releasePointer(event);
                return;
            }
            if (this.rightDrag) {
                this.rightDrag = null;
                this.releasePointer(event);
                return;
            }
            if (this.textDrag) {
                const item = this.textDrag.item;
                const changed = this.textDrag.changed;
                this.textDrag = null;
                this.releasePointer(event);
                if (changed) {
                    if (!this.lastTextOp(item.id)) {
                        this.pushOp({ kind: 'text', id: item.id, text: item.text, x: item.x, y: item.y, size: item.size, color: item.color });
                    } else {
                        this.redoOps = [];
                        this.updateHistory();
                    }
                    this.render();
                }
                return;
            }
            if (this.draft) {
                const draft = this.draft;
                this.draft = null;
                this.releasePointer(event);
                if (Math.abs(draft.w) >= 2 && Math.abs(draft.h) >= 2) {
                    this.pushOp(draft);
                }
                this.render();
                return;
            }
            if (this.strokeOp) {
                const op = this.strokeOp;
                this.strokeOp = null;
                this.releasePointer(event);
                this.pushOp(op);
                this.render();
            }
        }

        handleDoubleClick(event) {
            if (this.closed || this.busy) return;
            if (this.textEditorEl) return;
            const point = this.imagePos(event);
            if (!point) return;
            const hit = this.textItemAt(point);
            if (hit) {
                event.preventDefault();
                this.selectedTextId = hit.id;
                this.startTextEditing(hit, false);
            }
        }

        capturePointer(event) {
            this.pointerId = event.pointerId;
            try {
                this.stage.setPointerCapture(event.pointerId);
            } catch (error) { /* 忽略不支持的浏览器 */ }
        }

        releasePointer(event) {
            if (this.pointerId == null) return;
            try {
                this.stage.releasePointerCapture(this.pointerId);
            } catch (error) { /* 忽略 */ }
            this.pointerId = null;
        }

        // ---- 文字 ----
        createTextAt(point) {
            const id = 'text-' + Date.now().toString(36) + '-' + Math.floor(Math.random() * 1e4).toString(36);
            const item = {
                id: id,
                text: '',
                x: point.x,
                y: point.y,
                size: clamp(Math.round(this.brushSize * 2), 14, 120),
                color: this.colorValue()
            };
            this.startTextEditing(item, true);
        }

        startTextEditing(item, isNew) {
            this.commitTextEditing();
            this.editingId = item.id;
            this.editingIsNew = !!isNew;
            this.editingItem = { id: item.id, text: item.text || '', x: item.x, y: item.y, size: item.size, color: item.color };
            this.hiddenTextIds.add(item.id);
            this.render();

            const el = document.createElement('div');
            el.className = 'ae-text-editor';
            el.contentEditable = 'true';
            el.spellcheck = false;
            el.dataset.placeholder = this.label('textPlaceholder');
            el.textContent = isNew ? '' : (item.text || '');
            el.style.left = item.x + 'px';
            el.style.top = item.y + 'px';
            el.style.fontSize = item.size + 'px';
            el.style.color = item.color;
            el.style.webkitTextStroke = Math.max(1, item.size / 8) + 'px rgba(255,255,255,.92)';
            this.viewport.appendChild(el);
            this.textEditorEl = el;

            const session = this;
            el.addEventListener('keydown', function (event) {
                event.stopPropagation();
                if (event.key === 'Escape') {
                    event.preventDefault();
                    session.cancelTextEditing();
                } else if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    session.commitTextEditing();
                }
            });
            el.addEventListener('pointerdown', function (event) { event.stopPropagation(); });
            el.addEventListener('blur', function () {
                if (session.textEditorEl === el) session.commitTextEditing();
            });
            requestAnimationFrame(function () {
                el.focus({ preventScroll: true });
                try {
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    range.collapse(false);
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                } catch (error) { /* 忽略 */ }
            });
        }

        commitTextEditing() {
            const el = this.textEditorEl;
            if (!el) return;
            const id = this.editingId;
            const isNew = this.editingIsNew;
            const base = this.editingItem || {};
            const raw = String(el.textContent || '').replace(/\u00a0/g, ' ');
            const text = raw.replace(/^\s+|\s+$/g, '');
            this.textEditorEl = null;
            this.editingId = '';
            this.editingIsNew = false;
            this.editingItem = null;
            if (id) this.hiddenTextIds.delete(id);
            if (el.parentNode) el.parentNode.removeChild(el);

            if (!id) {
                this.render();
                return;
            }
            if (!text) {
                if (!isNew) {
                    this.pushOp({ kind: 'text-delete', id: id });
                    if (this.selectedTextId === id) this.selectedTextId = '';
                }
                this.render();
                return;
            }
            const item = { id: id, text: text, x: base.x, y: base.y, size: base.size, color: base.color };
            this.pushOp({ kind: 'text', id: item.id, text: item.text, x: item.x, y: item.y, size: item.size, color: item.color });
            this.selectedTextId = id;
            this.suppressTextCreateAt = Date.now() + 250;
            this.render();
        }

        cancelTextEditing() {
            const el = this.textEditorEl;
            if (!el) return;
            this.textEditorEl = null;
            if (this.editingId) this.hiddenTextIds.delete(this.editingId);
            this.editingId = '';
            this.editingIsNew = false;
            this.editingItem = null;
            if (el.parentNode) el.parentNode.removeChild(el);
            this.render();
        }

        deleteSelectedText() {
            if (!this.selectedTextId) return;
            this.pushOp({ kind: 'text-delete', id: this.selectedTextId });
            this.selectedTextId = '';
            this.render();
        }

        startTextMove(event, point) {
            const item = this.textItems.get(this.selectedTextId);
            if (!item) return;
            this.capturePointer(event);
            this.textDrag = {
                mode: 'move',
                item: { id: item.id, text: item.text, x: item.x, y: item.y, size: item.size, color: item.color },
                origin: { x: item.x, y: item.y },
                start: point,
                changed: false
            };
        }

        startTextResize(event, point) {
            const item = this.textItems.get(this.selectedTextId);
            if (!item) return;
            this.capturePointer(event);
            this.textDrag = {
                mode: 'resize',
                item: { id: item.id, text: item.text, x: item.x, y: item.y, size: item.size, color: item.color },
                origin: { size: item.size },
                center: { x: item.x, y: item.y },
                startDistance: Math.max(1, Math.hypot(point.x - item.x, point.y - item.y)),
                changed: false
            };
        }

        updateTextDrag(point) {
            const drag = this.textDrag;
            if (!drag) return;
            if (drag.mode === 'move') {
                drag.item.x = drag.origin.x + (point.x - drag.start.x);
                drag.item.y = drag.origin.y + (point.y - drag.start.y);
                drag.changed = true;
            } else {
                const distance = Math.max(1, Math.hypot(point.x - drag.center.x, point.y - drag.center.y));
                const next = clamp(Math.round(drag.origin.size * (distance / drag.startDistance)), MIN_TEXT, MAX_TEXT);
                if (next !== drag.item.size) {
                    drag.item.size = next;
                    this.setBrushSize(next);
                    drag.changed = true;
                }
            }
            // 拖动期间直接改最后一条文字操作，render() 才能从操作列表里读到新位置；
            // 抬手时不再追加重复操作，一次拖动只占一格撤销。
            const op = this.lastTextOp(drag.item.id);
            if (op) {
                op.x = drag.item.x;
                op.y = drag.item.y;
                op.size = drag.item.size;
            }
            this.render();
        }

        lastTextOp(id) {
            for (let i = this.ops.length - 1; i >= 0; i -= 1) {
                const op = this.ops[i];
                if (op.kind === 'text' && op.id === id) return op;
            }
            return null;
        }

        // ---- 历史 ----
        pushOp(op) {
            if (!op) return;
            this.ops.push(op);
            while (this.ops.length > MAX_OPS) this.ops.shift();
            this.redoOps = [];
            this.updateHistory();
        }

        undo() {
            if (!this.ops.length) return;
            this.redoOps.push(this.ops.pop());
            this.selectedTextId = '';
            this.render();
        }

        redo() {
            if (!this.redoOps.length) return;
            this.ops.push(this.redoOps.pop());
            this.selectedTextId = '';
            this.render();
        }

        clearAll() {
            if (!this.ops.length && !this.baseline) return;
            this.pushOp({ kind: 'clear' });
            this.selectedTextId = '';
            this.render();
        }

        // ---- 交互 ----
        handleClick(event) {
            const target = event.target;
            if (!target || !target.closest) return;
            const toolButton = target.closest('.me-tool[data-tool]');
            if (toolButton && this.root.contains(toolButton)) {
                this.setTool(toolButton.dataset.tool);
                return;
            }
            const swatch = target.closest('.me-swatch[data-color]');
            if (swatch && this.root.contains(swatch)) {
                this.setColor(swatch.dataset.color);
                return;
            }
            const actionButton = target.closest('button[data-act]');
            if (!actionButton || !this.root.contains(actionButton)) return;
            const act = actionButton.dataset.act;
            if (act === 'undo') this.undo();
            else if (act === 'redo') this.redo();
            else if (act === 'clear') this.clearAll();
            else if (act === 'cancel') this.close(false);
            else if (act === 'save') this.save();
            else if (act === 'fit') this.fitToStage();
            else if (act === 'zoom-in') this.zoomBy(1.25);
            else if (act === 'zoom-out') this.zoomBy(0.8);
        }

        handleKeyDown(event) {
            if (this.closed) return;
            if (this.isEditableTarget(event.target)) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.cancelTextEditing();
                }
                return;
            }
            const key = event.key;
            const lower = String(key || '').toLowerCase();
            const consumed = () => {
                event.preventDefault();
                event.stopPropagation();
                if (event.stopImmediatePropagation) event.stopImmediatePropagation();
            };

            if (key === 'Escape') {
                consumed();
                if (this.textEditorEl) this.cancelTextEditing();
                else if (this.selectedTextId) {
                    this.selectedTextId = '';
                    this.render();
                } else this.close(false);
                return;
            }
            if (this.busy) return;
            if (key === 'Alt') {
                this.altDown = true;
                if (this.cursor) this.cursor.classList.add('is-erase');
                return;
            }
            if ((event.ctrlKey || event.metaKey) && (lower === 'z' || lower === 'y')) {
                consumed();
                if (lower === 'z' && !event.shiftKey) this.undo();
                else this.redo();
                return;
            }
            if (event.ctrlKey || event.metaKey || event.altKey) {
                if (key === ' ') { this.spaceDown = true; consumed(); }
                return;
            }
            if (key === 'Enter') {
                consumed();
                this.save();
                return;
            }
            if (key === ' ') {
                this.spaceDown = true;
                consumed();
                return;
            }
            if (key === 'Delete' || key === 'Backspace') {
                if (this.selectedTextId) {
                    consumed();
                    this.deleteSelectedText();
                }
                return;
            }
            if (key === '[') { consumed(); this.adjustBrushSize(-1); return; }
            if (key === ']') { consumed(); this.adjustBrushSize(1); return; }
            if (lower === 'x') { consumed(); this.clearAll(); return; }
            const toolKeys = { b: 'brush', e: 'eraser', r: 'rect', o: 'ellipse', n: 'label', t: 'text' };
            if (toolKeys[lower]) {
                consumed();
                this.setTool(toolKeys[lower]);
            }
        }

        handleKeyUp(event) {
            if (event.key === ' ') this.spaceDown = false;
            if (event.key === 'Alt') {
                this.altDown = false;
                if (this.cursor) this.cursor.classList.toggle('is-erase', this.tool === 'eraser');
            }
        }

        isEditableTarget(target) {
            if (!target || !target.tagName) return false;
            const tag = target.tagName.toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
            return !!target.isContentEditable;
        }

        setBusy(busy) {
            this.busy = !!busy;
            if (this.root) this.root.classList.toggle('is-busy', this.busy);
            if (this.saveBtn) this.saveBtn.disabled = this.busy;
            if (this.saveText) this.saveText.textContent = this.busy ? this.label('applying') : this.label('save');
        }

        showToast(text) {
            if (!this.toastEl || !text) return;
            this.toastEl.textContent = text;
            this.toastEl.hidden = false;
            if (this.toastTimer) clearTimeout(this.toastTimer);
            const session = this;
            this.toastTimer = setTimeout(function () {
                if (session.toastEl) session.toastEl.hidden = true;
            }, 4200);
        }

        showError(text) {
            if (!this.errorBox) return;
            if (this.errorText) this.errorText.textContent = text || this.label('loadFail');
            this.errorBox.hidden = false;
        }

        hideError() {
            if (this.errorBox) this.errorBox.hidden = true;
        }

        // ---- 保存 ----
        async save() {
            if (this.busy || this.closed || !this.editWidth) return;
            this.commitTextEditing();
            this.setBusy(true);
            try {
                this.render();
                const result = await this.buildResult();
                if (!result || (!result.flattenBlob && !result.hasMarks)) throw new Error('empty result');
                if (this.onSave) await this.onSave(result);
                this.close(true);
            } catch (error) {
                this.setBusy(false);
                if (window.console && console.warn) console.warn('AnnotationEditor: 保存失败', error);
                this.showToast(this.label('saveFail'));
            }
        }

        async buildResult() {
            const scale = Math.min(1, MAX_EXPORT_LONG_SIDE / Math.max(this.naturalWidth, this.naturalHeight));
            const outWidth = Math.max(1, Math.round(this.naturalWidth * scale));
            const outHeight = Math.max(1, Math.round(this.naturalHeight * scale));
            const ratio = outWidth / Math.max(1, this.editWidth);

            const overlay = createCanvas(outWidth, outHeight);
            const overlayCtx = overlay.getContext('2d');
            overlayCtx.imageSmoothingEnabled = true;
            if (this.baseline) overlayCtx.drawImage(this.baseline, 0, 0, outWidth, outHeight);
            if (this.ops.length) {
                overlayCtx.save();
                overlayCtx.scale(ratio, ratio);
                this.replayInto(overlayCtx);
                overlayCtx.restore();
            }

            const flatten = createCanvas(outWidth, outHeight);
            const flattenCtx = flatten.getContext('2d');
            flattenCtx.imageSmoothingEnabled = true;
            flattenCtx.drawImage(this.imgEl, 0, 0, outWidth, outHeight);
            flattenCtx.drawImage(overlay, 0, 0);

            const hasMarks = this.canvasHasMarks(overlay);
            const flattenBlob = await canvasToBlob(flatten);
            const overlayBlob = hasMarks ? await canvasToBlob(overlay) : null;
            return {
                flattenBlob: flattenBlob,
                overlayBlob: overlayBlob,
                hasMarks: hasMarks,
                width: outWidth,
                height: outHeight
            };
        }

        canvasHasMarks(canvas) {
            if (!canvas) return false;
            const sample = createCanvas(96, 96);
            const ctx = sample.getContext('2d', { willReadFrequently: true });
            ctx.clearRect(0, 0, sample.width, sample.height);
            ctx.drawImage(canvas, 0, 0, sample.width, sample.height);
            let data = null;
            try {
                data = ctx.getImageData(0, 0, sample.width, sample.height).data;
            } catch (error) {
                return true;
            }
            for (let i = 3; i < data.length; i += 4) {
                if (data[i] > MARK_ALPHA) return true;
            }
            return false;
        }
    }

    // 单实例：同时只允许一个批注编辑器
    let activeSession = null;

    const AnnotationEditor = {
        open: function (opts) {
            if (!opts || !opts.imageUrl) {
                return Promise.reject(new Error('AnnotationEditor.open: imageUrl is required'));
            }
            if (activeSession) activeSession.close(false);
            const session = new AnnotationEditorSession(opts);
            activeSession = session;
            return session.start();
        }
    };

    window.AnnotationEditor = AnnotationEditor;
})();