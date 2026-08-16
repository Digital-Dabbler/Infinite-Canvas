/* Prompt library: system inspiration, favorites, and private presets. */
const PROMPT_EDITOR_SUBCATEGORIES = {
    style: [['real', '真人风格'], ['2d', '2D 风格'], ['3d', '3D 风格']],
    filter: [['film', '胶片质感'], ['color', '色彩调校'], ['lighting', '光效氛围']],
    function: [['character', '角色'], ['scene', '场景'], ['composition', '构图']]
};

const PromptLibrary = {
    tab: 'inspiration',
    category: 'all',
    subcategory: 'all',
    search: '',
    library: null,
    favorites: new Set(),
    editorId: '',
    previewId: '',
    previewTrigger: null,
    publishItemId: '',
    publishTrigger: null,
    initialized: false,
    loading: false,

    init() {
        if (this.initialized || !document.getElementById('prompts-library-overlay')) return;
        this.initialized = true;
        const overlay = document.getElementById('prompts-library-overlay');
        overlay.addEventListener('click', event => this.handleClick(event));
        ['pointerdown', 'mousedown', 'dblclick', 'wheel'].forEach(type => {
            overlay.addEventListener(type, event => event.stopPropagation());
        });
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;
            if (this.publishItemId) {
                event.preventDefault();
                event.stopImmediatePropagation();
                this.closePublishDialog();
                return;
            }
            if (!this.previewId) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            this.closePreview();
        }, true);
        LibrarySearch.setup('prompts-library-overlay', value => { this.search = String(value || '').trim().toLowerCase(); this.render(); });
    },

    async open(options = {}) {
        this.init();
        if (options.targetId) window.__promptLibraryTargetId = String(options.targetId);
        else window.clearPromptLibraryTarget?.();
        this.tab = options.tab || this.tab || 'inspiration';
        this.category = 'all'; this.subcategory = 'real'; this.editorId = '';
        this.closePreview({restoreFocus:false});
        LibraryModalManager.open('prompts');
        await this.load();
    },

    async load() {
        if (this.loading) return;
        this.loading = true;
        this.loadingState();
        try {
            let response = await fetch('/api/prompt-libraries');
            if (!response.ok) throw new Error('加载提示词库失败');
            this.library = (await response.json()).library || { libraries: [] };
            if (!this.mineLibrary()) {
                await fetch('/api/prompt-libraries', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
                response = await fetch('/api/prompt-libraries');
                this.library = (await response.json()).library || this.library;
            }
            const favorites = await fetch('/api/library/favorites?kind=prompt');
            if (favorites.ok) this.favorites = new Set((await favorites.json()).favorites || []);
            this.render();
        } catch (error) {
            this.content().innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="alert-circle"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(error.message || '加载失败')}</div></div>`;
            window.lucide?.createIcons();
        } finally { this.loading = false; }
    },

    overlay() { return document.getElementById('prompts-library-overlay'); },
    content() { return this.overlay()?.querySelector('.library-content'); },
    mineLibrary() { const uid = String(this.library?.viewer?.user_id || ''); return (this.library?.libraries || []).find(lib => lib.personal && String(lib.owner_id || '') === uid) || null; },
    allItems() {
        const items = [
            ...(this.library?.libraries || []).flatMap(lib => (lib.items || []).map(item => ({ ...item, __libraryId: lib.id }))),
            ...(this.library?.inspiration || []),
            ...(this.library?.published || []),
        ];
        return [...new Map(items.filter(item => item?.id).map(item => [item.id, item])).values()];
    },
    inspirationItems() { return Array.isArray(this.library?.inspiration) ? this.library.inspiration : this.allItems().filter(item => item.owner_type === 'system' || item.__libraryId === 'system'); },
    publishedItems() { return Array.isArray(this.library?.published) ? this.library.published : []; },
    publishedForSource(id) { return this.publishedItems().find(item => item.source_prompt_id === id) || null; },
    publishedMeta(item) {
        const author = item?.owner_name || t('library.unknownAuthor', '未知作者');
        const timestamp = Number(item?.published_at || 0);
        if (!timestamp) return author;
        return `${author} · ${new Date(timestamp).toLocaleDateString()}`;
    },
    mineItems() { return this.mineLibrary()?.items || []; },
    find(id) { return this.allItems().find(item => item.id === id) || null; },
    targetId() { return window.getPromptLibraryTargetId?.() || String(window.__promptLibraryTargetId || ''); },
    activeTarget() { return typeof nodes !== 'undefined' ? nodes.find(node => node?.id === this.targetId()) : null; },

    filteredItems() {
        let items = this.tab === 'myPrompts' ? this.mineItems()
            : this.tab === 'myPublished' ? this.publishedItems()
            : this.tab === 'favorites' ? this.inspirationItems().filter(item => this.favorites.has(item.id))
            : this.inspirationItems();
        if (this.tab === 'inspiration' && this.category !== 'all') items = items.filter(item => item.category === this.category);
        if (this.tab === 'inspiration' && this.category === 'style' && this.subcategory !== 'all') items = items.filter(item => item.subcategory === this.subcategory);
        if (this.search) items = items.filter(item => [item.name, item.description, item.category, item.subcategory, item.prefix, item.suffix].join(' ').toLowerCase().includes(this.search));
        return items;
    },

    render() {
        const overlay = this.overlay(); if (!overlay) return;
        overlay.querySelector('.library-tabs').innerHTML = this.tabsHtml();
        this.renderHeaderAction(overlay);
        const categories = overlay.querySelector('.library-categories');
        const categoriesHtml = this.categoriesHtml();
        categories.hidden = !categoriesHtml;
        categories.innerHTML = categoriesHtml;
        this.renderContent(); window.lucide?.createIcons();
    },
    renderHeaderAction(overlay) {
        const headerActions = overlay.querySelector('.library-header-right');
        headerActions?.querySelector('.prompt-library-header-create')?.remove();
        if (this.tab !== 'myPrompts' || this.editorId || !headerActions) return;
        const closeButton = headerActions.querySelector('.library-close-btn');
        const button = `<button type="button" class="library-editor-btn primary prompt-library-header-create" data-pl-new><i data-lucide="plus"></i>${LibraryUtils.escapeHtml(t('library.newPrompt', '新建提示词'))}</button>`;
        if (closeButton) closeButton.insertAdjacentHTML('beforebegin', button);
        else headerActions.insertAdjacentHTML('beforeend', button);
    },
    tabsHtml() { return [
        ['inspiration','lightbulb',t('library.inspiration', '灵感库')], ['favorites','heart',t('library.myFavorites', '我的收藏')], ['myPrompts','text-cursor-input',t('library.myPrompts', '我的提示词')], ['myPublished','send',t('library.myPublished', '我的发布')]
    ].map(([id,icon,label]) => `<button type="button" class="library-tab ${this.tab === id ? 'active' : ''}" data-pl-tab="${id}"><i data-lucide="${icon}"></i><span>${label}</span></button>`).join(''); },
    categoriesHtml() {
        if (this.tab !== 'inspiration') return '';
        return [['all','全部'],['style','风格'],['filter','滤镜'],['function','功能'],['other','其他']]
            .map(([id,label]) => `<button type="button" class="library-category ${this.category===id?'active':''}" data-pl-category="${id}">${label}</button>`).join('');
    },
    styleSidebarHtml() {
        return `<aside class="prompt-library-sidebar" aria-label="风格筛选">${[['real','真人风格'],['2d','2D 风格'],['3d','3D 风格']].map(([id,label]) => `<button type="button" class="${this.subcategory===id?'active':''}" data-pl-subcategory="${id}">${label}</button>`).join('')}</aside>`;
    },

    cardHtml(item) {
        const selected = this.activeTarget() && Array.isArray(this.activeTarget().promptPresets) && this.activeTarget().promptPresets.some(preset => preset.id === item.id);
        const favorite = this.favorites.has(item.id);
        const isMine = this.tab === 'myPrompts';
        const isPublished = this.tab === 'myPublished';
        const publication = isMine ? this.publishedForSource(item.id) : null;
        const preview = [item.prefix || item.positive, item.suffix || item.negative].filter(Boolean).join(' · ');
        const cover = item.cover_url ? `<img src="${LibraryUtils.escapeHtml(item.cover_url)}" alt="${LibraryUtils.escapeHtml(item.name || '')}" loading="lazy">` : `<div class="prompt-card-placeholder"><i data-lucide="sparkles"></i></div>`;
        const applyLabel = selected ? '取消' : '应用';
        const applyIcon = selected ? 'x' : 'plus';
        return `<article class="prompt-card ${selected ? 'is-selected' : ''}">
            <div class="prompt-card-cover">${cover}<div class="prompt-card-hover" aria-label="${LibraryUtils.escapeHtml(item.name || '提示词')} 操作"><button type="button" class="prompt-card-action prompt-card-apply ${selected ? 'is-applied' : ''}" data-pl-apply="${LibraryUtils.escapeHtml(item.id)}" aria-pressed="${selected ? 'true' : 'false'}"><i data-lucide="${applyIcon}"></i>${applyLabel}</button><button type="button" class="prompt-card-action" data-pl-preview="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="expand"></i>预览</button></div></div>
            <div class="prompt-card-info"><h3>${LibraryUtils.escapeHtml(item.name || t('library.untitled', '未命名'))}</h3><p>${LibraryUtils.escapeHtml(item.description || '')}</p><div class="prompt-card-preview">${LibraryUtils.escapeHtml(LibraryUtils.truncate(preview, 96) || t('library.emptyPrompt', '（空提示词）'))}</div>${!isMine && !isPublished && item.owner_type !== 'system' ? `<span class="prompt-card-meta"><i data-lucide="user-round"></i>${LibraryUtils.escapeHtml(this.publishedMeta(item))}</span>` : ''}</div>
            ${isMine ? `<div class="prompt-card-manage" role="group" aria-label="${LibraryUtils.escapeHtml(t('library.promptManageActions', '提示词管理操作'))}"><button type="button" data-pl-edit="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="pencil"></i><span>${LibraryUtils.escapeHtml(t('library.edit', '编辑'))}</span></button>${publication ? `<button type="button" class="is-published" data-pl-show-publication="${LibraryUtils.escapeHtml(publication.id)}"><i data-lucide="check-circle-2"></i><span>${LibraryUtils.escapeHtml(t('library.published', '已发布'))}</span></button>` : `<button type="button" data-pl-publish="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="send"></i><span>${LibraryUtils.escapeHtml(t('library.publish', '发布'))}</span></button>`}<button type="button" class="danger" data-pl-delete="${LibraryUtils.escapeHtml(item.id)}" aria-label="${LibraryUtils.escapeHtml(t('library.delete', '删除'))}" title="${LibraryUtils.escapeHtml(t('library.delete', '删除'))}"><i data-lucide="trash-2"></i></button></div>` : isPublished ? `<div class="prompt-card-manage prompt-card-published-manage" role="group" aria-label="${LibraryUtils.escapeHtml(t('library.publishedManageActions', '已发布提示词操作'))}"><button type="button" class="danger" data-pl-withdraw="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="rotate-ccw"></i><span>${LibraryUtils.escapeHtml(t('library.withdraw', '撤回'))}</span></button></div>` : `<div class="prompt-card-foot"><button type="button" class="prompt-card-favorite ${favorite ? 'is-favorite' : ''}" data-pl-favorite="${LibraryUtils.escapeHtml(item.id)}" title="${favorite ? t('library.unfavorite', '取消收藏') : t('library.favorite', '收藏')}" aria-label="${favorite ? t('library.unfavorite', '取消收藏') : t('library.favorite', '收藏')}" aria-pressed="${favorite ? 'true' : 'false'}"><i data-lucide="heart"></i></button></div>`}
        </article>`;
    },

    renderContent() {
        const content = this.content(); if (!content) return;
        content.classList.remove('is-prompt-editor-view');
        if (this.editorId === '__new__' || this.editorId) { content.classList.add('is-prompt-editor-view'); content.innerHTML = this.editorHtml(this.editorId === '__new__' ? null : this.find(this.editorId)); window.lucide?.createIcons(); return; }
        const items = this.filteredItems();
        const emptyDesc = this.tab === 'myPrompts' ? t('library.promptEmptyMine', '新建一个预设，快速复用你的前缀与后缀。')
            : this.tab === 'myPublished' ? t('library.promptEmptyPublished', '在“我的提示词”中发布模板后，会在这里管理公开版本。')
            : t('library.promptEmptySearch', '尝试调整分类或搜索关键词。');
        const grid = items.length ? `<div class="prompt-card-grid">${items.map(item => this.cardHtml(item)).join('')}</div>` : `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="text-cursor-input"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(t('library.empty', '暂无提示词'))}</div><div class="library-empty-desc">${LibraryUtils.escapeHtml(emptyDesc)}</div>${this.tab === 'myPrompts' ? `<button type="button" class="library-editor-btn primary" data-pl-new><i data-lucide="plus"></i>${LibraryUtils.escapeHtml(t('library.newPrompt', '新建提示词'))}</button>` : ''}</div>`;
        content.innerHTML = this.tab === 'inspiration' && this.category === 'style'
            ? `<div class="prompt-library-browser">${this.styleSidebarHtml()}<section class="prompt-library-results">${grid}</section></div>`
            : `<section class="prompt-library-results is-wide">${grid}</section>`;
        window.lucide?.createIcons();
        this.bindCardActions(content);
        if (this.returnFocusId) {
            const focusId = this.returnFocusId;
            this.returnFocusId = '';
            requestAnimationFrame(() => content.querySelector(`[data-pl-edit="${CSS.escape(focusId)}"]`)?.focus());
        }
    },

    openEditor(id) {
        this.editorId = String(id || '');
        this.returnFocusId = this.editorId;
        this.render();
    },

    bindCardActions(content) {
        content.querySelectorAll('[data-pl-edit]').forEach(button => button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            this.openEditor(button.dataset.plEdit);
        }));
    },

    detailHtml(item) {
        if (!item) return '';
        const prefix = item.prefix || item.positive || '';
        const suffix = item.suffix || item.negative || '';
        const selected = this.activeTarget() && Array.isArray(this.activeTarget().promptPresets) && this.activeTarget().promptPresets.some(preset => preset.id === item.id);
        const previewLabel = t('smart.promptPreview', '提示词预览');
        const recipeLabel = t('smart.promptRecipe', '提示词配方');
        const prefixLabel = t('smart.promptPrefix', '前缀');
        const userPromptLabel = t('smart.promptUserInput', '用户提示词');
        const suffixLabel = t('smart.promptSuffix', '后缀');
        const copyLabel = t('smart.copyPromptRecipe', '复制组合');
        const applyLabel = selected ? t('smart.unapplyPrompt', '取消应用') : t('smart.applyPrompt', '应用提示词');
        const applyIcon = selected ? 'x' : 'plus';
        const closeLabel = t('library.close', '关闭预览');
        return `<section class="prompt-detail" aria-labelledby="prompt-preview-title" aria-describedby="prompt-preview-description">
            <header class="prompt-preview-header"><div><span class="prompt-preview-eyebrow">${LibraryUtils.escapeHtml(previewLabel)}</span><span class="prompt-preview-recipe-label">${LibraryUtils.escapeHtml(recipeLabel)}</span></div><button type="button" class="prompt-preview-close" data-pl-preview-close aria-label="${LibraryUtils.escapeHtml(closeLabel)}"><i data-lucide="x"></i></button></header>
            <div class="prompt-detail-body"><div class="prompt-detail-grid">
                <figure class="prompt-detail-cover">${item.cover_url ? `<img src="${LibraryUtils.escapeHtml(item.cover_url)}" alt="${LibraryUtils.escapeHtml(item.name || '')}">` : '<i data-lucide="sparkles" aria-hidden="true"></i>'}</figure>
                <div class="prompt-detail-copy"><span class="prompt-detail-kicker">${LibraryUtils.escapeHtml(item.subcategory || item.category)}</span><h2 id="prompt-preview-title">${LibraryUtils.escapeHtml(item.name)}</h2><p id="prompt-preview-description">${LibraryUtils.escapeHtml(item.description || '')}</p>
                    <section class="prompt-detail-recipe" aria-label="${LibraryUtils.escapeHtml(recipeLabel)}">
                        <div class="prompt-detail-field"><b>${LibraryUtils.escapeHtml(prefixLabel)}</b><pre>${LibraryUtils.escapeHtml(prefix)}</pre></div>
                        <div class="prompt-detail-user-input"><span>${LibraryUtils.escapeHtml(userPromptLabel)}</span><i data-lucide="text-cursor-input" aria-hidden="true"></i></div>
                        <div class="prompt-detail-field"><b>${LibraryUtils.escapeHtml(suffixLabel)}</b><pre>${LibraryUtils.escapeHtml(suffix)}</pre></div>
                    </section>
                </div>
            </div></div>
            <footer class="prompt-detail-actions"><button type="button" class="library-editor-btn" data-pl-copy="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="copy"></i>${LibraryUtils.escapeHtml(copyLabel)}</button><button type="button" class="library-editor-btn prompt-preview-apply ${selected ? 'is-applied' : 'primary'}" data-pl-apply="${LibraryUtils.escapeHtml(item.id)}" aria-pressed="${selected ? 'true' : 'false'}"><i data-lucide="${applyIcon}"></i>${LibraryUtils.escapeHtml(applyLabel)}</button></footer>
        </section>`;
    },

    previewOverlay() {
        let overlay = document.getElementById('prompt-library-preview-overlay');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'prompt-library-preview-overlay';
        overlay.className = 'prompt-library-preview-overlay';
        overlay.innerHTML = '<div class="prompt-library-preview-modal" role="dialog" aria-modal="true" aria-labelledby="prompt-preview-title" aria-describedby="prompt-preview-description"><div class="prompt-library-preview-content"></div></div>';
        overlay.addEventListener('click', event => {
            if (event.target !== overlay) return;
            event.stopPropagation();
            this.closePreview();
        });
        this.overlay()?.appendChild(overlay);
        return overlay;
    },

    openPreview(id, trigger) {
        if (!this.find(id)) return;
        this.previewId = id;
        this.previewTrigger = trigger || null;
        const overlay = this.previewOverlay();
        overlay.classList.add('open');
        this.renderPreview();
        LibraryModalManager.trapFocus(overlay);
        requestAnimationFrame(() => overlay.querySelector('[data-pl-preview-close]')?.focus());
    },

    renderPreview() {
        if (!this.previewId) return;
        const overlay = this.previewOverlay();
        const item = this.find(this.previewId);
        if (!item) { this.closePreview(); return; }
        overlay.querySelector('.prompt-library-preview-content').innerHTML = this.detailHtml(item);
        window.lucide?.createIcons();
    },

    showPreviewFeedback(message, isError=false) {
        const modal = document.querySelector('#prompt-library-preview-overlay .prompt-library-preview-modal');
        if (!modal) return;
        let feedback = modal.querySelector('.prompt-preview-feedback');
        if (!feedback) {
            feedback = document.createElement('div');
            feedback.className = 'prompt-preview-feedback';
            feedback.setAttribute('role', 'status');
            feedback.setAttribute('aria-live', 'polite');
            modal.appendChild(feedback);
        }
        feedback.textContent = message;
        feedback.classList.toggle('is-error', isError);
        feedback.classList.remove('show');
        requestAnimationFrame(() => feedback.classList.add('show'));
        clearTimeout(this._previewFeedbackTimer);
        this._previewFeedbackTimer = setTimeout(() => feedback.classList.remove('show'), 2200);
    },

    closePreview({restoreFocus=true} = {}) {
        const overlay = document.getElementById('prompt-library-preview-overlay');
        const trigger = this.previewTrigger;
        this.previewId = '';
        this.previewTrigger = null;
        overlay?.classList.remove('open');
        if (this.overlay()?.classList.contains('open')) LibraryModalManager.trapFocus(this.overlay());
        if (restoreFocus && trigger?.isConnected) requestAnimationFrame(() => trigger.focus());
    },

    publishDialog() {
        let overlay = document.getElementById('prompt-library-publish-overlay');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'prompt-library-publish-overlay';
        overlay.className = 'prompt-library-publish-overlay';
        overlay.innerHTML = `<section class="prompt-library-publish-dialog" role="dialog" aria-modal="true" aria-labelledby="prompt-publish-title" aria-describedby="prompt-publish-hint">
            <form data-pl-publish-form novalidate>
                <header class="prompt-publish-header"><span class="prompt-publish-kicker"><i data-lucide="send" aria-hidden="true"></i><span data-pl-publish-kicker></span></span><h2 id="prompt-publish-title"></h2><p id="prompt-publish-hint"></p></header>
                <div class="prompt-publish-fields"><label><span data-pl-publish-name-label></span><input type="text" data-pl-publish-name maxlength="80" autocomplete="off" required></label><label><span data-pl-publish-category-label></span><select data-pl-publish-category></select></label><label data-pl-publish-subcategory-wrap><span data-pl-publish-subcategory-label></span><select data-pl-publish-subcategory></select></label></div>
                <p class="prompt-publish-error" data-pl-publish-error role="alert" aria-live="assertive"></p>
                <footer class="prompt-publish-actions"><button type="button" class="library-editor-btn" data-pl-publish-cancel></button><button type="submit" class="library-editor-btn primary" data-pl-publish-confirm><i data-lucide="send" aria-hidden="true"></i><span></span></button></footer>
            </form>
        </section>`;
        overlay.addEventListener('click', event => {
            if (event.target !== overlay) return;
            event.stopPropagation();
            this.closePublishDialog();
        });
        overlay.querySelector('[data-pl-publish-category]')?.addEventListener('change', event => {
            this.updatePublishSubcategory(event.target.value);
        });
        overlay.querySelector('[data-pl-publish-form]')?.addEventListener('submit', event => this.submitPublishDialog(event));
        this.overlay()?.appendChild(overlay);
        return overlay;
    },

    publicationCategoryOptions(category) {
        const options = [
            ['style', t('library.categoryStyle', '风格')],
            ['filter', t('library.categoryFilter', '滤镜')],
            ['function', t('library.categoryFunction', '功能')],
            ['other', t('library.categoryOther', '其他')],
        ];
        return options.map(([id, label]) => `<option value="${id}" ${category === id ? 'selected' : ''}>${LibraryUtils.escapeHtml(label)}</option>`).join('');
    },

    publicationSubcategoryOptions(category, selected = '') {
        const options = this.subcategoryOptions(category);
        if (!options.length) return '';
        const selectedId = options.some(([id]) => id === selected) ? selected : options[0][0];
        return options.map(([id, label]) => `<option value="${id}" ${id === selectedId ? 'selected' : ''}>${LibraryUtils.escapeHtml(label)}</option>`).join('');
    },

    updatePublishSubcategory(category, selected = '') {
        const overlay = document.getElementById('prompt-library-publish-overlay');
        const wrap = overlay?.querySelector('[data-pl-publish-subcategory-wrap]');
        const select = overlay?.querySelector('[data-pl-publish-subcategory]');
        if (!wrap || !select) return;
        const options = this.subcategoryOptions(category);
        wrap.hidden = !options.length;
        select.disabled = !options.length;
        select.innerHTML = this.publicationSubcategoryOptions(category, selected);
    },

    openPublishDialog(id, trigger) {
        const item = this.find(id);
        if (!item) return;
        const overlay = this.publishDialog();
        const category = ['style', 'filter', 'function', 'other'].includes(item.category) ? item.category : 'other';
        this.publishItemId = item.id;
        this.publishTrigger = trigger || null;
        overlay.querySelector('[data-pl-publish-kicker]').textContent = t('library.publishKicker', '公开版本');
        overlay.querySelector('#prompt-publish-title').textContent = t('library.publishPrompt', '发布到灵感库');
        overlay.querySelector('#prompt-publish-hint').textContent = t('library.publishHint', '设置其他人在灵感库中看到的名称和类别；不会改动你的原提示词。');
        overlay.querySelector('[data-pl-publish-name-label]').textContent = t('library.publishName', '发布名称');
        overlay.querySelector('[data-pl-publish-category-label]').textContent = t('library.publishCategory', '发布类别');
        overlay.querySelector('[data-pl-publish-subcategory-label]').textContent = t('library.publishSubcategory', '发布子分类');
        const nameInput = overlay.querySelector('[data-pl-publish-name]');
        const categorySelect = overlay.querySelector('[data-pl-publish-category]');
        nameInput.value = item.name || '';
        nameInput.removeAttribute('aria-invalid');
        categorySelect.innerHTML = this.publicationCategoryOptions(category);
        this.updatePublishSubcategory(category, item.category === category ? item.subcategory : '');
        overlay.querySelector('[data-pl-publish-cancel]').textContent = t('library.publishCancel', '取消');
        overlay.querySelector('[data-pl-publish-confirm] span').textContent = t('library.publishConfirm', '确认发布');
        this.setPublishError('');
        overlay.classList.add('open');
        window.lucide?.createIcons();
        LibraryModalManager.trapFocus(overlay);
        requestAnimationFrame(() => nameInput.focus());
    },

    closePublishDialog({restoreFocus=true} = {}) {
        const overlay = document.getElementById('prompt-library-publish-overlay');
        const trigger = this.publishTrigger;
        this.publishItemId = '';
        this.publishTrigger = null;
        overlay?.classList.remove('open');
        if (this.overlay()?.classList.contains('open')) LibraryModalManager.trapFocus(this.overlay());
        if (restoreFocus && trigger?.isConnected) requestAnimationFrame(() => trigger.focus());
    },

    setPublishError(message) {
        const error = document.querySelector('#prompt-library-publish-overlay [data-pl-publish-error]');
        if (error) error.textContent = message || '';
    },

    async submitPublishDialog(event) {
        event.preventDefault();
        const id = this.publishItemId;
        const overlay = this.publishDialog();
        const nameInput = overlay.querySelector('[data-pl-publish-name]');
        const categorySelect = overlay.querySelector('[data-pl-publish-category]');
        const subcategorySelect = overlay.querySelector('[data-pl-publish-subcategory]');
        const name = nameInput.value.trim();
        const category = categorySelect.value;
        const subcategory = subcategorySelect?.disabled ? '' : String(subcategorySelect?.value || '');
        if (!name) {
            nameInput.setAttribute('aria-invalid', 'true');
            this.setPublishError(t('library.publishNameRequired', '请填写发布名称'));
            nameInput.focus();
            return;
        }
        if (!['style', 'filter', 'function', 'other'].includes(category)) {
            this.setPublishError(t('library.publishCategoryInvalid', '请选择有效的发布类别'));
            categorySelect.focus();
            return;
        }
        if (this.subcategoryOptions(category).length && !this.subcategoryOptions(category).some(([id]) => id === subcategory)) {
            this.setPublishError(t('library.publishSubcategoryInvalid', '请选择与发布类别匹配的子分类'));
            subcategorySelect?.focus();
            return;
        }
        nameInput.removeAttribute('aria-invalid');
        this.setPublishError('');
        const submit = overlay.querySelector('[data-pl-publish-confirm]');
        try {
            await this.withBusy(submit, () => this.publishItem(id, {name, category, subcategory}));
            this.closePublishDialog({restoreFocus:false});
        } catch (error) {
            this.setPublishError(error.message || t('library.publishFailed', '发布失败'));
        }
    },

    editorHtml(item) {
        const isNew = !item; item = item || { name:'', category:'other', subcategory:'', description:'', cover_url:'', prefix:'', suffix:'' };
        const assetOptions = this.assetCoverOptions(item.cover_url);
        const categoryOptions = [['style','风格'],['filter','滤镜'],['function','功能'],['other','其他']].map(([id,label]) => `<option value="${id}" ${item.category===id?'selected':''}>${label}</option>`).join('');
        const hasSubcategories = this.subcategoryOptions(item.category).length > 0;
        return `<section class="prompt-editor">
            <header class="prompt-editor-header"><button type="button" class="prompt-detail-back" data-pl-back><i data-lucide="arrow-left"></i>返回我的提示词</button><div><span>MY PROMPT PRESET</span><h2>${isNew ? '新建提示词' : '编辑提示词'}</h2><p>用前缀和后缀组成可重复使用的生成提示词结构。</p></div></header>
            <div class="prompt-editor-layout">
                <section class="prompt-editor-cover-panel"><div class="prompt-editor-cover-preview"><img data-pe-preview src="${LibraryUtils.escapeHtml(item.cover_url || '/static/images/logo.png')}" alt="封面预览"></div><div class="prompt-editor-cover-controls"><label class="prompt-editor-field-label">卡片封面</label><label class="prompt-editor-upload"><i data-lucide="upload"></i><span>上传封面</span><input type="file" data-pe-upload accept="image/*"></label><select data-pe-asset><option value="">从我的资产选择</option>${assetOptions}</select><input data-pe-cover value="${LibraryUtils.escapeHtml(item.cover_url || '')}" placeholder="站内 /assets/... 地址"></div></section>
                <section class="prompt-editor-basics"><label>名称<input data-pe-name value="${LibraryUtils.escapeHtml(item.name)}" maxlength="80" placeholder="例如：电影感肖像"></label><label>简短说明<input data-pe-description value="${LibraryUtils.escapeHtml(item.description || '')}" maxlength="180" placeholder="用一句话说明这个预设适合什么场景"></label><div class="prompt-editor-selects ${hasSubcategories ? '' : 'is-single'}" data-pe-selects><label>分类<select data-pe-category>${categoryOptions}</select></label><label class="prompt-editor-subcategory" data-pe-subcategory-wrap ${hasSubcategories ? '' : 'hidden'}>子分类<select data-pe-subcategory>${this.subcategoryOptionsHtml(item.category, item.subcategory)}</select></label></div></section>
            </div>
            <section class="prompt-editor-texts"><label><span>前缀</span><small>将在用户提示词前发送</small><textarea data-pe-prefix rows="8" placeholder="例如：cinematic portrait, soft rim light">${LibraryUtils.escapeHtml(item.prefix || item.positive || '')}</textarea></label><label><span>后缀</span><small>将在用户提示词后发送</small><textarea data-pe-suffix rows="8" placeholder="例如：fine film grain, high-end editorial finish">${LibraryUtils.escapeHtml(item.suffix || item.negative || '')}</textarea></label></section>
            <footer class="prompt-editor-actions"><button type="button" class="library-editor-btn" data-pl-back>取消</button><button type="button" class="library-editor-btn primary" data-pl-save="${item.id || ''}"><i data-lucide="save"></i>保存提示词</button></footer>
        </section>`;
    },
    subcategoryOptions(category) {
        return PROMPT_EDITOR_SUBCATEGORIES[category] || [];
    },
    subcategoryOptionsHtml(category, selected = '') {
        const options = this.subcategoryOptions(category);
        const normalizedSelected = String(selected || '');
        const hasSelectedOption = options.some(([id]) => id === normalizedSelected);
        const legacyOption = normalizedSelected && !hasSelectedOption
            ? `<option value="${LibraryUtils.escapeHtml(normalizedSelected)}" selected>已有分类：${LibraryUtils.escapeHtml(normalizedSelected)}</option>`
            : '';
        return `<option value="">不设置</option>${legacyOption}${options.map(([id, label]) => `<option value="${id}" ${id === normalizedSelected ? 'selected' : ''}>${label}</option>`).join('')}`;
    },
    assetCoverOptions(selected) {
        const own = window.AssetLibrary?.personalImageItems?.() || [];
        return own.slice(0, 100).map(item => `<option value="${LibraryUtils.escapeHtml(item.url || '')}" ${(item.url||'')===selected?'selected':''}>${LibraryUtils.escapeHtml(item.name || '素材')}</option>`).join('');
    },

    async uploadCover(file, root) {
        const form = new FormData(); form.append('files', file, file.name || 'prompt-cover');
        const response = await fetch('/api/ai/upload', { method:'POST', body:form });
        if (!response.ok) throw new Error('封面上传失败');
        const fileInfo = (await response.json()).files?.[0];
        if (!fileInfo?.url) throw new Error('封面上传失败');
        root.querySelector('[data-pe-cover]').value = fileInfo.url;
        root.querySelector('[data-pe-preview]').src = fileInfo.url;
    },

    async save(itemId, root) {
        const category = root.querySelector('[data-pe-category]').value;
        const subcategory = this.subcategoryOptions(category).length
            ? root.querySelector('[data-pe-subcategory]').value
            : '';
        const payload = {
            library_id: this.mineLibrary()?.id || '', name: root.querySelector('[data-pe-name]').value.trim() || '未命名提示词',
            category, subcategory,
            description: root.querySelector('[data-pe-description]').value.trim(), cover_url: root.querySelector('[data-pe-cover]').value.trim(),
            prefix: root.querySelector('[data-pe-prefix]').value.trim(), suffix: root.querySelector('[data-pe-suffix]').value.trim(),
        };
        if (!payload.prefix && !payload.suffix) throw new Error('请至少填写前缀或后缀');
        const url = itemId ? `/api/prompt-libraries/items/${encodeURIComponent(itemId)}` : '/api/prompt-libraries/items';
        const response = await fetch(url, { method:itemId?'PATCH':'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
        if (!response.ok) throw new Error((await response.text()) || '保存失败');
        this.editorId = ''; await this.load(); window.toast?.('已保存');
    },

    async toggleFavorite(id) {
        const active = this.favorites.has(id);
        const response = active ? await fetch(`/api/library/favorites/prompt/${encodeURIComponent(id)}`, {method:'DELETE'}) : await fetch('/api/library/favorites', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'prompt',item_id:id})});
        if (!response.ok) throw new Error('收藏操作失败'); this.favorites = new Set((await response.json()).favorites || []); this.render();
    },
    async withBusy(button, work) {
        if (button?.disabled) return;
        const previous = button?.innerHTML;
        if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); button.innerHTML = '<i data-lucide="loader-circle" class="is-spinning"></i>'; window.lucide?.createIcons(); }
        try { return await work(); }
        finally { if (button?.isConnected) { button.disabled = false; button.removeAttribute('aria-busy'); button.innerHTML = previous; window.lucide?.createIcons(); } }
    },
    async deleteItem(id) {
        if (!confirm(t('library.deletePromptConfirm', '确认删除这条提示词？删除后可在回收站恢复。'))) return;
        const response = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(id)}`, {method:'DELETE'});
        if (!response.ok) throw new Error((await response.text()) || t('library.deleteFailed', '删除失败'));
        await this.load();
        window.toast?.(t('library.promptMovedToTrash', '已移至回收站，可在回收站恢复'));
    },
    async publishItem(id, metadata = {}) {
        const response = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(id)}/publish`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({published:true, ...metadata})});
        if (!response.ok) throw new Error((await response.text()) || t('library.publishFailed', '发布失败'));
        await this.load();
        window.toast?.(t('library.publishedToast', '已发布到灵感库'));
    },
    async withdrawItem(id) {
        if (!confirm(t('library.withdrawPromptConfirm', '确认撤回这条已发布提示词？其他用户将不能再从灵感库使用它。'))) return;
        const response = await fetch(`/api/prompt-libraries/published/${encodeURIComponent(id)}`, {method:'DELETE'});
        if (!response.ok) throw new Error((await response.text()) || t('library.withdrawFailed', '撤回失败'));
        await this.load();
        window.toast?.(t('library.promptWithdrawnToast', '已从灵感库撤回'));
    },
    async apply(id) { const item=this.find(id); if(!item)return; const result=window.applyPromptPresetCard?.(item,this.targetId()); if(!result?.ok) throw new Error(result?.message || '应用失败'); if(result.targetId) window.setPromptLibraryTarget?.(result.targetId); this.render(); this.renderPreview(); },

    async handleClick(event) {
        const close = event.target.closest('.library-close-btn'); if (close) { this.closePreview({restoreFocus:false}); LibraryModalManager.closeAll(); window.clearPromptLibraryTarget?.(); return; }
        const previewClose = event.target.closest('[data-pl-preview-close]'); if(previewClose){ this.closePreview(); return; }
        const tab = event.target.closest('[data-pl-tab]'); if(tab){ this.tab=tab.dataset.plTab; this.category='all'; this.subcategory='real'; this.editorId=''; this.render(); return; }
        const category=event.target.closest('[data-pl-category]'); if(category){ this.category=category.dataset.plCategory; this.subcategory=this.category==='style'?'real':'all'; this.render(); return; }
        const sub=event.target.closest('[data-pl-subcategory]'); if(sub){this.subcategory=sub.dataset.plSubcategory;this.render();return;}
        const preview=event.target.closest('[data-pl-preview]'); if(preview){event.stopPropagation();this.openPreview(preview.dataset.plPreview,preview);return;}
        const back=event.target.closest('[data-pl-back]'); if(back){this.editorId='';this.render();return;}
        const newButton=event.target.closest('[data-pl-new]'); if(newButton){this.editorId='__new__';this.render();return;}
        const edit=event.target.closest('[data-pl-edit]'); if(edit){event.stopPropagation();this.openEditor(edit.dataset.plEdit);return;}
        const del=event.target.closest('[data-pl-delete]'); if(del){event.stopPropagation();try{await this.deleteItem(del.dataset.plDelete)}catch(error){window.toast?.(error.message)}return;}
        const publishCancel=event.target.closest('[data-pl-publish-cancel]'); if(publishCancel){event.stopPropagation();this.closePublishDialog();return;}
        const publish=event.target.closest('[data-pl-publish]'); if(publish){event.stopPropagation();this.openPublishDialog(publish.dataset.plPublish, publish);return;}
        const withdraw=event.target.closest('[data-pl-withdraw]'); if(withdraw){event.stopPropagation();try{await this.withBusy(withdraw,()=>this.withdrawItem(withdraw.dataset.plWithdraw))}catch(error){window.toast?.(error.message)}return;}
        const showPublication=event.target.closest('[data-pl-show-publication]'); if(showPublication){event.stopPropagation();this.tab='myPublished';this.render();requestAnimationFrame(()=>this.content()?.querySelector(`[data-pl-withdraw="${CSS.escape(showPublication.dataset.plShowPublication)}"]`)?.focus());return;}
        const fav=event.target.closest('[data-pl-favorite]'); if(fav){event.stopPropagation();try{await this.toggleFavorite(fav.dataset.plFavorite)}catch(error){window.toast?.(error.message)}return;}
        const apply=event.target.closest('[data-pl-apply]'); if(apply){try{await this.apply(apply.dataset.plApply)}catch(error){window.toast?.(error.message)}return;}
        const copy=event.target.closest('[data-pl-copy]'); if(copy){const item=this.find(copy.dataset.plCopy); const text=[item?.prefix||item?.positive,'用户提示词',item?.suffix||item?.negative].filter(Boolean).join('\n\n'); const copied = typeof copyTextToClipboard === 'function' ? await copyTextToClipboard(text) : false; this.showPreviewFeedback(copied ? t('smart.promptRecipeCopied', '已复制组合提示词') : t('smart.promptRecipeCopyFailed', '复制失败，请检查浏览器剪贴板权限'), !copied); return;}
        const save=event.target.closest('[data-pl-save]'); if(save){try{await this.withBusy(save,()=>this.save(save.dataset.plSave,event.target.closest('.prompt-editor')))}catch(error){window.toast?.(error.message)}return;}
        const upload=event.target.closest('[data-pe-upload]'); if(upload) return;
    },
    bindEditorInputs() {},
    loadingState() { const content=this.content(); if(content) content.innerHTML='<div class="library-loading"><i data-lucide="loader-2"></i><span>加载提示词库...</span></div>'; },
};

