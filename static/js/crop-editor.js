/*
 * 全屏裁切编辑器：与遮罩/批注编辑器（mask-editor.js、annotation-editor.js）同一套外壳与视觉语言。
 *
 * 对外只有一个入口：window.CropEditor.open(options) -> Promise<boolean>
 *   options = { imageUrl, rect, ratio, labels, onSave }
 *   result  = { rect:{x,y,w,h}, ratio, width, height, blob }
 *
 * 设计取舍：
 * - 舞台上只做 CSS 缩放（transform: scale），交互永远不碰原图像素；只有确认时才用自然像素
 *   画到 canvas 导出，所以 8000×8000 的图也能打开、能拖。
 * - 选框、手柄、三分线都挂在被缩放的 viewport 里，按 --ce-inv（1/scale）反向补偿尺寸，
 *   这样缩放舞台时不需要重排 DOM，手柄在屏幕上始终是同样大小。
 * - 拖拽只改 rect 数据 + 用 rAF 合并写样式，不在 mousemove 里重建节点。
 * - 文案全部来自 labels，模块内不写死任何语言。
 *
 * 依赖：static/css/mask-editor.css（外壳 .me- 样式）+ static/css/crop-editor.css（裁剪增量样式）。
 * 该文件只依赖原生 ES2019，可直接以 <script> 加载。
 */
