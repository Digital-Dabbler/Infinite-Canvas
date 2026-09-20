/* ============================================
   Workflow Library - 工作流库
   发布/取消发布交互与提示词库（prompt-library.js）保持一致。
   ============================================ */
const WorkflowLibrary = {
    tab: 'inspiration',
    search: '',
    library: null,
    favorites: new Set(),
    initialized: false,
    loading: false,

    overlay() { return document.getElementById('workflows-library-overlay'); },
    content() { return this.overlay()?.querySelector('.library-content'); },

    init() {
        const overlay = this.overlay();
        if (!overlay || this.initialized) return;
        this.initialized = true;
        overlay.querySelectorAll('.library-tab').forEach(tab => tab.addEventListener('click', () => {
            this.tab = tab.dataset.tab || 'inspiration';
            this.syncTabs();
            this.render();
        }));
        overlay.querySelector('.library-close-btn')?.addEventListener('click', () => LibraryModalManager.closeAll());
        overlay.addEventListener('click', event => this.handleClick(event));
        LibrarySearch.setup('workflows-library-overlay', value => {
            this.search = String(value || '').trim().toLowerCase();
            this.render();
        });
        this.load();
    },

    syncTabs() {
        this.overlay()?.querySelectorAll('.library-tab').forEach(tab => {
            tab.classList.toggle('active', (tab.dataset.tab || 'inspiration') === this.tab);
        });
    },

    async load() {
        if (this.loading) return;
        this.loading = true;
        try {
            const [libraryResponse, favoritesResponse] = await Promise.all([
                fetch('/api/workflow-library'),
                fetch('/api/library/favorites?kind=workflow'),
            ]);
            if (!libraryResponse.ok) throw new Error(t('library.loadFailed', '加载失败'));
            this.library = await libraryResponse.json();
            if (favoritesResponse.ok) this.favorites = new Set((await favoritesResponse.json()).favorites || []);
            this.render();
        } catch (error) {
            const content = this.content();
            if (content) content.innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="alert-circle"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(error.message || t('library.loadFailed', '加载失败'))}</div></div>`;
            window.lucide?.createIcons();
        } finally {
            this.loading = false;
        }
    },

    publishedItems() { return Array.isArray(this.library?.published) ? this.library.published : []; },
    publishedForSource(id) { return this.publishedItems().find(item => item.source_workflow_id === id) || null; },
    publishedMeta(item) {
        const timestamp = Number(item?.published_at || 0);
        return timestamp ? new Date(timestamp).toLocaleDateString() : '';
    },

    items() {
        const data = this.library || {};
        let out = this.tab === 'myWorkflows' ? (data.workflows || [])
            : this.tab === 'myPublished' ? this.publishedItems()
            : this.tab === 'favorites' ? (data.inspiration || []).filter(item => this.favorites.has(item.id))
            : (data.inspiration || []);
        if (this.search) out = out.filter(item => [item.name, item.owner_name].join(' ').toLowerCase().includes(this.search));
        return out;
    },

    emptyState() {
        if (this.search) return this.emptyHtml('search', t('library.empty', '暂无内容'), t('library.workflowEmptySearch', '换个分类或搜索关键词试试。'));
        const states = {
            myWorkflows: ['workflow', t('library.myWorkflows', '我的工作流'), t('library.workflowEmptyMine', '在画布中框选节点并创建工作流后，会出现在这里。')],
            myPublished: ['send', t('library.myPublished', '我的发布'), t('library.workflowEmptyPublished', '在“我的工作流”中发布后，会在这里管理公开版本。')],
            favorites: ['heart', t('library.myFavorites', '我的收藏'), t('library.workflowEmptyFavorites', '收藏灵感库中的工作流后，会出现在这里。')],
        };
        const [icon, title, desc] = states[this.tab] || ['workflow', t('library.inspiration', '灵感库'), t('library.workflowEmptyInspiration', '还没有人发布工作流。')];
        return this.emptyHtml(icon, title, desc);
    },

    emptyHtml(icon, title, desc) {
        return `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="${icon}"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(title)}</div><div class="library-empty-desc">${LibraryUtils.escapeHtml(desc)}</div></div>`;
    },

    render() {
        const content = this.content();
        if (!content) return;
        this.syncTabs();
        const items = this.items();
        content.innerHTML = items.length
            ? `<div class="library-grid workflow-library-grid">${items.map(item => this.card(item)).join('')}</div>`
            : this.emptyState();
        window.lucide?.createIcons();
    },

    card(item) {
        const mine = this.tab === 'myWorkflows';
        const published = this.tab === 'myPublished';
        const id = LibraryUtils.escapeHtml(item.id);
        const publication = mine ? this.publishedForSource(item.id) : null;
        const cover = item.cover_url
            ? `<img src="${LibraryUtils.escapeHtml(item.cover_url)}" alt="${LibraryUtils.escapeHtml(item.name || '')}" loading="lazy" onerror="window.libraryCoverFallback?.(this, 'workflow', 'workflow-cover-placeholder')">`
            : '<div class="workflow-cover-placeholder"><i data-lucide="workflow"></i></div>';
        const applyAction = `<button type="button" data-wf-action="apply" data-id="${id}">${LibraryUtils.escapeHtml(t('library.apply', '应用'))}</button>`;
        let actions;
        let ariaLabel;
        if (published) {
            ariaLabel = t('library.publishedWorkflowActions', '已发布工作流操作');
            actions = `${applyAction}<button type="button" class="danger" data-wf-action="withdraw" data-id="${id}"><i data-lucide="rotate-ccw"></i><span>${LibraryUtils.escapeHtml(t('library.unpublish', '取消发布'))}</span></button>`;
        } else if (mine) {
            ariaLabel = t('library.workflowManageActions', '工作流管理操作');
            const publishAction = publication
                ? `<button type="button" class="is-published" data-wf-action="show-publication" data-id="${LibraryUtils.escapeHtml(publication.id)}" title="${LibraryUtils.escapeHtml(t('library.showPublication', '查看公开版本'))}"><i data-lucide="check-circle-2"></i><span>${LibraryUtils.escapeHtml(t('library.published', '已发布'))}</span></button>`
                : `<button type="button" class="publish" data-wf-action="publish" data-id="${id}"><i data-lucide="send"></i><span>${LibraryUtils.escapeHtml(t('library.publish', '发布'))}</span></button>`;
            actions = `${applyAction}${publishAction}<button type="button" class="danger" data-wf-action="delete" data-id="${id}" title="${LibraryUtils.escapeHtml(t('library.delete', '删除'))}" aria-label="${LibraryUtils.escapeHtml(t('library.delete', '删除'))}"><i data-lucide="trash-2"></i></button>`;
        } else {
            ariaLabel = t('library.workflowActions', '工作流操作');
            const favorite = this.favorites.has(item.id);
            actions = `${applyAction}<button type="button" class="${favorite ? 'is-favorite' : ''}" data-wf-action="favorite" data-id="${id}">${LibraryUtils.escapeHtml(favorite ? t('library.unfavorite', '取消收藏') : t('library.favorite', '收藏'))}</button>`;
        }
        const meta = [
            `<span>${Number(item.node_count) || 0} ${LibraryUtils.escapeHtml(t('library.workflowNodeCount', '节点'))}</span>`,
            `<span>${Number(item.resource_count) || 0} ${LibraryUtils.escapeHtml(t('library.workflowResourceCount', '资源'))}</span>`,
            published && this.publishedMeta(item) ? `<span>${LibraryUtils.escapeHtml(`${t('library.publishedAt', '发布于')} ${this.publishedMeta(item)}`)}</span>` : '',
        ].join('');
        return `<div class="library-card workflow-library-card"><div class="library-card-cover">${cover}</div><div class="library-card-body"><div class="library-card-title">${LibraryUtils.escapeHtml(item.name || t('library.untitled', '未命名'))}</div><div class="workflow-meta">${meta}</div></div><div class="library-card-actions workflow-card-actions${published ? ' is-managed' : ''}" role="group" aria-label="${LibraryUtils.escapeHtml(ariaLabel)}">${actions}</div></div>`;
    },

    async withBusy(button, work) {
        if (button?.disabled) return;
        const previous = button?.innerHTML;
        if (button) {
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            button.innerHTML = '<i data-lucide="loader-circle" class="is-spinning"></i>';
            window.lucide?.createIcons();
        }
        try {
            return await work();
        } finally {
            if (button?.isConnected) {
                button.disabled = false;
                button.removeAttribute('aria-busy');
                button.innerHTML = previous;
                window.lucide?.createIcons();
            }
        }
    },

    async errorDetail(response, fallback) {
        try {
            const detail = (await response.json())?.detail;
            if (detail) return String(detail);
        } catch (error) { /* response body is not JSON */ }
        return fallback;
    },

    async consume(response) {
        const payload = await response.json().catch(() => null);
        if (payload?.library) this.library = payload.library;
        this.render();
        return payload;
    },

    async toggleFavorite(id) {
        const active = this.favorites.has(id);
        const response = active
            ? await fetch(`/api/library/favorites/workflow/${encodeURIComponent(id)}`, { method: 'DELETE' })
            : await fetch('/api/library/favorites', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'workflow', item_id: id }) });
        if (!response.ok) throw new Error(t('library.favoriteFailed', '收藏操作失败'));
        this.favorites = new Set((await response.json()).favorites || []);
        this.render();
    },

    async publish(id) {
        if (!confirm(t('library.publishWorkflowConfirm', '确认把这个工作流发布到灵感库？其他人可以在灵感库中查看和复制它。'))) return;
        const response = await fetch(`/api/workflow-library/${encodeURIComponent(id)}/publish`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ published: true }) });
        if (!response.ok) throw new Error(await this.errorDetail(response, t('library.publishFailed', '发布失败')));
        await this.consume(response);
        window.toast?.(t('library.publishedToast', '已发布到灵感库'));
    },

    async unpublish(id) {
        if (!confirm(t('library.unpublishWorkflowConfirm', '确认取消发布这个工作流？其他人将不能再从灵感库查看或复制它。'))) return;
        const response = await fetch(`/api/workflow-library/published/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(await this.errorDetail(response, t('library.withdrawFailed', '撤回失败')));
        await this.consume(response);
        window.toast?.(t('library.workflowUnpublishedToast', '已取消发布，该工作流已从灵感库移除'));
    },

    async deleteItem(id) {
        if (!confirm(t('library.deleteWorkflowConfirm', '确认删除这个工作流？'))) return;
        const response = await fetch(`/api/workflow-library/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(await this.errorDetail(response, t('library.deleteFailed', '删除失败')));
        await this.consume(response);
        window.toast?.(t('library.deleted', '已删除'));
    },

    async apply(id) {
        const item = this.items().find(candidate => candidate.id === id);
        if (!item || !window.applyLibraryWorkflow) return;
        await window.applyLibraryWorkflow(item);
        LibraryModalManager.closeAll();
    },

    showPublication(snapshotId) {
        this.tab = 'myPublished';
        this.render();
        requestAnimationFrame(() => this.content()?.querySelector(`[data-wf-action="withdraw"][data-id="${CSS.escape(snapshotId)}"]`)?.focus());
    },

    async handleClick(event) {
        const button = event.target.closest('[data-wf-action]');
        if (!button) return;
        const id = button.dataset.id;
        const action = button.dataset.wfAction;
        try {
            if (action === 'apply') { await this.apply(id); return; }
            if (action === 'favorite') { await this.toggleFavorite(id); return; }
            if (action === 'show-publication') { this.showPublication(id); return; }
            if (action === 'publish') { await this.withBusy(button, () => this.publish(id)); return; }
            if (action === 'withdraw') { await this.withBusy(button, () => this.unpublish(id)); return; }
            if (action === 'delete') { await this.deleteItem(id); }
        } catch (error) {
            window.toast?.(error.message || t('library.withdrawFailed', '撤回失败'));
        }
    },
};
document.addEventListener('DOMContentLoaded', () => WorkflowLibrary.init());
window.openWorkflowLibrary = () => { LibraryModalManager.open('workflows'); WorkflowLibrary.load(); };