document.addEventListener('DOMContentLoaded', () => PromptLibrary.init());
window.openPromptLibrary = options => PromptLibrary.open(options || {});

document.addEventListener('change', async event => {
    const root=event.target.closest?.('.prompt-editor'); if(!root)return;
    if(event.target.matches('[data-pe-upload]') && event.target.files?.[0]) { try { await PromptLibrary.uploadCover(event.target.files[0],root); } catch(error) { window.toast?.(error.message); } }
    if(event.target.matches('[data-pe-asset]') && event.target.value) { root.querySelector('[data-pe-cover]').value=event.target.value; root.querySelector('[data-pe-preview]').src=event.target.value; }
    if(event.target.matches('[data-pe-cover]')) root.querySelector('[data-pe-preview]').src=event.target.value || '/static/images/logo.png';
    if(event.target.matches('[data-pe-category]')) {
        const subcategorySelect = root.querySelector('[data-pe-subcategory]');
        const wrap = root.querySelector('[data-pe-subcategory-wrap]');
        const selects = root.querySelector('[data-pe-selects]');
        const options = PromptLibrary.subcategoryOptions(event.target.value);
        if (subcategorySelect) subcategorySelect.innerHTML = PromptLibrary.subcategoryOptionsHtml(event.target.value);
        if (wrap) wrap.hidden = !options.length;
        if (selects) selects.classList.toggle('is-single', !options.length);
    }
});
