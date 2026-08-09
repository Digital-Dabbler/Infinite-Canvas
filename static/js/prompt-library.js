/* ============================================
   Prompt Library - 提示词库弹窗逻辑
   灵感库 / 我的收藏 / 我的提示词
   ============================================ */

const PromptLibrary = {
    tab: 'inspiration',
    category: 'all',
    search: '',
    library: null,
    favorites: new Set(),
    editingId: '',
    choiceNodeId: '',
    initialized: false,
    loading: false,
    PRESET_INSPIRATION_CATEGORIES: [
        { id: 'all', name: t('library.category.all', '全部') },
        { id: 'hot_selling', name: t('library.category.hotSelling', '热门带货') },
        { id: 'viral_case', name: t('library.category.viralCase', '爆款案例') },
        { id: 'social_media', name: t('library.category.socialMedia', '种草新媒体') },
        { id: 'industry', name: t('library.category.industry', '行业定制') },
        { id: 'digital_virtual', name: t('library.category.digitalVirtual', '数字虚拟') },
        { id: 'copywriting', name: t('library.category.copywriting', '文案策划') },
        { id: 'graphic_design', name: t('library.category.graphicDesign', '平面设计') },
        { id: 'short_video', name: t('library.category.shortVideo', '短视频内容') },
        { id: 'ecommerce', name: t('library.category.ecommerce', '电商详情') }
    ],
    init() {
        if (!document.getElementById('prompts-library-overlay') || this.initialized) return;
        this.initialized = true;
        this.bindTabs();
        this.bindClose();
        this.bindContent();
        LibrarySearch.setup('prompts-library-overlay', value => {
            this.search = String(value || '').trim().toLowerCase();
            this.render();
        });
        this.load();
    },

    bindTabs() {
        document.querySelectorAll('#prompts-library-overlay .library-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('#prompts-library-overlay .library-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.tab = tab.dataset.tab || 'inspiration';
                this.category = 'all';
                this.editingId = '';
                this.choiceNodeId = '';
                this.choiceItem = '';
                LibrarySearch.clear('prompts-library-overlay');
                this.search = '';
                this.render();
            });
        });
    },

    bindClose() {
        const btn = document.querySelector('#prompts-library-overlay .library-close-btn');
        btn?.addEventListener('click', () => LibraryModalManager.closeAll());
    },

    bindContent() {
        const overlay = document.querySelector('#prompts-library-overlay');
        overlay?.addEventListener('click', e => {
            const actionBtn = e.target.closest('[data-action]');
            if (actionBtn) {
                const action = actionBtn.dataset.action;
                const id = actionBtn.dataset.id || '';
                if (action === 'apply') this.handleApply(id);
                else if (action === 'favorite') this.toggleFavorite(id);
                else if (action === 'publish') this.togglePublish(id);
                else if (action === 'edit') this.openEditor(id);
                else if (action === 'delete') this.deleteItem(id);
                return;
            }
            const choiceBtn = e.target.closest('[data-choice]');
            if (choiceBtn) {
                const mode = choiceBtn.dataset.choice;
                if (mode === 'cancel') {
                    this.choiceNodeId = '';
                    this.choiceItem = '';
                    this.render();
                    return;
                }
                const item = this.findItem(this.choiceItem || '');
                const text = item?.positive || '';
                if (window.applyLibraryPromptToNode) {
                    window.applyLibraryPromptToNode(text, mode, this.choiceNodeId);
                    this.choiceNodeId = '';
                    this.choiceItem = '';
                    LibraryModalManager.closeAll();
                }
                return;
            }
            const catBtn = e.target.closest('[data-category]');
            if (catBtn) {
                this.category = catBtn.dataset.category || 'all';
                this.renderCategories();
                this.renderContent();
            }
        });
    },

    async load() {
        if (this.loading) return;
        this.loading = true;
        this.showLoading();
        try {
            let res = await fetch('/api/prompt-libraries');
            if (!res.ok) throw new Error('加载提示词库失败');
            let data = await res.json();
            this.library = data?.library || { libraries: [], viewer: {} };
            if (!this.myLibrary()) {
                await fetch('/api/prompt-libraries', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: '{}'
                }).catch(() => {});
                res = await fetch('/api/prompt-libraries');
                data = await res.json();
                this.library = data?.library || this.library;
            }
            const favRes = await fetch('/api/library/favorites?kind=prompt');
            if (favRes.ok) {
                const favData = await favRes.json();
                this.favorites = new Set(favData.favorites || []);
            }
            this.render();
        } catch (err) {
            console.warn('[PromptLibrary]', err);
            const content = document.querySelector('#prompts-library-overlay .library-content');
            if (content) {
                content.innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="alert-circle"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(err.message || '加载失败')}</div></div>`;
                if (window.lucide) lucide.createIcons();
            }
        } finally {
            this.loading = false;
        }
    },

    showLoading() {
        const content = document.querySelector('#prompts-library-overlay .library-content');
        if (!content) return;
        content.innerHTML = `<div class="library-loading"><i data-lucide="loader-2"></i><span>${LibraryUtils.escapeHtml(t('library.loading', '加载中...'))}</span></div>`;
        if (window.lucide) lucide.createIcons();
    },

    viewer() {
        return this.library?.viewer || { user_id: '', is_admin: false };
    },

    canManageItem(item) {
        const uid = String(this.viewer().user_id || '');
        return item.owner_type === 'user' && String(item.owner_id || '') === uid;
    },

    myLibrary() {
        const uid = String(this.viewer().user_id || '');
        return (this.library?.libraries || []).find(lib =>
            lib?.personal && String(lib.owner_id || '') === uid
        ) || null;
    },

    allItems() {
        const items = [];
        (this.library?.libraries || []).forEach(lib => {
            (lib?.items || []).forEach(item => {
                items.push({ ...item, __libraryId: lib.id });
            });
        });
        return items;
    },

    inspirationItems() {
        const uid = String(this.viewer().user_id || '');
        return this.allItems().filter(item =>
            item.published !== false
            && !(item.owner_type === 'user' && String(item.owner_id || '') === uid)
        );
    },

    myItems() {
        const lib = this.myLibrary();
        return lib?.items || [];
    },

    tabItems() {
        let items = [];
        if (this.tab === 'favorites') items = this.allItems().filter(item => this.favorites.has(item.id));
        else if (this.tab === 'myPrompts') items = this.myItems();
        else items = this.inspirationItems();
        return items;
    },

    currentItems() {
        let items = this.tabItems();
        if (this.category !== 'all') {
            items = items.filter(item => String(item.category || 'custom') === this.category);
        }
        if (this.search) {
            const q = this.search;
            items = items.filter(item => [
                item.name, item.positive, item.scene, item.owner_name
            ].join(' ').toLowerCase().includes(q));
        }
        return items;
    },

    categories() {
        if (this.tab === 'inspiration') {
            return this.PRESET_INSPIRATION_CATEGORIES.map(c => ({ ...c }));
        }
        return [{ id: 'all', name: t('library.category.all', '全部') }];
    },

    findItem(id) {
        return this.allItems().find(item => item.id === id) || null;
    },

    render() {
        this.renderCategories();
        this.renderContent();
    },

    renderCategories() {
        const bar = document.querySelector('#prompts-library-overlay .library-categories');
        if (!bar) return;
        bar.innerHTML = this.categories().map(cat =>
            `<button type="button" class="library-category ${cat.id === this.category ? 'active' : ''}" data-category="${LibraryUtils.escapeHtml(cat.id)}">${LibraryUtils.escapeHtml(cat.name)}</button>`
        ).join('');
        if (window.lucide) lucide.createIcons();
    },

    renderContent() {
        const content = document.querySelector('#prompts-library-overlay .library-content');
        if (!content) return;
        if (this.editingId) {
            content.innerHTML = this.editorHtml();
            if (window.lucide) lucide.createIcons();
            return;
        }
        if (this.choiceNodeId) {
            content.innerHTML = this.choiceHtml();
            if (window.lucide) lucide.createIcons();
            return;
        }
        const items = this.currentItems();
        if (!items.length) {
            content.innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="text-cursor-input"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(t('library.empty', '暂无内容'))}</div><div class="library-empty-desc">${LibraryUtils.escapeHtml(this.emptyHint())}</div></div>`;
            if (window.lucide) lucide.createIcons();
            return;
        }
        content.innerHTML = `<div class="library-grid">${items.map(item => this.cardHtml(item)).join('')}</div>`;
        if (window.lucide) lucide.createIcons();
    },

    emptyHint() {
        if (this.tab === 'inspiration') return '灵感库暂无提示词';
        if (this.tab === 'favorites') return '还没有收藏，去灵感库看看吧';
        return '在我的提示词库中创建提示词后，可发布到灵感库';
    },

    presetCategoryLabel(id) {
        if (!id) return '';
        const labels = {
            hot_selling: t('library.category.hotSelling', '热门带货'),
            viral_case: t('library.category.viralCase', '爆款案例'),
            social_media: t('library.category.socialMedia', '种草新媒体'),
            industry: t('library.category.industry', '行业定制'),
            digital_virtual: t('library.category.digitalVirtual', '数字虚拟'),
            copywriting: t('library.category.copywriting', '文案策划'),
            graphic_design: t('library.category.graphicDesign', '平面设计'),
            short_video: t('library.category.shortVideo', '短视频内容'),
            ecommerce: t('library.category.ecommerce', '电商详情')
        };
        return labels[id] || '';
    },

    cardHtml(item) {
        const isFav = this.favorites.has(item.id);
        const isMine = this.tab === 'myPrompts';
        const presetLabel = this.presetCategoryLabel(item.category);
        const badges = [];
        if (isFav) badges.push(`<span class="library-card-badge success"><i data-lucide="heart"></i>${LibraryUtils.escapeHtml(t('library.favorite', '已收藏'))}</span>`);
        if (item.published) badges.push(`<span class="library-card-badge"><i data-lucide="send"></i>${LibraryUtils.escapeHtml(t('library.published', '已发布'))}</span>`);
        const actions = [];
        actions.push(`<button type="button" class="library-card-action primary" data-action="apply" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="plus"></i>${LibraryUtils.escapeHtml(t('library.apply', '应用'))}</button>`);
        if (this.tab !== 'myPrompts') {
            actions.push(`<button type="button" class="library-card-action" data-action="favorite" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="${isFav ? 'heart-off' : 'heart'}"></i>${LibraryUtils.escapeHtml(isFav ? t('library.unfavorite', '取消收藏') : t('library.favorite', '收藏'))}</button>`);
        }
        if (isMine && this.canManageItem(item)) {
            actions.push(`<button type="button" class="library-card-action" data-action="publish" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="send"></i>${LibraryUtils.escapeHtml(item.published ? t('library.unpublish', '取消发布') : t('library.publish', '发布'))}</button>`);
            actions.push(`<button type="button" class="library-card-action" data-action="edit" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="pencil"></i>${LibraryUtils.escapeHtml(t('library.edit', '编辑'))}</button>`);
        }
        if (this.canManageItem(item)) {
            actions.push(`<button type="button" class="library-card-action danger" data-action="delete" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="trash-2"></i>${LibraryUtils.escapeHtml(t('library.delete', '删除'))}</button>`);
        }
        const preview = String(item.positive || '').trim();
        return `<div class="library-card prompt-library-card">
            <div class="library-card-cover">
                <div class="prompt-preview">${LibraryUtils.escapeHtml(LibraryUtils.truncate(preview, 180) || t('library.emptyPrompt', '（空提示词）'))}</div>
                ${badges.join('')}
            </div>
            <div class="library-card-body">
                <div class="library-card-title">${LibraryUtils.escapeHtml(item.name || t('library.untitled', '未命名'))}</div>
                <div class="library-card-meta">
                    ${presetLabel ? `<span>${presetLabel}</span>` : ''}
                    ${item.owner_name ? `<span>${LibraryUtils.escapeHtml(item.owner_name)}</span>` : ''}
                </div>
            </div>
            <div class="library-card-actions">${actions.join('')}</div>
        </div>`;
    },

    editorHtml() {
        const item = this.findItem(this.editingId);
        if (!item) {
            this.editingId = '';
            return '';
        }
        const categoryOptions = this.PRESET_INSPIRATION_CATEGORIES
            .filter(c => c.id !== 'all')
            .map(c => ({ id: c.id, name: c.name }));
        if (!categoryOptions.some(c => c.id === item.category) && item.category) {
            categoryOptions.push({ id: item.category, name: item.category });
        }
        const cats = categoryOptions.map(cat =>
            `<option value="${LibraryUtils.escapeHtml(cat.id)}" ${cat.id === item.category ? 'selected' : ''}>${LibraryUtils.escapeHtml(cat.name)}</option>`
        ).join('');
        return `<div class="library-editor-panel">
            <div class="library-editor-title"><i data-lucide="pencil"></i>${LibraryUtils.escapeHtml(t('library.editPrompt', '编辑提示词'))}</div>
            <label class="library-editor-field"><span>${LibraryUtils.escapeHtml(t('library.name', '名称'))}</span><input data-edit-name type="text" value="${LibraryUtils.escapeHtml(item.name || '')}"></label>
            <label class="library-editor-field"><span>${LibraryUtils.escapeHtml(t('library.categoryLabel', '分类'))}</span><select data-edit-category>${cats}</select></label>
            <label class="library-editor-field"><span>${LibraryUtils.escapeHtml(t('library.scene', '场景'))}</span><textarea data-edit-scene rows="2">${LibraryUtils.escapeHtml(item.scene || '')}</textarea></label>
            <label class="library-editor-field"><span>${LibraryUtils.escapeHtml(t('library.positive', '正向提示词'))}</span><textarea data-edit-positive rows="5">${LibraryUtils.escapeHtml(item.positive || '')}</textarea></label>
            <label class="library-editor-field"><span>${LibraryUtils.escapeHtml(t('library.negative', '负向提示词'))}</span><textarea data-edit-negative rows="3">${LibraryUtils.escapeHtml(item.negative || '')}</textarea></label>
            <div class="library-editor-actions">
                <button type="button" class="library-editor-btn" data-edit-cancel>${LibraryUtils.escapeHtml(t('common.cancel', '取消'))}</button>
                <button type="button" class="library-editor-btn primary" data-edit-save>${LibraryUtils.escapeHtml(t('common.save', '保存'))}</button>
            </div>
        </div>`;
    },

    choiceHtml() {
        const item = this.findItem(this.choiceItem || '');
        const preview = String(item?.positive || '').trim();
        return `<div class="library-choice-panel">
            <div class="library-choice-title"><i data-lucide="mouse-pointer-click"></i>${LibraryUtils.escapeHtml(t('library.applyChoiceTitle', '提示词应用到画布'))}</div>
            <div class="library-choice-desc">${LibraryUtils.escapeHtml(t('library.applyChoiceDesc', '已选中提示词节点，选择插入方式'))}</div>
            <div class="library-choice-preview">${LibraryUtils.escapeHtml(LibraryUtils.truncate(preview, 200))}</div>
            <div class="library-choice-actions">
                <button type="button" class="library-editor-btn" data-choice="cancel">${LibraryUtils.escapeHtml(t('common.cancel', '取消'))}</button>
                <button type="button" class="library-editor-btn" data-choice="append">${LibraryUtils.escapeHtml(t('library.append', '追加'))}</button>
                <button type="button" class="library-editor-btn primary" data-choice="replace">${LibraryUtils.escapeHtml(t('library.replace', '替换'))}</button>
            </div>
        </div>`;
    },

    openEditor(id) {
        this.editingId = id;
        this.choiceNodeId = '';
        this.render();
        const saveBtn = document.querySelector('#prompts-library-overlay [data-edit-save]');
        const cancelBtn = document.querySelector('#prompts-library-overlay [data-edit-cancel]');
        saveBtn?.addEventListener('click', () => this.saveItem());
        cancelBtn?.addEventListener('click', () => {
            this.editingId = '';
            this.render();
        });
    },

    async saveItem() {
        const item = this.findItem(this.editingId);
        if (!item) return;
        const root = document.querySelector('#prompts-library-overlay .library-editor-panel');
        if (!root) return;
        const payload = {
            library_id: String(item.__libraryId || ''),
            name: root.querySelector('[data-edit-name]')?.value || item.name,
            category: root.querySelector('[data-edit-category]')?.value || item.category,
            scene: root.querySelector('[data-edit-scene]')?.value || '',
            positive: root.querySelector('[data-edit-positive]')?.value || '',
            negative: root.querySelector('[data-edit-negative]')?.value || '',
        };
        try {
            const res = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(item.id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error('保存失败');
            this.editingId = '';
            await this.load();
            toast(t('common.saved', '已保存'));
        } catch (err) {
            toast(err.message || '保存失败');
        }
    },

    async handleApply(id) {
        const item = this.findItem(id);
        if (!item) return;
        const text = String(item.positive || '').trim();
        if (!text) {
            toast(t('library.emptyPromptToast', '该提示词内容为空'));
            return;
        }
        if (!window.applyLibraryPrompt) {
            toast(t('library.canvasNotReady', '请先打开画布'));
            return;
        }
        const result = window.applyLibraryPrompt(text);
        if (result?.selected) {
            this.choiceNodeId = result.selected;
            this.choiceItem = id;
            this.render();
        } else if (result?.created) {
            LibraryModalManager.closeAll();
        }
    },

    async toggleFavorite(id) {
        const item = this.findItem(id);
        if (!item) return;
        const isFav = this.favorites.has(id);
        try {
            let res;
            if (isFav) {
                res = await fetch(`/api/library/favorites/prompt/${encodeURIComponent(id)}`, { method: 'DELETE' });
            } else {
                res = await fetch('/api/library/favorites', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind: 'prompt', item_id: id })
                });
            }
            if (!res.ok) throw new Error('操作失败');
            const data = await res.json();
            this.favorites = new Set(data.favorites || []);
            this.render();
            toast(isFav ? t('library.unfavorited', '已取消收藏') : t('library.favorited', '已收藏'));
        } catch (err) {
            toast(err.message || '操作失败');
        }
    },

    async togglePublish(id) {
        const item = this.findItem(id);
        if (!item) return;
        try {
            const res = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    library_id: String(item.__libraryId || ''),
                    name: item.name || '提示词',
                    category: item.category || 'custom',
                    positive: item.positive || '',
                    negative: item.negative || '',
                    scene: item.scene || '',
                    published: !item.published
                })
            });
            if (!res.ok) throw new Error('操作失败');
            await this.load();
            toast(item.published ? t('library.unpublished', '已取消发布') : t('library.publishedToast', '已发布到灵感库'));
        } catch (err) {
            toast(err.message || '操作失败');
        }
    },

    async deleteItem(id) {
        const item = this.findItem(id);
        if (!item) return;
        if (!confirm(t('library.deleteConfirm', '确认删除这条提示词？'))) return;
        try {
            const res = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(id)}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('删除失败');
            await this.load();
            toast(t('library.deleted', '已删除'));
        } catch (err) {
            toast(err.message || '删除失败');
        }
    },

};

document.addEventListener('DOMContentLoaded', () => PromptLibrary.init());
window.openPromptLibrary = () => {
    LibraryModalManager.open('prompts');
    PromptLibrary.load();
};