(function () {
    'use strict';

    // ---- 常量 ----
    const MIN_SCALE = 0.1;
    const MAX_SCALE = 8;
    const FIT_PADDING = 56;           // 适配窗口时四周留白，让选框手柄不贴边
    const MIN_CROP_PIXELS = 16;       // 选框最小边（原图像素）
    const HANDLE_REACH = 12;          // 手柄命中半径（屏幕像素），比视觉尺寸大，方便抓
    const CLICK_SLOP = 3;             // 小于该屏幕位移视为点击，避免误缩成最小框
    const ROOT_Z_INDEX = '2147483000';
    const IMAGE_LOAD_TIMEOUT = 30000;
    const LOAD_FAIL_LINGER = 2200;    // 加载失败后停留展示的时长，然后 close(false)
    const CENTER_GRID = 8;            // 中心取样网格边长
    const SAMPLE_LONG_SIDE = 512;     // 亮度取样用的缩略图长边，与图片原始大小无关
    const TONE_WHITE = '#ffffff';
    const TONE_DARK = '#0b0f14';

    // 比例预设：'free' 自由，'source' 跟随原图。其余是显示用的 w:h。
    const RATIOS = ['free', 'source', '1:1', '2:3', '3:2', '3:4', '4:3',
        '9:16', '16:9', '21:9', '1:2', '2:1'];

    const SVG_OPEN = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" ' +
        'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';

    const ICONS = {
        check: SVG_OPEN + '<path d="M3 8.6 6.2 12 13 4.6"/></svg>',
        fit: SVG_OPEN + '<path d="M2.6 6V2.6H6M10 2.6h3.4V6M13.4 10v3.4H10M6 13.4H2.6V10"/></svg>',
        plus: SVG_OPEN + '<path d="M8 3.4v9.2M3.4 8h9.2"/></svg>',
        minus: SVG_OPEN + '<path d="M3.4 8h9.2"/></svg>'
    };

    // 中心辅助标记：按取样亮度切成黑/白，画在选框正中，方便确认裁切中心。
    const CENTER_MARK =
        '<svg class="ce-center" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="1.5" stroke-linecap="round" aria-hidden="true">' +
        '<circle cx="12" cy="12" r="5.2"/>' +
        '<path d="M12 1.6v4.4M12 18v4.4M1.6 12h4.4M18 12h4.4"/>' +
        '</svg>';

    const HANDLE_ORDER = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
    const HANDLE_CURSORS = {
        n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize',
        nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize'
    };

    // ---- 工具函数 ----
    function clamp(value, min, max) {
        const number = Number(value);
        if (!Number.isFinite(number)) return min;
        return Math.min(max, Math.max(min, number));
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function percent(value) {
        return (value * 100).toFixed(4) + '%';
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

    // 拖拽重画：以按下点为锚点向四个方向生长；alt 时以按下点为中心对称生长。
    // 越界修正放在 maxW/maxH 里做，避免先算出越界矩形再回退造成抖动。
    function drawRect(anchor, point, aspect, width, height, symmetric) {
        let w = Math.abs(point.x - anchor.x);
        let h = Math.abs(point.y - anchor.y);
        const sx = point.x < anchor.x ? -1 : 1;
        const sy = point.y < anchor.y ? -1 : 1;
        const maxW = symmetric ? Math.min(anchor.x, width - anchor.x)
            : (sx > 0 ? width - anchor.x : anchor.x);
        const maxH = symmetric ? Math.min(anchor.y, height - anchor.y)
            : (sy > 0 ? height - anchor.y : anchor.y);
        if (aspect) {
            // 先按两条边各自需要的宽度取大者，再同时受两个方向的剩余空间约束
            w = Math.min(Math.max(Math.max(w, 0), Math.max(h, 0) * aspect), maxW, maxH * aspect);
            h = w / aspect;
        } else {
            w = Math.min(Math.max(w, 0), maxW);
            h = Math.min(Math.max(h, 0), maxH);
        }
        if (symmetric) return { x: anchor.x - w, y: anchor.y - h, w: w * 2, h: h * 2 };
        return {
            x: sx < 0 ? anchor.x - w : anchor.x,
            y: sy < 0 ? anchor.y - h : anchor.y,
            w: w,
            h: h
        };
    }

    // 手柄缩放：锚点是被拖手柄的对边（角手柄时是斜对角），比例锁定时只认锚点算一条边，
    // 另一条边由 aspect 推导，因此任何拖拽都不会破坏比例。
    function resizeRect(rect, handle, point, aspect, width, height, symmetric) {
        const left = rect.x;
        const top = rect.y;
        const right = left + rect.w;
        const bottom = top + rect.h;
        const horizontal = /[we]/.test(handle);
        const vertical = /[ns]/.test(handle);

        if (symmetric) {
            const cx = (left + right) / 2;
            const cy = (top + bottom) / 2;
            const maxW = Math.min(cx, width - cx);
            const maxH = Math.min(cy, height - cy);
            let halfW = horizontal ? Math.abs(point.x - cx) : rect.w / 2;
            let halfH = vertical ? Math.abs(point.y - cy) : rect.h / 2;
            if (aspect) {
                halfW = horizontal ? (vertical ? Math.max(halfW, halfH * aspect) : halfW) : halfH * aspect;
                halfW = Math.min(halfW, maxW, maxH * aspect);
                halfH = halfW / aspect;
            } else {
                halfW = Math.min(halfW, maxW);
                halfH = Math.min(halfH, maxH);
            }
            return {
                x: cx - Math.max(halfW, 0),
                y: cy - Math.max(halfH, 0),
                w: Math.max(halfW * 2, 0),
                h: Math.max(halfH * 2, 0)
            };
        }

        if (!aspect) {
            let nextLeft = left;
            let nextTop = top;
            let nextRight = right;
            let nextBottom = bottom;
            if (handle.indexOf('w') >= 0) nextLeft = clamp(point.x, 0, nextRight);
            if (handle.indexOf('e') >= 0) nextRight = clamp(point.x, nextLeft, width);
            if (handle.indexOf('n') >= 0) nextTop = clamp(point.y, 0, nextBottom);
            if (handle.indexOf('s') >= 0) nextBottom = clamp(point.y, nextTop, height);
            return {
                x: nextLeft,
                y: nextTop,
                w: Math.max(nextRight - nextLeft, 0),
                h: Math.max(nextBottom - nextTop, 0)
            };
        }

        const anchorX = handle.indexOf('w') >= 0 ? right : (horizontal ? left : (left + right) / 2);
        const anchorY = handle.indexOf('n') >= 0 ? bottom : (vertical ? top : (top + bottom) / 2);
        const signX = handle.indexOf('w') >= 0 ? -1 : 1;
        const signY = handle.indexOf('n') >= 0 ? -1 : 1;
        let w = horizontal
            ? signX * (point.x - anchorX)
            : signY * (point.y - anchorY) * aspect;
        if (horizontal && vertical) {
            w = Math.max(w, signY * (point.y - anchorY) * aspect);
        }
        // 单边手柄的另一侧要相对选框中心对称扩张，所以最大宽度由中心到图边的距离决定
        const maxW = horizontal
            ? (signX > 0 ? width - anchorX : anchorX)
            : 2 * Math.min(anchorX, width - anchorX);
        const maxH = vertical
            ? (signY > 0 ? height - anchorY : anchorY)
            : 2 * Math.min(anchorY, height - anchorY);
        w = Math.max(0, Math.min(w, maxW, maxH * aspect));
        const h = aspect ? w / aspect : 0;
        return {
            x: horizontal ? (signX < 0 ? anchorX - w : anchorX) : anchorX - w / 2,
            y: vertical ? (signY < 0 ? anchorY - h : anchorY) : anchorY - h / 2,
            w: w,
            h: h
        };
    }

    // ---- 编辑器会话 ----
    class CropEditorSession {
        constructor(opts) {
            this.opts = opts || {};
            this.labels = this.opts.labels || {};
            this.imageUrl = this.opts.imageUrl || '';
            this.initialRect = this.opts.rect || null;
            this.ratioName = RATIOS.indexOf(this.opts.ratio) >= 0 ? this.opts.ratio : 'free';
            this.onSave = typeof this.opts.onSave === 'function' ? this.opts.onSave : null;

            this.root = null;
            this.stage = null;
            this.viewport = null;
            this.imgEl = null;
            this.masks = null;
            this.selection = null;
            this.centerEl = null;
            this.sizeValue = null;
            this.zoomValue = null;
            this.toastEl = null;
            this.errorBox = null;
            this.errorText = null;
            this.applyBtn = null;
            this.applyText = null;

            this.natW = 0;
            this.natH = 0;
            this.rect = null;
            this.view = { x: 0, y: 0, scale: 1 };
            this.stageW = 0;
            this.stageH = 0;
            this.userAdjusted = false;

            this.drag = null;
            this.pan = null;
            this.spaceDown = false;

            this.frame = 0;
            this.toneKey = '';
            this.sampleSource = null;
            this.sampleCanvas = null;
            this.sampleCtx = null;

            this.ready = false;
            this.busy = false;
            this.closed = false;
            this.toastTimer = 0;
            this.loadTimer = 0;
            this.pointerId = null;
            this.scrollLock = null;
            this.bound = {};

            const session = this;
            this.promise = new Promise(function (resolve) {
                session.resolve = resolve;
            });
        }

        label(key) {
            const value = this.labels ? this.labels[key] : '';
            return value == null ? '' : String(value);
        }

        // 比例按钮上的文字：比例值本身（'16:9'）是语言无关的，缺失词条时回退到它，
        // 避免出现一排点不到内容的空按钮。
        ratioText(name) {
            if (name === 'free') return this.label('ratioFree') || name;
            if (name === 'source') return this.label('ratioSource') || name;
            const map = this.labels ? this.labels.ratioLabels : null;
            const text = map ? map[name] : null;
            return text == null ? name : String(text);
        }

        // ---- 生命周期 ----
        async start() {
            this.buildDom();
            this.bindEvents();
            this.lockScroll();
            this.syncRatioButtons();
            try {
                await this.loadImage();
                this.applyInitialRect();
                this.buildSampleSource();
                this.handleResize();
                this.fitToStage();
                this.ready = true;
                this.root.classList.add('is-ready');
                this.scheduleRender();
                try { this.root.focus({ preventScroll: true }); } catch (error) { /* 忽略 */ }
            } catch (error) {
                this.failLoad(error);
            }
            return this.promise;
        }

        close(saved) {
            if (this.closed) return;
            this.closed = true;
            this.unlockScroll();
            if (this.frame) {
                cancelAnimationFrame(this.frame);
                this.frame = 0;
            }
            if (this.toastTimer) {
                clearTimeout(this.toastTimer);
                this.toastTimer = 0;
            }
            if (this.loadTimer) {
                clearTimeout(this.loadTimer);
                this.loadTimer = 0;
            }

            const bound = this.bound || {};
            document.removeEventListener('keydown', bound.keydown, true);
            document.removeEventListener('keyup', bound.keyup, true);
            window.removeEventListener('resize', bound.resize);
            window.removeEventListener('blur', bound.blur);
            if (this.stage) {
                this.stage.removeEventListener('wheel', bound.wheel);
                this.stage.removeEventListener('pointerdown', bound.pointerDown);
                this.stage.removeEventListener('pointermove', bound.pointerMove);
                this.stage.removeEventListener('pointerup', bound.pointerUp);
                this.stage.removeEventListener('pointercancel', bound.pointerUp);
                this.stage.removeEventListener('dblclick', bound.dblclick);
            }

            if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
            this.root = null;
            this.imgEl = null;
            this.masks = null;
            this.selection = null;
            this.sampleSource = null;
            this.sampleCtx = null;
            this.resolve(!!saved);
        }

        // 编辑器打开期间锁住页面滚动；关掉时精确还原，避免覆盖调用方原本的设置。
        lockScroll() {
            const body = document.body;
            const html = document.documentElement;
            if (!body || !html) return;
            this.scrollLock = {
                body: body.style.overflow,
                html: html.style.overflow
            };
            body.style.overflow = 'hidden';
            html.style.overflow = 'hidden';
        }

        unlockScroll() {
            if (!this.scrollLock) return;
            if (document.body) document.body.style.overflow = this.scrollLock.body;
            if (document.documentElement) document.documentElement.style.overflow = this.scrollLock.html;
            this.scrollLock = null;
        }

        // ---- DOM ----
        buildDom() {
            const session = this;
            const root = document.createElement('div');
            root.className = 'me-root ce-root';
            root.tabIndex = -1;
            root.setAttribute('role', 'dialog');
            root.setAttribute('aria-modal', 'true');
            root.setAttribute('aria-label', this.label('title'));
            root.style.zIndex = ROOT_Z_INDEX;

            const ratios = RATIOS.map(function (name) {
                const text = session.ratioText(name);
                return '<button type="button" class="me-seg-btn ce-ratio" data-ratio="' + name + '" ' +
                    'aria-pressed="false" title="' + escapeHtml(text) + '">' +
                    escapeHtml(text) + '</button>';
            }).join('');

            const handles = HANDLE_ORDER.map(function (name) {
                return '<span class="ce-handle ce-h-' + name + '" data-handle="' + name + '"></span>';
            }).join('');

            root.innerHTML =
                '<div class="me-toolbar">' +
                    '<div class="me-title">' + escapeHtml(this.label('title')) + '</div>' +
                    '<div class="ce-ratios" role="group" aria-label="' +
                        escapeHtml(this.label('ratioLabel')) + '">' +
                        '<span class="ce-ratios-label">' + escapeHtml(this.label('ratioLabel')) + '</span>' +
                        ratios +
                    '</div>' +
                    '<div class="me-actions">' +
                        '<button type="button" class="me-icon-btn" data-act="fit" title="' +
                            escapeHtml(this.label('fit')) + '">' + ICONS.fit + '</button>' +
                        '<span class="me-divider" aria-hidden="true"></span>' +
                        '<button type="button" class="me-icon-btn" data-act="zoom-out" title="' +
                            escapeHtml(this.label('zoomOut')) + '">' + ICONS.minus + '</button>' +
                        '<span class="ce-zoom-value">100%</span>' +
                        '<button type="button" class="me-icon-btn" data-act="zoom-in" title="' +
                            escapeHtml(this.label('zoomIn')) + '">' + ICONS.plus + '</button>' +
                    '</div>' +
                '</div>' +
                '<div class="me-main">' +
                    '<div class="me-stage">' +
                        '<div class="me-viewport">' +
                            '<img class="me-image" alt="" draggable="false">' +
                            '<div class="ce-overlay">' +
                                '<div class="ce-masks">' +
                                    '<span class="ce-mask ce-mask-t"></span>' +
                                    '<span class="ce-mask ce-mask-b"></span>' +
                                    '<span class="ce-mask ce-mask-l"></span>' +
                                    '<span class="ce-mask ce-mask-r"></span>' +
                                '</div>' +
                                '<div class="ce-selection">' +
                                    '<span class="ce-third ce-third-v" style="left:33.3333%"></span>' +
                                    '<span class="ce-third ce-third-v" style="left:66.6667%"></span>' +
                                    '<span class="ce-third ce-third-h" style="top:33.3333%"></span>' +
                                    '<span class="ce-third ce-third-h" style="top:66.6667%"></span>' +
                                    CENTER_MARK +
                                    handles +
                                '</div>' +
                            '</div>' +
                        '</div>' +
                        '<div class="me-error-box" hidden>' +
                            '<div class="me-error-text"></div>' +
                            '<button type="button" class="me-text-btn me-error-close" data-act="cancel">' +
                                escapeHtml(this.label('cancel')) +
                            '</button>' +
                        '</div>' +
                        '<div class="me-toast" hidden></div>' +
                    '</div>' +
                '</div>' +
                '<div class="me-footer ce-footer">' +
                    '<span class="me-footer-hint">' + escapeHtml(this.label('hint')) + '</span>' +
                    '<span class="me-footer-shortcut">' + escapeHtml(this.label('shortcutHint')) + '</span>' +
                    '<span class="ce-size">' +
                        '<span class="ce-size-label">' + escapeHtml(this.label('sizeText')) + '</span>' +
                        '<span class="ce-size-value"></span>' +
                    '</span>' +
                    '<button type="button" class="me-text-btn" data-act="clear">' +
                        escapeHtml(this.label('clear')) + '</button>' +
                    '<button type="button" class="me-text-btn" data-act="cancel" title="Esc">' +
                        escapeHtml(this.label('cancel')) + '</button>' +
                    '<button type="button" class="me-primary-btn" data-act="apply" title="Enter">' +
                        ICONS.check + '<span class="me-save-text">' +
                        escapeHtml(this.label('apply')) + '</span></button>' +
                '</div>';

            document.body.appendChild(root);
            this.root = root;
            this.stage = root.querySelector('.me-stage');
            this.viewport = root.querySelector('.me-viewport');
            this.imgEl = root.querySelector('.me-image');
            this.masks = {
                top: root.querySelector('.ce-mask-t'),
                bottom: root.querySelector('.ce-mask-b'),
                left: root.querySelector('.ce-mask-l'),
                right: root.querySelector('.ce-mask-r')
            };
            this.selection = root.querySelector('.ce-selection');
            this.centerEl = root.querySelector('.ce-center');
            this.sizeValue = root.querySelector('.ce-size-value');
            this.zoomValue = root.querySelector('.ce-zoom-value');
            this.toastEl = root.querySelector('.me-toast');
            this.errorBox = root.querySelector('.me-error-box');
            this.errorText = root.querySelector('.me-error-text');
            this.applyBtn = root.querySelector('[data-act="apply"]');
            this.applyText = root.querySelector('.me-save-text');
        }

        bindEvents() {
            const session = this;

            this.bound = {
                click: function (event) { session.handleClick(event); },
                keydown: function (event) { session.handleKeyDown(event); },
                keyup: function (event) { session.handleKeyUp(event); },
                resize: function () { session.handleResize(); },
                blur: function () { session.handleWindowBlur(); },
                wheel: function (event) { session.handleWheel(event); },
                pointerDown: function (event) { session.handlePointerDown(event); },
                pointerMove: function (event) { session.handlePointerMove(event); },
                pointerUp: function (event) { session.handlePointerUp(event); },
                dblclick: function (event) { session.handleDoubleClick(event); },
                contextMenu: function (event) { event.preventDefault(); },
                dragStart: function (event) { event.preventDefault(); }
            };

            this.root.addEventListener('click', this.bound.click);
            this.root.addEventListener('contextmenu', this.bound.contextMenu);
            this.root.addEventListener('dragstart', this.bound.dragStart);

            this.stage.addEventListener('wheel', this.bound.wheel, { passive: false });
            this.stage.addEventListener('pointerdown', this.bound.pointerDown);
            this.stage.addEventListener('pointermove', this.bound.pointerMove);
            this.stage.addEventListener('pointerup', this.bound.pointerUp);
            this.stage.addEventListener('pointercancel', this.bound.pointerUp);
            this.stage.addEventListener('dblclick', this.bound.dblclick);

            // 键盘挂在 document 捕获阶段：焦点可能落在根部或工具栏按钮上，都要能收到
            document.addEventListener('keydown', this.bound.keydown, true);
            document.addEventListener('keyup', this.bound.keyup, true);
            window.addEventListener('resize', this.bound.resize);
            window.addEventListener('blur', this.bound.blur);
        }

        // ---- 载入 ----
        async loadImage() {
            await waitForImage(this.imgEl, this.imageUrl);
            this.natW = this.imgEl.naturalWidth || this.imgEl.width || 1;
            this.natH = this.imgEl.naturalHeight || this.imgEl.height || 1;
            // 舞台用自然尺寸 + transform 缩放；这样指针换算只需对 img 的包围盒做一次比例运算
            this.imgEl.style.width = this.natW + 'px';
            this.imgEl.style.height = this.natH + 'px';
            this.viewport.style.width = this.natW + 'px';
            this.viewport.style.height = this.natH + 'px';
        }

        // 亮度取样用的预缩小副本：拖拽时每帧都可能重新取色，直接从原图采样会让大图卡顿。
        buildSampleSource() {
            const longSide = Math.max(this.natW, this.natH);
            if (!longSide) return;
            const scale = Math.min(1, SAMPLE_LONG_SIDE / longSide);
            try {
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round(this.natW * scale));
                canvas.height = Math.max(1, Math.round(this.natH * scale));
                const ctx = canvas.getContext('2d', { willReadFrequently: true });
                ctx.drawImage(this.imgEl, 0, 0, canvas.width, canvas.height);
                this.sampleSource = canvas;
            } catch (error) {
                this.sampleSource = null;
            }
            this.sampleCanvas = document.createElement('canvas');
            this.sampleCanvas.width = CENTER_GRID;
            this.sampleCanvas.height = CENTER_GRID;
            this.sampleCtx = this.sampleCanvas.getContext('2d', { willReadFrequently: true });
            this.toneKey = '';
        }

        applyInitialRect() {
            const width = this.natW;
            const height = this.natH;
            const source = this.initialRect;
            let rect = null;
            if (source) {
                // 传进来的归一化选框可能来自旧数据：先用 0–1 夹紧，再决定是否可用
                const rx = clamp(Number(source.x) || 0, 0, 1);
                const ry = clamp(Number(source.y) || 0, 0, 1);
                const rw = clamp(Number(source.w) || 0, 0, 1 - rx);
                const rh = clamp(Number(source.h) || 0, 0, 1 - ry);
                if (rw > 0 && rh > 0) {
                    rect = {
                        x: rx * width,
                        y: ry * height,
                        w: Math.max(1, rw * width),
                        h: Math.max(1, rh * height)
                    };
                }
            }
            this.rect = rect || { x: 0, y: 0, w: width, h: height };
            this.clampRect();
            // 调用方可能同时给 rect 和 ratio，两者冲突时以 ratio 为准：否则会出现
            // 「按钮亮着 1:1、选框却是整张图」的错位，直到用户拖一次才纠正。
            const aspect = this.aspectOf(this.ratioName);
            if (aspect) this.fitRectToAspect(aspect);
        }

        // 把选框收成指定比例：以当前选框中心为基准，只缩小不放大，
        // 因此既不会跳出一个合法初始选框的范围，也能保留调用方的位置意图。
        fitRectToAspect(aspect) {
            const rect = this.rect;
            if (!rect || !aspect || !(aspect > 0)) return;
            const width = this.natW;
            const height = this.natH;
            const cx = rect.x + rect.w / 2;
            const cy = rect.y + rect.h / 2;
            let w = Math.min(rect.w, rect.h * aspect);
            let h = w / aspect;
            // 极端比例（21:9 遇上矮选框）可能算出不足 1px，此时按最小可用的高反推宽度
            if (!(w > 0) || !(h > 0)) {
                h = Math.max(1, Math.min(height, MIN_CROP_PIXELS));
                w = h * aspect;
            }
            if (w > width) {
                w = width;
                h = w / aspect;
            }
            if (h > height) {
                h = height;
                w = h * aspect;
            }
            rect.w = w;
            rect.h = h;
            rect.x = cx - w / 2;
            rect.y = cy - h / 2;
            this.clampRect();
            this.ensureMinRect();
        }

        // ---- 视图 ----
        applyTransform() {
            if (!this.viewport) return;
            const view = this.view;
            this.viewport.style.transform = 'translate(' + view.x.toFixed(2) + 'px,' +
                view.y.toFixed(2) + 'px) scale(' + view.scale.toFixed(5) + ')';
            // 手柄/描边靠这个变量反向补偿，缩放舞台时不用重建 DOM
            this.viewport.style.setProperty('--ce-inv', String(1 / view.scale));
            if (this.zoomValue) this.zoomValue.textContent = Math.round(view.scale * 100) + '%';
            if (this.rect) this.updateSizeLabel();
        }

        handleResize() {
            if (!this.stage) return;
            this.stageW = Math.max(1, Math.round(this.stage.clientWidth));
            this.stageH = Math.max(1, Math.round(this.stage.clientHeight));
            if (!this.natW) return;
            if (!this.userAdjusted) this.fitToStage();
            else this.applyTransform();
        }

        fitToStage() {
            if (!this.natW || !this.stageW || !this.stageH) return;
            const fit = Math.min(
                (this.stageW - FIT_PADDING * 2) / this.natW,
                (this.stageH - FIT_PADDING * 2) / this.natH
            );
            const scale = clamp(fit, MIN_SCALE, MAX_SCALE);
            this.view.scale = scale;
            this.view.x = Math.round((this.stageW - this.natW * scale) / 2);
            this.view.y = Math.round((this.stageH - this.natH * scale) / 2);
            this.userAdjusted = false;
            this.applyTransform();
        }

        zoomBy(factor) {
            if (!this.stageW) return;
            this.zoomAt(this.stageW / 2, this.stageH / 2, factor);
        }

        // 以光标为中心缩放：保持光标下的图像点不动，改的是舞台平移量
        zoomAt(stageX, stageY, factor) {
            const view = this.view;
            const next = clamp(view.scale * factor, MIN_SCALE, MAX_SCALE);
            if (Math.abs(next - view.scale) < 1e-5) return;
            const ratio = next / view.scale;
            view.x = stageX - (stageX - view.x) * ratio;
            view.y = stageY - (stageY - view.y) * ratio;
            view.scale = next;
            this.userAdjusted = true;
            this.applyTransform();
        }

        handleWheel(event) {
            if (this.closed) return;
            // 滚轮只缩放本编辑器，绝不透传给背后的画布，也不让页面滚动
            event.preventDefault();
            event.stopPropagation();
            if (!this.stage) return;
            const bounds = this.stage.getBoundingClientRect();
            const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
            this.zoomAt(event.clientX - bounds.left, event.clientY - bounds.top, factor);
        }

        // ---- 几何 ----
        imagePoint(event) {
            if (!this.imgEl) return null;
            const bounds = this.imgEl.getBoundingClientRect();
            if (!bounds.width || !bounds.height) return null;
            return {
                x: clamp((event.clientX - bounds.left) * this.natW / bounds.width, 0, this.natW),
                y: clamp((event.clientY - bounds.top) * this.natH / bounds.height, 0, this.natH)
            };
        }

        isOverImage(event) {
            if (!this.imgEl) return false;
            const bounds = this.imgEl.getBoundingClientRect();
            return event.clientX >= bounds.left && event.clientX <= bounds.right &&
                event.clientY >= bounds.top && event.clientY <= bounds.bottom;
        }

        rectContains(point) {
            const rect = this.rect;
            if (!rect || !point) return false;
            return point.x >= rect.x && point.x <= rect.x + rect.w &&
                point.y >= rect.y && point.y <= rect.y + rect.h;
        }

        aspectOf(name) {
            if (!name || name === 'free') return 0;
            if (name === 'source') return this.natH ? this.natW / this.natH : 0;
            const parts = String(name).split(':');
            const w = Number(parts[0]);
            const h = Number(parts[1]);
            if (!w || !h || !Number.isFinite(w) || !Number.isFinite(h)) return 0;
            return w / h;
        }

        // 手柄命中：在屏幕坐标里比较，判定半径是屏幕像素，缩放后手感不变。
        // 先判四角再判四边，小选框时角优先，避免同一点既算边又算角。
        handleAt(event) {
            const rect = this.rect;
            if (!rect || !this.imgEl) return '';
            const bounds = this.imgEl.getBoundingClientRect();
            if (!bounds.width || !bounds.height) return '';
            const left = bounds.left + rect.x / this.natW * bounds.width;
            const top = bounds.top + rect.y / this.natH * bounds.height;
            const right = left + rect.w / this.natW * bounds.width;
            const bottom = top + rect.h / this.natH * bounds.height;
            const x = event.clientX;
            const y = event.clientY;
            if (x < left - HANDLE_REACH || x > right + HANDLE_REACH ||
                y < top - HANDLE_REACH || y > bottom + HANDLE_REACH) return '';
            const nearLeft = Math.abs(x - left) <= HANDLE_REACH;
            const nearRight = Math.abs(x - right) <= HANDLE_REACH;
            const nearTop = Math.abs(y - top) <= HANDLE_REACH;
            const nearBottom = Math.abs(y - bottom) <= HANDLE_REACH;
            if (nearTop && nearLeft) return 'nw';
            if (nearTop && nearRight) return 'ne';
            if (nearBottom && nearLeft) return 'sw';
            if (nearBottom && nearRight) return 'se';
            if (nearTop) return 'n';
            if (nearBottom) return 's';
            if (nearLeft) return 'w';
            if (nearRight) return 'e';
            return '';
        }

        // 把选框整体推回图内（不改尺寸），这是所有交互的最终兜底
        clampRect() {
            const rect = this.rect;
            if (!rect) return;
            const width = this.natW;
            const height = this.natH;
            rect.w = clamp(rect.w, 1, width);
            rect.h = clamp(rect.h, 1, height);
            rect.x = clamp(rect.x, 0, Math.max(0, width - rect.w));
            rect.y = clamp(rect.y, 0, Math.max(0, height - rect.h));
        }

        // 最小边 16 原图像素：比例锁定时不能各轴单独补足（会破坏比例），而要整体放大。
        ensureMinRect() {
            const rect = this.rect;
            if (!rect) return;
            const width = this.natW;
            const height = this.natH;
            const min = Math.min(MIN_CROP_PIXELS, width, height);
            const aspect = this.aspectOf(this.ratioName);
            if (aspect) {
                if (rect.w < 0.5 || rect.h < 0.5) {
                    // 退化成 0 的选框无法按比例放大，直接给一个居中的最大框
                    let w = Math.min(width, height * aspect);
                    let h = w / aspect;
                    rect.w = w;
                    rect.h = h;
                    rect.x = (width - w) / 2;
                    rect.y = (height - h) / 2;
                } else if (rect.w < min || rect.h < min) {
                    const grow = Math.max(min / rect.w, min / rect.h);
                    const fit = Math.min(1, width / (rect.w * grow), height / (rect.h * grow));
                    const cx = rect.x + rect.w / 2;
                    const cy = rect.y + rect.h / 2;
                    rect.w = rect.w * grow * fit;
                    rect.h = rect.h * grow * fit;
                    rect.x = cx - rect.w / 2;
                    rect.y = cy - rect.h / 2;
                }
            } else {
                if (rect.w < min) {
                    rect.x -= (min - rect.w) / 2;
                    rect.w = min;
                }
                if (rect.h < min) {
                    rect.y -= (min - rect.h) / 2;
                    rect.h = min;
                }
            }
            this.clampRect();
        }

        // 导出用整数像素矩形；和底栏 W × H 共用同一套取整，保证显示值等于实际导出尺寸
        cropRect() {
            const width = this.natW;
            const height = this.natH;
            const rect = this.rect || { x: 0, y: 0, w: width, h: height };
            const left = clamp(Math.round(rect.x), 0, Math.max(0, width - 1));
            const top = clamp(Math.round(rect.y), 0, Math.max(0, height - 1));
            const right = clamp(Math.round(rect.x + rect.w), left + 1, width);
            const bottom = clamp(Math.round(rect.y + rect.h), top + 1, height);
            return {
                x: left,
                y: top,
                width: right - left,
                height: bottom - top
            };
        }

        // ---- 渲染 ----
        scheduleRender() {
            if (this.frame || this.closed) return;
            const session = this;
            this.frame = requestAnimationFrame(function () {
                session.frame = 0;
                if (!session.closed) session.render();
            });
        }

        render() {
            if (!this.root || !this.rect || !this.natW) return;
            const rect = this.rect;
            const width = this.natW;
            const height = this.natH;
            const selection = this.selection;
            const masks = this.masks;

            if (selection) {
                selection.style.left = percent(rect.x / width);
                selection.style.top = percent(rect.y / height);
                selection.style.width = percent(rect.w / width);
                selection.style.height = percent(rect.h / height);
            }
            if (masks && masks.top) {
                // 用四块独立遮罩而不是一条 evenodd 多边形：多边形的两条"连接边"会自交，
                // 在选框外侧留下一条可见的对角亮缝；四块遮罩永远严丝合缝。
                const x = rect.x / width;
                const y = rect.y / height;
                const right = (rect.x + rect.w) / width;
                const bottom = (rect.y + rect.h) / height;
                const innerW = percent(Math.max(0, right - x));
                masks.top.style.left = '0%';
                masks.top.style.top = '0%';
                masks.top.style.width = '100%';
                masks.top.style.height = percent(y);
                masks.bottom.style.left = '0%';
                masks.bottom.style.top = percent(bottom);
                masks.bottom.style.width = '100%';
                masks.bottom.style.height = percent(Math.max(0, 1 - bottom));
                masks.left.style.left = '0%';
                masks.left.style.top = percent(y);
                masks.left.style.width = percent(x);
                masks.left.style.height = innerW ? percent(Math.max(0, bottom - y)) : '0%';
                masks.right.style.left = percent(right);
                masks.right.style.top = percent(y);
                masks.right.style.width = percent(Math.max(0, 1 - right));
                masks.right.style.height = percent(Math.max(0, bottom - y));
            }
            this.updateSizeLabel();
            this.updateTone();
        }

        updateSizeLabel() {
            if (!this.sizeValue || !this.natW) return;
            const crop = this.cropRect();
            const text = crop.width + ' × ' + crop.height;
            if (this.sizeValue.textContent !== text) this.sizeValue.textContent = text;
        }

        // 中心十字的颜色按取样亮度决定：亮底用黑，暗底用白，保证任何图上都看得见
        updateTone() {
            if (!this.selection || !this.centerEl) return;
            const crop = this.cropRect();
            const key = crop.x + ':' + crop.y + ':' + crop.width + ':' + crop.height;
            if (key === this.toneKey) return;
            this.toneKey = key;
            this.centerEl.style.color = this.sampleTone(crop);
        }

        sampleTone(crop) {
            const width = this.natW;
            const height = this.natH;
            if (!this.sampleCtx || !width || !height) return TONE_WHITE;
            const source = this.sampleSource || this.imgEl;
            if (!source) return TONE_WHITE;
            // 取选框中心约 1% 短边的区域：足够代表中心色调，又不会在大图上重采样整块
            const radius = Math.max(2, Math.min(width, height) * 0.01);
            const cx = clamp(crop.x + crop.width / 2, 0, width);
            const cy = clamp(crop.y + crop.height / 2, 0, height);
            const sx = clamp(cx - radius, 0, Math.max(0, width - 1));
            const sy = clamp(cy - radius, 0, Math.max(0, height - 1));
            let sw = Math.min(width - sx, radius * 2);
            let sh = Math.min(height - sy, radius * 2);
            if (!(sw > 0) || !(sh > 0)) return TONE_WHITE;
            const sourceWidth = this.sampleSource ? this.sampleSource.width : width;
            const sourceHeight = this.sampleSource ? this.sampleSource.height : height;
            const kx = sourceWidth / width;
            const ky = sourceHeight / height;
            let dx = sx * kx;
            let dy = sy * ky;
            let dw = Math.max(1, sw * kx);
            let dh = Math.max(1, sh * ky);
            if (dx + dw > sourceWidth) dw = Math.max(1, sourceWidth - dx);
            if (dy + dh > sourceHeight) dh = Math.max(1, sourceHeight - dy);
            try {
                const ctx = this.sampleCtx;
                ctx.clearRect(0, 0, CENTER_GRID, CENTER_GRID);
                ctx.fillStyle = '#242833';
                ctx.fillRect(0, 0, CENTER_GRID, CENTER_GRID);
                ctx.drawImage(source, dx, dy, dw, dh, 0, 0, CENTER_GRID, CENTER_GRID);
                const data = ctx.getImageData(0, 0, CENTER_GRID, CENTER_GRID).data;
                let sum = 0;
                let count = 0;
                for (let i = 0; i < data.length; i += 4) {
                    sum += 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
                    count += 1;
                }
                if (!count) return TONE_WHITE;
                return sum / count < 128 ? TONE_WHITE : TONE_DARK;
            } catch (error) {
                // 跨域图片会污染 canvas，此时保持白色描边即可，不影响裁切结果
                return TONE_WHITE;
            }
        }

        // ---- 交互 ----
        handleClick(event) {
            if (this.closed) return;
            const target = event.target;
            if (!target || !target.closest) return;
            const ratioButton = target.closest('[data-ratio]');
            if (ratioButton && this.root.contains(ratioButton)) {
                event.preventDefault();
                if (!this.busy) this.setRatio(ratioButton.dataset.ratio);
                return;
            }
            const actionButton = target.closest('[data-act]');
            if (!actionButton || !this.root.contains(actionButton)) return;
            const action = actionButton.dataset.act;
            if (action === 'apply') {
                event.preventDefault();
                this.apply();
            } else if (action === 'cancel') {
                event.preventDefault();
                this.close(false);
            } else if (action === 'clear') {
                event.preventDefault();
                if (!this.busy) this.resetRect();
            } else if (action === 'fit') {
                event.preventDefault();
                this.fitToStage();
            } else if (action === 'zoom-in') {
                event.preventDefault();
                this.zoomBy(1.2);
            } else if (action === 'zoom-out') {
                event.preventDefault();
                this.zoomBy(1 / 1.2);
            }
        }

        setRatio(name) {
            if (RATIOS.indexOf(name) < 0) return;
            this.ratioName = name;
            const aspect = this.aspectOf(name);
            if (aspect) {
                // 与参考实现一致：切到带比例的模式时给一个居中的最大选框，位置可再拖
                const width = this.natW;
                const height = this.natH;
                const w = Math.min(width, height * aspect);
                const h = w / aspect;
                this.rect = {
                    x: (width - w) / 2,
                    y: (height - h) / 2,
                    w: w,
                    h: h
                };
            }
            this.ensureMinRect();
            this.syncRatioButtons();
            this.scheduleRender();
        }

        // 注意：data-ratio 只能挂在本组按钮上。若也挂到 root，closest('[data-ratio]')
        // 会让任意点击（含「应用」）都命中 root 而被比例分支吞掉。
        syncRatioButtons() {
            if (!this.root) return;
            const buttons = this.root.querySelectorAll('[data-ratio]');
            for (let i = 0; i < buttons.length; i += 1) {
                const active = buttons[i].dataset.ratio === this.ratioName;
                buttons[i].classList.toggle('is-active', active);
                buttons[i].setAttribute('aria-pressed', active ? 'true' : 'false');
            }
        }

        // 清除 = 回到整图。整图只与「自由」或「原图」比例自洽，因此同时解除比例锁定，
        // 避免出现「按钮写着 1:1、选框却是整图」的矛盾状态。
        resetRect() {
            this.ratioName = 'free';
            this.rect = { x: 0, y: 0, w: this.natW, h: this.natH };
            this.syncRatioButtons();
            this.scheduleRender();
        }

        handlePointerDown(event) {
            if (this.closed || !this.ready || this.busy) return;
            if (event.button === 1 || (event.button === 0 && this.spaceDown)) {
                // 中键 / 空格拖拽平移：先掐掉浏览器默认的中键自动滚动
                event.preventDefault();
                this.beginPan(event);
                return;
            }
            if (event.button !== 0) return;
            // 双击的第二次按下交给 dblclick，否则会先触发一次移动/重画
            if (event.detail > 1) {
                event.preventDefault();
                return;
            }
            if (!this.isOverImage(event)) return;
            const point = this.imagePoint(event);
            if (!point) return;
            event.preventDefault();
            this.capturePointer(event);
            const handle = this.handleAt(event);
            const mode = handle ? 'resize' : (this.rectContains(point) ? 'move' : 'draw');
            this.drag = {
                mode: mode,
                handle: handle,
                start: point,
                point: point,
                rect: {
                    x: this.rect.x,
                    y: this.rect.y,
                    w: this.rect.w,
                    h: this.rect.h
                },
                moved: false
            };
            this.root.classList.add('is-dragging');
            if (mode === 'draw') {
                this.root.classList.add('is-drawing');
                this.rect = { x: point.x, y: point.y, w: 0, h: 0 };
                this.scheduleRender();
            }
        }

        handlePointerMove(event) {
            if (this.closed) return;
            if (this.pan) {
                this.updatePan(event);
                return;
            }
            if (!this.ready) return;
            if (this.drag) {
                event.preventDefault();
                const point = this.imagePoint(event);
                if (!point) return;
                this.updateDrag(point, event.shiftKey, event.altKey);
                return;
            }
            this.updateHoverCursor(event);
        }

        handlePointerUp(event) {
            if (this.closed) return;
            if (this.pan) {
                this.endPan(event);
                return;
            }
            if (!this.drag) return;
            const drag = this.drag;
            this.drag = null;
            this.root.classList.remove('is-dragging', 'is-drawing');
            this.releasePointer(event);
            if (drag.mode === 'draw' && !drag.moved) {
                // 只是点了一下图内空白：还原原选框，不要把它缩成 16px
                this.rect = drag.rect;
            } else {
                this.ensureMinRect();
            }
            this.scheduleRender();
        }

        updateDrag(point, shiftKey, altKey) {
            const drag = this.drag;
            if (!drag) return;
            drag.point = point;
            const width = this.natW;
            const height = this.natH;
            const scale = this.view.scale || 1;
            const dx = point.x - drag.start.x;
            const dy = point.y - drag.start.y;
            if (!drag.moved && Math.abs(dx) * scale < CLICK_SLOP && Math.abs(dy) * scale < CLICK_SLOP) {
                return;
            }
            drag.moved = true;
            // 预设比例优先于 Shift：锁定后任何拖拽都保持该比例，自由模式才允许 Shift 临时锁 1:1
            const locked = this.aspectOf(this.ratioName);
            const aspect = locked || (shiftKey ? 1 : 0);
            const symmetric = !!altKey;
            if (drag.mode === 'move') {
                this.rect = {
                    x: clamp(drag.rect.x + dx, 0, Math.max(0, width - drag.rect.w)),
                    y: clamp(drag.rect.y + dy, 0, Math.max(0, height - drag.rect.h)),
                    w: drag.rect.w,
                    h: drag.rect.h
                };
            } else if (drag.mode === 'resize') {
                this.rect = resizeRect(drag.rect, drag.handle, point, aspect, width, height, symmetric);
            } else {
                this.rect = drawRect(drag.start, point, aspect, width, height, symmetric);
            }
            this.scheduleRender();
        }

        updateHoverCursor(event) {
            if (!this.stage) return;
            if (this.spaceDown) {
                this.stage.style.cursor = 'grab';
                return;
            }
            if (!this.isOverImage(event)) {
                this.stage.style.cursor = 'default';
                return;
            }
            const handle = this.handleAt(event);
            if (handle) {
                this.stage.style.cursor = HANDLE_CURSORS[handle] || 'default';
                return;
            }
            const point = this.imagePoint(event);
            this.stage.style.cursor = this.rectContains(point) ? 'move' : 'crosshair';
        }

        handleDoubleClick(event) {
            if (this.closed || this.busy || !this.ready) return;
            if (!this.isOverImage(event)) return;
            const point = this.imagePoint(event);
            if (!this.rectContains(point)) return;
            event.preventDefault();
            event.stopPropagation();
            this.apply();
        }

        capturePointer(event) {
            this.pointerId = event.pointerId;
            try {
                this.stage.setPointerCapture(event.pointerId);
            } catch (error) { /* 旧浏览器不支持捕获，退回普通事件流 */ }
        }

        releasePointer(event) {
            const id = this.pointerId;
            this.pointerId = null;
            if (id == null) return;
            try {
                this.stage.releasePointerCapture(id);
            } catch (error) { /* 忽略 */ }
        }

        beginPan(event) {
            this.drag = null;
            this.root.classList.remove('is-dragging', 'is-drawing');
            this.pan = {
                startX: event.clientX,
                startY: event.clientY,
                viewX: this.view.x,
                viewY: this.view.y
            };
            this.capturePointer(event);
            this.stage.classList.add('is-panning');
        }

        updatePan(event) {
            if (!this.pan) return;
            event.preventDefault();
            this.view.x = this.pan.viewX + (event.clientX - this.pan.startX);
            this.view.y = this.pan.viewY + (event.clientY - this.pan.startY);
            this.userAdjusted = true;
            this.applyTransform();
        }

        endPan(event) {
            if (!this.pan) return;
            this.pan = null;
            if (this.stage) this.stage.classList.remove('is-panning');
            this.releasePointer(event);
        }

        // ---- 键盘 ----
        isEditableTarget(target) {
            if (!target || !target.tagName) return false;
            const tag = target.tagName.toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'button' || tag === 'a') {
                return true;
            }
            return !!target.isContentEditable;
        }

        handleKeyDown(event) {
            if (this.closed) return;
            if (event.isComposing) return;
            const key = event.key;
            const editable = this.isEditableTarget(event.target);

            if (key === 'Escape') {
                // 输入框里按 Esc 也当作取消整个编辑器：全屏编辑器没有别的「退一层」语义
                event.preventDefault();
                event.stopPropagation();
                if (!this.busy) this.close(false);
                return;
            }
            if (editable) return;
            if (this.busy) return;

            if (key === ' ' || key === 'Spacebar' || event.code === 'Space') {
                event.preventDefault();
                this.spaceDown = true;
                if (this.stage) this.stage.style.cursor = 'grab';
                return;
            }
            if (key === 'Enter') {
                event.preventDefault();
                this.apply();
                return;
            }
            if (key === 'Shift' || key === 'Alt') {
                if (this.drag) {
                    // 拖拽途中补按修饰键：立刻按新约束重算，不需要再动一次鼠标
                    event.preventDefault();
                    this.updateDrag(this.drag.point, event.shiftKey, event.altKey);
                }
                return;
            }
            const direction = {
                ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]
            }[key];
            if (!direction || !this.rect) return;
            event.preventDefault();
            const step = event.shiftKey ? 10 : 1;
            this.rect.x = clamp(this.rect.x + direction[0] * step, 0,
                Math.max(0, this.natW - this.rect.w));
            this.rect.y = clamp(this.rect.y + direction[1] * step, 0,
                Math.max(0, this.natH - this.rect.h));
            this.scheduleRender();
        }

        handleKeyUp(event) {
            if (this.closed) return;
            if (event.key === ' ' || event.key === 'Spacebar' || event.code === 'Space') {
                this.spaceDown = false;
                if (this.stage && !this.pan) this.stage.style.cursor = '';
            }
        }

        handleWindowBlur() {
            this.spaceDown = false;
            if (this.stage && !this.pan) this.stage.style.cursor = '';
            // 失焦时 pointerup 可能永远收不到，就地收尾，避免选框卡在中间态
            if (this.drag) {
                this.drag = null;
                if (this.root) this.root.classList.remove('is-dragging', 'is-drawing');
                this.ensureMinRect();
                this.scheduleRender();
            }
            if (this.pan) {
                this.pan = null;
                if (this.stage) this.stage.classList.remove('is-panning');
            }
        }

        // ---- 保存 ----
        setBusy(busy) {
            this.busy = !!busy;
            if (this.root) this.root.classList.toggle('is-busy', this.busy);
            if (this.applyBtn) this.applyBtn.disabled = this.busy;
            if (this.applyText) {
                this.applyText.textContent = this.busy ? this.label('applying') : this.label('apply');
            }
        }

        async apply() {
            if (this.closed || this.busy || !this.ready) return;
            this.setBusy(true);
            try {
                const result = await this.buildResult();
                if (this.onSave) await this.onSave(result);
                this.close(true);
            } catch (error) {
                // 保存失败必须留在编辑器里，让用户重试或取消
                this.setBusy(false);
                if (window.console && console.warn) console.warn('CropEditor: 保存失败', error);
                this.showToast(this.label('saveFail'));
            }
        }

        async buildResult() {
            const crop = this.cropRect();
            const width = this.natW;
            const height = this.natH;
            // 只有导出这一步用自然像素；不做缩放/降采样，保证裁切精度
            const canvas = document.createElement('canvas');
            canvas.width = crop.width;
            canvas.height = crop.height;
            const ctx = canvas.getContext('2d');
            if (!ctx) throw new Error('canvas context unavailable');
            ctx.drawImage(this.imgEl, crop.x, crop.y, crop.width, crop.height,
                0, 0, crop.width, crop.height);
            const blob = await canvasToBlob(canvas);
            if (!blob) throw new Error('crop export failed');
            const nx = clamp(crop.x / width, 0, 1);
            const ny = clamp(crop.y / height, 0, 1);
            return {
                rect: {
                    x: nx,
                    y: ny,
                    w: clamp(crop.width / width, 0, 1 - nx),
                    h: clamp(crop.height / height, 0, 1 - ny)
                },
                ratio: this.ratioName,
                width: crop.width,
                height: crop.height,
                blob: blob
            };
        }

        // ---- 提示 ----
        showToast(text) {
            if (!this.toastEl || !text) return;
            this.toastEl.textContent = text;
            this.toastEl.hidden = false;
            if (this.toastTimer) clearTimeout(this.toastTimer);
            const session = this;
            this.toastTimer = setTimeout(function () {
                session.toastTimer = 0;
                if (session.toastEl) session.toastEl.hidden = true;
            }, 4200);
        }

        showError(text) {
            if (!this.errorBox) return;
            if (this.errorText) this.errorText.textContent = text || '';
            this.errorBox.hidden = false;
            if (this.root) this.root.classList.add('is-error');
        }

        // 加载失败：给出可见提示后自动 close(false)。停留一会儿是为了让用户看到原因，
        // 同时保证 open() 不会永久挂起。
        failLoad(error) {
            if (window.console && console.warn) console.warn('CropEditor: 图片加载失败', error);
            this.showError(this.label('loadFail'));
            this.showToast(this.label('loadFail'));
            if (this.applyBtn) this.applyBtn.disabled = true;
            const session = this;
            if (this.loadTimer) clearTimeout(this.loadTimer);
            this.loadTimer = setTimeout(function () {
                session.loadTimer = 0;
                session.close(false);
            }, LOAD_FAIL_LINGER);
        }
    }

    // 单实例：重复 open 时先关掉旧实例，避免两个全屏层叠在一起
    let activeSession = null;

    const CropEditor = {
        RATIOS: RATIOS.slice(),
        open: function (opts) {
            // 契约是「只回布尔、不抛错」：缺 imageUrl 属接线错误，记日志后按取消处理，
            // 避免调用方 await 时冒出一个没人 catch 的 rejection。
            if (!opts || !opts.imageUrl) {
                if (window.console && console.warn) {
                    console.warn('CropEditor.open: options.imageUrl is required');
                }
                return Promise.resolve(false);
            }
            if (activeSession) activeSession.close(false);
            const session = new CropEditorSession(opts);
            activeSession = session;
            const promise = session.start();
            // 只清理模块级的单实例引用，不吞掉异常也不改变返回值：start() 正常实现不会 reject，
            // 但 DOM 构建等同步异常也要把 activeSession 清掉，否则下次 open 会对着已废弃实例 close()。
            const release = function () {
                if (activeSession === session) activeSession = null;
            };
            promise.then(release, release);
            return promise;
        }
    };

    window.CropEditor = CropEditor;
})();