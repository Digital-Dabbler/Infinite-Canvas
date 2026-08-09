/* ============================================
   Workflow Library - 工作流库弹窗逻辑
   灵感库 / 我的收藏 / 我的工作流
   ============================================ */

const WorkflowLibrary = {
    tab: 'inspiration',
    category: 'all',
    search: '',
    library: null,
    favorites: new Set(),
    initialized: false,
    loading: false,
    PRESET_WORKFLOW_CATEGORIES: [
        { id: 'all', name: t('library.category.all', '全部') },
        { id: 'image', name: t('library.category.imageGen', '图片生成') },
        { id: 'video', name: t('library.category.videoGen', '视频生成') },
        { id: 'prompt', name: t('library.category.llm', '提示词') },
        { id: 'loop', name: t('library.category.loop', '循环') }
    ],

    init() {
        if (!document.getElementById('workflows-library-overlay') || this.initialized) return;
        this.initialized = true;
        this.bindTabs();
        this.bindClose();
        this.bindContent();
        LibrarySearch.setup('workflows-library-overlay', value => {
            this.search = String(value || '').trim().toLowerCase();
            this.render();
        });
        this.load();
    },

    bindTabs() {
        document.querySelectorAll('#workflows-library-overlay .library-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('#workflows-library-overlay .library-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.tab = tab.dataset.tab || 'inspiration';
                this.category = 'all';
                LibrarySearch.clear('workflows-library-overlay');
                this.search = '';
                this.render();
            });
        });
    },

    bindClose() {
        const btn = document.querySelector('#workflows-library-overlay .library-close-btn');
        btn?.addEventListener('click', () => LibraryModalManager.closeAll());
    },

    bindContent() {
        const overlay = document.querySelector('#workflows-library-overlay');
        overlay?.addEventListener('click', e => {
            const actionBtn = e.target.closest('[data-action]');
            if (actionBtn) {
                const action = actionBtn.dataset.action;
                const id = actionBtn.dataset.id || '';
                if (action === 'apply') this.handleApply(id);
                else if (action === 'favorite') this.toggleFavorite(id);
                else if (action === 'publish') this.togglePublish(id);
                else if (action === 'rename') this.renameItem(id);
                else if (action === 'delete') this.deleteItem(id);
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
            const res = await fetch('/api/asset-library');
            if (!res.ok) throw new Error('加载工作流库失败');
            const data = await res.json();
            this.library = data?.library || { libraries: [] };
            const favRes = await fetch('/api/library/favorites?kind=workflow');
            if (favRes.ok) {
                const favData = await favRes.json();
                this.favorites = new Set(favData.favorites || []);
            }
            this.render();
        } catch (err) {
            console.warn('[WorkflowLibrary]', err);
            const content = document.querySelector('#workflows-library-overlay .library-content');
            if (content) {
                content.innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="alert-circle"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(err.message || '加载失败')}</div></div>`;
                if (window.lucide) lucide.createIcons();
            }
        } finally {
            this.loading = false;
        }
    },

    showLoading() {
        const content = document.querySelector('#workflows-library-overlay .library-content');
        if (!content) return;
        content.innerHTML = `<div class="library-loading"><i data-lucide="loader-2"></i><span>${LibraryUtils.escapeHtml(t('library.loading', '加载中...'))}</span></div>`;
        if (window.lucide) lucide.createIcons();
    },

    userId() {
        return String(LibraryShared.getUser()?.id || '');
    },

    allItems() {
        const items = [];
        (this.library?.libraries || []).forEach(lib => {
            (lib?.categories || []).forEach(cat => {
                (cat?.items || []).forEach(item => {
                    if (String(item.kind || '') !== 'workflow' && String(item.type || '') !== 'workflow') return;
                    const canManage = item.owner_type === 'user' && String(item.owner_id || '') === this.userId();
                    items.push({
                        ...item,
                        __libraryId: lib.id,
                        __categoryName: cat.name || '工作流',
                        __librarySystem: Boolean(lib.system || lib.owner_type === 'system'),
                        can_manage: canManage
                    });
                });
            });
        });
        return items;
    },

    isOwnItem(item) {
        return item.owner_type === 'user' && String(item.owner_id || '') === this.userId();
    },

    inspirationItems() {
        return this.allItems().filter(item =>
            (item.__librarySystem || item.published === true) && !this.isOwnItem(item)
        );
    },

    myItems() {
        return this.allItems().filter(item => this.isOwnItem(item));
    },

    tabItems() {
        let items = [];
        if (this.tab === 'favorites') items = this.allItems().filter(item => this.favorites.has(item.id));
        else if (this.tab === 'myWorkflows') items = this.myItems();
        else items = this.inspirationItems();
        return items;
    },

    currentItems() {
        let items = this.tabItems();
        if (this.category !== 'all') {
            items = items.filter(item => this.workflowMatchesCategory(item, this.category));
        }
        if (this.search) {
            const q = this.search;
            items = items.filter(item => [item.name, item.__categoryName, item.owner_name].join(' ').toLowerCase().includes(q));
        }
        return items;
    },

    categories() {
        if (this.tab === 'inspiration') {
            return this.PRESET_WORKFLOW_CATEGORIES.map(c => ({ ...c }));
        }
        return [{ id: 'all', name: t('library.category.all', '全部') }];
    },

    workflowMatchesCategory(item, catId) {
        if (catId === 'all') return true;
        const text = [item.name, item.__categoryName, item.format].join(' ').toLowerCase();
        const keywords = {
            image: ['图片', 'image', '生成'],
            video: ['视频', 'video'],
            prompt: ['提示词', 'prompt', 'llm'],
            loop: ['循环', 'loop']
        };
        const keywordList = keywords[catId] || [];
        return keywordList.some(keyword => text.includes(keyword));
    },

    findItem(id) {
        return this.allItems().find(item => item.id === id) || null;
    },

    render() {
        this.renderCategories();
        this.renderContent();
    },

    renderCategories() {
        const bar = document.querySelector('#workflows-library-overlay .library-categories');
        if (!bar) return;
        bar.innerHTML = this.categories().map(cat =>
            `<button type="button" class="library-category ${cat.id === this.category ? 'active' : ''}" data-category="${LibraryUtils.escapeHtml(cat.id)}">${LibraryUtils.escapeHtml(cat.name)}</button>`
        ).join('');
        if (window.lucide) lucide.createIcons();
    },

    renderContent() {
        const content = document.querySelector('#workflows-library-overlay .library-content');
        if (!content) return;
        const items = this.currentItems();
        if (!items.length) {
            content.innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="workflow"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(t('library.empty', '暂无内容'))}</div><div class="library-empty-desc">${LibraryUtils.escapeHtml(this.emptyHint())}</div></div>`;
            if (window.lucide) lucide.createIcons();
            return;
        }
        content.innerHTML = `<div class="library-grid">${items.map(item => this.cardHtml(item)).join('')}</div>`;
        if (window.lucide) lucide.createIcons();
    },

    emptyHint() {
        if (this.tab === 'inspiration') return '灵感库暂无工作流';
        if (this.tab === 'favorites') return '还没有收藏，去灵感库看看吧';
        return '可以从画布导出工作流到库，再发布到灵感库';
    },

    cardHtml(item) {
        const isFav = this.favorites.has(item.id);
        const isMine = this.tab === 'myWorkflows';
        const badges = [];
        if (isFav) badges.push(`<span class="library-card-badge success"><i data-lucide="heart"></i>${LibraryUtils.escapeHtml(t('library.favorite', '已收藏'))}</span>`);
        if (item.published) badges.push(`<span class="library-card-badge"><i data-lucide="send"></i>${LibraryUtils.escapeHtml(t('library.published', '已发布'))}</span>`);
        const actions = [];
        actions.push(`<button type="button" class="library-card-action primary" data-action="apply" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="plus"></i>${LibraryUtils.escapeHtml(t('library.apply', '应用'))}</button>`);
        if (this.tab !== 'myWorkflows') {
            actions.push(`<button type="button" class="library-card-action" data-action="favorite" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="${isFav ? 'heart-off' : 'heart'}"></i>${LibraryUtils.escapeHtml(isFav ? t('library.unfavorite', '取消收藏') : t('library.favorite', '收藏'))}</button>`);
        }
        if (isMine && this.isOwnItem(item)) {
            actions.push(`<button type="button" class="library-card-action" data-action="publish" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="send"></i>${LibraryUtils.escapeHtml(item.published ? t('library.unpublish', '取消发布') : t('library.publish', '发布'))}</button>`);
            actions.push(`<button type="button" class="library-card-action" data-action="rename" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="pencil"></i>${LibraryUtils.escapeHtml(t('library.edit', '重命名'))}</button>`);
        }
        if (this.isOwnItem(item)) {
            actions.push(`<button type="button" class="library-card-action danger" data-action="delete" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="trash-2"></i>${LibraryUtils.escapeHtml(t('library.delete', '删除'))}</button>`);
        }
        const sizeText = item.size ? `${(item.size / 1024).toFixed(1)} KB` : '';
        const countText = item.node_count ? `${item.node_count} 节点` : '';
        return `<div class="library-card workflow-library-card">
            <div class="library-card-cover">
                <div class="workflow-cover-placeholder"><i data-lucide="workflow"></i></div>
                ${badges.join('')}
            </div>
            <div class="library-card-body">
                <div class="library-card-title">${LibraryUtils.escapeHtml(item.name || t('library.untitled', '未命名'))}</div>
                <div class="workflow-meta">
                    <span>${LibraryUtils.escapeHtml(item.__categoryName || '工作流')}</span>
                    ${countText ? `<span class="workflow-node-count"><i data-lucide="boxes"></i>${LibraryUtils.escapeHtml(countText)}</span>` : ''}
                    ${sizeText ? `<span>${LibraryUtils.escapeHtml(sizeText)}</span>` : ''}
                </div>
            </div>
            <div class="library-card-actions">${actions.join('')}</div>
        </div>`;
    },

    async handleApply(id) {
        const item = this.findItem(id);
        if (!item) return;
        if (!window.applyLibraryWorkflow) {
            toast(t('library.canvasNotReady', '请先打开画布'));
            return;
        }
        await window.applyLibraryWorkflow(item);
        LibraryModalManager.closeAll();
    },

    async toggleFavorite(id) {
        const item = this.findItem(id);
        if (!item) return;
        const isFav = this.favorites.has(id);
        try {
            let res;
            if (isFav) {
                res = await fetch(`/api/library/favorites/workflow/${encodeURIComponent(id)}`, { method: 'DELETE' });
            } else {
                res = await fetch('/api/library/favorites', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind: 'workflow', item_id: id })
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
            const res = await fetch(`/api/asset-library/items/${encodeURIComponent(id)}/publish`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ published: !item.published })
            });
            if (!res.ok) throw new Error('操作失败');
            await this.load();
            toast(item.published ? t('library.unpublished', '已取消发布') : t('library.publishedToast', '已发布到灵感库'));
        } catch (err) {
            toast(err.message || '操作失败');
        }
    },

    async renameItem(id) {
        const item = this.findItem(id);
        if (!item) return;
        const name = prompt(t('library.renamePrompt', '请输入新的工作流名称'), item.name || '');
        if (!name || name === item.name) return;
        try {
            const res = await fetch(`/api/asset-library/items/${encodeURIComponent(id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            if (!res.ok) throw new Error('重命名失败');
            await this.load();
            toast(t('library.renamed', '已重命名'));
        } catch (err) {
            toast(err.message || '重命名失败');
        }
    },

    async deleteItem(id) {
        const item = this.findItem(id);
        if (!item) return;
        if (!confirm(t('library.deleteWorkflowConfirm', '确认删除这个工作流？'))) return;
        try {
            const res = await fetch('/api/asset-library/items/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: [id] })
            });
            if (!res.ok) throw new Error('删除失败');
            await this.load();
            toast(t('library.deleted', '已删除'));
        } catch (err) {
            toast(err.message || '删除失败');
        }
    },

};

document.addEventListener('DOMContentLoaded', () => WorkflowLibrary.init());
window.openWorkflowLibrary = () => {
    LibraryModalManager.open('workflows');
    WorkflowLibrary.load();
};
