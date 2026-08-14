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
    initialized: false,
    loading: false,

    init() {
        if (this.initialized || !document.getElementById('prompts-library-overlay')) return;
        this.initialized = true;
        const overlay = document.getElementById('prompts-library-overlay');
        overlay.addEventListener('click', event => this.handleClick(event));
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || !this.previewId) return;
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
    allItems() { return (this.library?.libraries || []).flatMap(lib => (lib.items || []).map(item => ({ ...item, __libraryId: lib.id }))); },
    systemItems() { return this.allItems().filter(item => item.owner_type === 'system' || item.__libraryId === 'system'); },
    mineItems() { return this.mineLibrary()?.items || []; },
    find(id) { return this.allItems().find(item => item.id === id) || null; },
    targetId() { return window.getPromptLibraryTargetId?.() || String(window.__promptLibraryTargetId || ''); },
    activeTarget() { return typeof nodes !== 'undefined' ? nodes.find(node => node?.id === this.targetId()) : null; },

    filteredItems() {
        let items = this.tab === 'myPrompts' ? this.mineItems() : this.tab === 'favorites' ? this.systemItems().filter(item => this.favorites.has(item.id)) : this.systemItems();
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
        const button = `<button type="button" class="library-editor-btn primary prompt-library-header-create" data-pl-new><i data-lucide="plus"></i>新建提示词</button>`;
        if (closeButton) closeButton.insertAdjacentHTML('beforebegin', button);
        else headerActions.insertAdjacentHTML('beforeend', button);
    },
    tabsHtml() { return [
        ['inspiration','lightbulb','灵感库'], ['favorites','heart','我的收藏'], ['myPrompts','text-cursor-input','我的提示词']
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
        const preview = [item.prefix || item.positive, item.suffix || item.negative].filter(Boolean).join(' · ');
        const cover = item.cover_url ? `<img src="${LibraryUtils.escapeHtml(item.cover_url)}" alt="${LibraryUtils.escapeHtml(item.name || '')}" loading="lazy">` : `<div class="prompt-card-placeholder"><i data-lucide="sparkles"></i></div>`;
        const applyLabel = selected ? '取消' : '应用';
        const applyIcon = selected ? 'x' : 'plus';
        return `<article class="prompt-card ${selected ? 'is-selected' : ''}">
            <div class="prompt-card-cover">${cover}<div class="prompt-card-hover" aria-label="${LibraryUtils.escapeHtml(item.name || '提示词')} 操作"><button type="button" class="prompt-card-action prompt-card-apply ${selected ? 'is-applied' : ''}" data-pl-apply="${LibraryUtils.escapeHtml(item.id)}" aria-pressed="${selected ? 'true' : 'false'}"><i data-lucide="${applyIcon}"></i>${applyLabel}</button><button type="button" class="prompt-card-action" data-pl-preview="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="expand"></i>预览</button></div></div>
            <div class="prompt-card-info"><h3>${LibraryUtils.escapeHtml(item.name || '未命名')}</h3><p>${LibraryUtils.escapeHtml(item.description || '')}</p><div class="prompt-card-preview">${LibraryUtils.escapeHtml(LibraryUtils.truncate(preview, 96) || '（空提示词）')}</div></div>
            <div class="prompt-card-foot"><span>${LibraryUtils.escapeHtml(item.subcategory || item.category || '我的')}</span><button type="button" class="prompt-card-favorite ${favorite ? 'is-favorite' : ''}" data-pl-favorite="${LibraryUtils.escapeHtml(item.id)}" title="${favorite ? '取消收藏' : '收藏'}" aria-label="${favorite ? '取消收藏' : '收藏'}" aria-pressed="${favorite ? 'true' : 'false'}"><i data-lucide="heart"></i></button>${this.tab === 'myPrompts' ? `<button type="button" data-pl-edit="${LibraryUtils.escapeHtml(item.id)}" title="编辑"><i data-lucide="pencil"></i></button><button type="button" data-pl-delete="${LibraryUtils.escapeHtml(item.id)}" title="删除"><i data-lucide="trash-2"></i></button>` : ''}</div>
        </article>`;
    },

    renderContent() {
        const content = this.content(); if (!content) return;
        content.classList.remove('is-prompt-editor-view');
        if (this.editorId === '__new__' || this.editorId) { content.classList.add('is-prompt-editor-view'); content.innerHTML = this.editorHtml(this.editorId === '__new__' ? null : this.find(this.editorId)); window.lucide?.createIcons(); return; }
        const items = this.filteredItems();
        const grid = items.length ? `<div class="prompt-card-grid">${items.map(item => this.cardHtml(item)).join('')}</div>` : `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="text-cursor-input"></i></div><div class="library-empty-title">暂无提示词</div><div class="library-empty-desc">${this.tab === 'myPrompts' ? '新建一个预设，快速复用你的前缀与后缀。' : '尝试调整分类或搜索关键词。'}</div>${this.tab === 'myPrompts' ? '<button type="button" class="library-editor-btn primary" data-pl-new><i data-lucide="plus"></i>新建提示词</button>' : ''}</div>`;
        content.innerHTML = this.tab === 'inspiration' && this.category === 'style'
            ? `<div class="prompt-library-browser">${this.styleSidebarHtml()}<section class="prompt-library-results">${grid}</section></div>`
            : `<section class="prompt-library-results is-wide">${grid}</section>`;
        window.lucide?.createIcons();
    },

    detailHtml(item) {
        if (!item) return '';
        const example = [item.prefix || item.positive, '用户提示词', item.suffix || item.negative].filter(Boolean).join('\n\n');
        const selected = this.activeTarget() && Array.isArray(this.activeTarget().promptPresets) && this.activeTarget().promptPresets.some(preset => preset.id === item.id);
        const applyLabel = selected ? '取消应用' : '应用提示词';
        const applyIcon = selected ? 'x' : 'plus';
        return `<section class="prompt-detail"><header class="prompt-preview-header"><span>提示词预览</span><button type="button" class="prompt-preview-close" data-pl-preview-close aria-label="关闭预览"><i data-lucide="x"></i></button></header><div class="prompt-detail-grid"><div class="prompt-detail-cover">${item.cover_url ? `<img src="${LibraryUtils.escapeHtml(item.cover_url)}" alt="${LibraryUtils.escapeHtml(item.name || '')}">` : '<i data-lucide="sparkles"></i>'}</div><div><span class="prompt-detail-kicker">${LibraryUtils.escapeHtml(item.subcategory || item.category)}</span><h2>${LibraryUtils.escapeHtml(item.name)}</h2><p>${LibraryUtils.escapeHtml(item.description || '')}</p><div class="prompt-detail-field"><b>前缀</b><pre>${LibraryUtils.escapeHtml(item.prefix || item.positive || '')}</pre></div><div class="prompt-detail-field"><b>组合示例</b><pre>${LibraryUtils.escapeHtml(example)}</pre></div><div class="prompt-detail-field"><b>后缀</b><pre>${LibraryUtils.escapeHtml(item.suffix || item.negative || '')}</pre></div><div class="prompt-detail-actions"><button type="button" class="library-editor-btn" data-pl-copy="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="copy"></i>复制组合</button><button type="button" class="library-editor-btn prompt-preview-apply ${selected ? 'is-applied' : 'primary'}" data-pl-apply="${LibraryUtils.escapeHtml(item.id)}" aria-pressed="${selected ? 'true' : 'false'}"><i data-lucide="${applyIcon}"></i>${applyLabel}</button></div></div></div></section>`;
    },

    previewOverlay() {
        let overlay = document.getElementById('prompt-library-preview-overlay');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'prompt-library-preview-overlay';
        overlay.className = 'prompt-library-preview-overlay';
        overlay.innerHTML = '<div class="prompt-library-preview-modal" role="dialog" aria-modal="true" aria-label="提示词预览"><div class="prompt-library-preview-content"></div></div>';
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

    closePreview({restoreFocus=true} = {}) {
        const overlay = document.getElementById('prompt-library-preview-overlay');
        const trigger = this.previewTrigger;
        this.previewId = '';
        this.previewTrigger = null;
        overlay?.classList.remove('open');
        if (this.overlay()?.classList.contains('open')) LibraryModalManager.trapFocus(this.overlay());
        if (restoreFocus && trigger?.isConnected) requestAnimationFrame(() => trigger.focus());
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
    async deleteItem(id) { if (!confirm('确认删除这条提示词？')) return; const response = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(id)}`, {method:'DELETE'}); if (!response.ok) throw new Error('删除失败'); await this.load(); },
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
        const edit=event.target.closest('[data-pl-edit]'); if(edit){event.stopPropagation();this.editorId=edit.dataset.plEdit;this.render();return;}
        const del=event.target.closest('[data-pl-delete]'); if(del){event.stopPropagation();try{await this.deleteItem(del.dataset.plDelete)}catch(error){window.toast?.(error.message)}return;}
        const fav=event.target.closest('[data-pl-favorite]'); if(fav){event.stopPropagation();try{await this.toggleFavorite(fav.dataset.plFavorite)}catch(error){window.toast?.(error.message)}return;}
        const apply=event.target.closest('[data-pl-apply]'); if(apply){try{await this.apply(apply.dataset.plApply)}catch(error){window.toast?.(error.message)}return;}
        const copy=event.target.closest('[data-pl-copy]'); if(copy){const item=this.find(copy.dataset.plCopy); const text=[item?.prefix||item?.positive,'用户提示词',item?.suffix||item?.negative].filter(Boolean).join('\n\n');try{await navigator.clipboard.writeText(text);window.toast?.('已复制组合提示词')}catch(_){window.toast?.('复制失败')}return;}
        const save=event.target.closest('[data-pl-save]'); if(save){try{await this.save(save.dataset.plSave,event.target.closest('.prompt-editor'))}catch(error){window.toast?.(error.message)}return;}
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
