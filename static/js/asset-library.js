/* ============================================
   Asset Library - 资产库弹窗逻辑
   我的资产 / 素材库 / 虚拟人像库
   上传到我的资产 + 文件夹管理 + 时间筛选
   ============================================ */

const AssetLibrary = {
    tab: 'myAssets',
    category: 'all',
    search: '',
    timeFilter: 'all',
    manageMode: false,
    browseFolderId: '',
    folderDeleteTarget: '',
    selectedAssetIds: new Set(),
    library: null,
    initialized: false,
    loading: false,
    uploadState: null,
    PRESET_IDS: ['video', 'scene', 'ui', 'icon', 'clothing', 'portrait', 'other'],
    PRESET_CATEGORIES: {
        myAssets: ['all', 'video', 'scene', 'ui', 'icon', 'clothing', 'portrait', 'other'],
        material: ['all', 'video', 'scene', 'ui', 'icon', 'clothing', 'portrait', 'other'],
        avatar: ['all', 'characterSets', 'characters']
    },
    TIME_OPTIONS: [
        { id: 'all', name: () => t('library.timeAll', '全部时间') },
        { id: 'today', name: () => t('library.timeToday', '今天') },
        { id: '7d', name: () => t('library.time7d', '最近7天') },
        { id: '30d', name: () => t('library.time30d', '最近30天') },
        { id: 'year', name: () => t('library.timeYear', '今年') }
    ],

    init() {
        if (!document.getElementById('assets-library-overlay') || this.initialized) return;
        this.initialized = true;
        this.bindTabs();
        this.bindClose();
        this.bindContent();
        this.bindUploadDialog();
        LibrarySearch.setup('assets-library-overlay', value => {
            this.search = String(value || '').trim().toLowerCase();
            this.renderContent();
        });
        this.load();
    },

    bindTabs() {
        document.querySelectorAll('#assets-library-overlay .library-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('#assets-library-overlay .library-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.tab = tab.dataset.tab || 'myAssets';
                this.category = 'all';
                this.manageMode = false;
                this.browseFolderId = '';
                this.selectedAssetIds.clear();
                LibrarySearch.clear('assets-library-overlay');
                this.search = '';
                this.render();
            });
        });
    },

    bindClose() {
        const btn = document.querySelector('#assets-library-overlay .library-close-btn');
        btn?.addEventListener('click', () => LibraryModalManager.closeAll());
    },

    bindContent() {
        const overlay = document.querySelector('#assets-library-overlay');
        overlay?.addEventListener('click', e => {
            const actionBtn = e.target.closest('[data-action]');
            if (actionBtn) {
                const action = actionBtn.dataset.action;
                const id = actionBtn.dataset.id || '';
                if (action === 'apply') this.handleApply(id);
                else if (action === 'delete') this.deleteItem(id);
                return;
            }
            const catBtn = e.target.closest('[data-category]');
            if (catBtn) {
                this.category = catBtn.dataset.category || 'all';
                this.renderCategories();
                this.renderContent();
                return;
            }
            const renameBtn = e.target.closest('[data-folder-rename]');
            if (renameBtn) {
                this.promptForRenameFolder(renameBtn.dataset.folderRename || '');
                return;
            }
            const deleteBtn = e.target.closest('[data-folder-delete]');
            if (deleteBtn) {
                this.confirmDeleteFolder(deleteBtn.dataset.folderDelete || '');
                return;
            }
            const openFolder = e.target.closest('[data-open-folder]');
            if (openFolder) {
                this.browseFolderId = openFolder.dataset.openFolder || '';
                this.renderContent();
                return;
            }
            if (e.target.closest('[data-folder-back]')) {
                this.browseFolderId = '';
                this.renderContent();
                return;
            }
            if (e.target.closest('[data-toolbar-new-folder]')) {
                this.promptForNewFolder();
                return;
            }
            if (e.target.closest('[data-toolbar-manage]')) {
                this.toggleManage();
                return;
            }
            if (e.target.closest('[data-folder-delete-cancel]')) {
                this.folderDeleteTarget = '';
                this.renderContent();
                return;
            }
            if (e.target.closest('[data-folder-delete-confirm]')) {
                const folderId = this.folderDeleteTarget;
                this.folderDeleteTarget = '';
                this.deleteFolder(folderId);
                return;
            }
            if (e.target.closest('[data-manage-select-all]')) {
                this.toggleSelectAllAssets();
                return;
            }
            const checkEl = e.target.closest('[data-asset-check]');
            if (checkEl) {
                this.toggleAssetSelection(checkEl.dataset.assetCheck || '');
                return;
            }
            if (e.target.closest('[data-manage-done]')) {
                this.toggleManage(false);
                return;
            }
            if (e.target.closest('[data-manage-delete]')) {
                this.batchDeleteAssets();
            }
        });
        overlay?.addEventListener('change', e => {
            if (e.target.matches('[data-time-filter]')) {
                this.timeFilter = e.target.value || 'all';
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
            if (!res.ok) throw new Error('加载资产库失败');
            const data = await res.json();
            this.library = data?.library || { libraries: [] };
            this.render();
        } catch (err) {
            console.warn('[AssetLibrary]', err);
            const content = document.querySelector('#assets-library-overlay .library-content');
            if (content) {
                content.innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="alert-circle"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(err.message || '加载失败')}</div></div>`;
                if (window.lucide) lucide.createIcons();
            }
        } finally {
            this.loading = false;
        }
    },

    showLoading() {
        const content = document.querySelector('#assets-library-overlay .library-content');
        if (!content) return;
        content.innerHTML = `<div class="library-loading"><i data-lucide="loader-2"></i><span>${LibraryUtils.escapeHtml(t('library.loading', '加载中...'))}</span></div>`;
        if (window.lucide) lucide.createIcons();
    },

    userId() {
        const fromViewer = String(this.library?.viewer?.user_id || '');
        if (fromViewer) return fromViewer;
        return String(LibraryShared.getUser()?.id || '');
    },

    personalLibrary() {
        const uid = this.userId();
        return (this.library?.libraries || []).find(lib =>
            lib?.personal && String(lib.owner_id || '') === uid
        ) || null;
    },

    findFolder(folderId) {
        const lib = this.personalLibrary();
        return (lib?.categories || []).find(cat => cat.id === folderId) || null;
    },

    personalImageFolders() {
        const lib = this.personalLibrary();
        return (lib?.categories || []).filter(cat => (cat.type || 'image') === 'image');
    },

    folderMatchesCategory(folder, catId) {
        if (catId === 'all') return true;
        return (folder.items || []).some(item => this.assetMatchesCategory(item, catId));
    },

    folderMatchesTime(folder) {
        if (this.timeFilter === 'all') return true;
        return (folder.items || []).some(item => this.timeMatches(item));
    },

    allImageItems() {
        const items = [];
        (this.library?.libraries || []).forEach(lib => {
            (lib?.categories || []).forEach(cat => {
                if (String(cat.type || 'image') !== 'image') return;
                (cat?.items || []).forEach(item => {
                    const canManage = item.owner_type === 'user' && String(item.owner_id || '') === this.userId();
                    items.push({
                        ...item,
                        __libraryId: lib.id,
                        __categoryId: cat.id,
                        __categoryName: cat.name || '素材',
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

    myItems() {
        return this.allImageItems().filter(item => this.isOwnItem(item));
    },

    materialItems() {
        return this.allImageItems().filter(item => item.__librarySystem);
    },

    avatarItems() {
        return this.allImageItems().filter(item =>
            item.__librarySystem
            && item.registrations && typeof item.registrations === 'object'
            && Object.keys(item.registrations).length > 0
        );
    },

    tabItems() {
        if (this.tab === 'material') return this.materialItems();
        if (this.tab === 'avatar') return this.avatarItems();
        return this.myItems();
    },

    timeMatches(item) {
        if (this.timeFilter === 'all') return true;
        const created = Number(item.created_at || 0);
        if (!created) return false;
        const now = Date.now();
        const startOfToday = new Date();
        startOfToday.setHours(0, 0, 0, 0);
        if (this.timeFilter === 'today') return created >= startOfToday.getTime();
        if (this.timeFilter === '7d') return created >= now - 7 * 86400000;
        if (this.timeFilter === '30d') return created >= now - 30 * 86400000;
        if (this.timeFilter === 'year') {
            const startOfYear = new Date(new Date().getFullYear(), 0, 1).getTime();
            return created >= startOfYear;
        }
        return true;
    },

    currentItems() {
        let items = this.tabItems();
        if (this.tab === 'myAssets' && this.browseFolderId) {
            items = items.filter(item => String(item.__categoryId || '') === this.browseFolderId);
        }
        if (this.category !== 'all') {
            items = items.filter(item => this.assetMatchesCategory(item, this.category));
        }
        if (this.tab === 'myAssets' && this.timeFilter !== 'all') {
            items = items.filter(item => this.timeMatches(item));
        }
        if (this.search) {
            const q = this.search;
            items = items.filter(item => [item.name, item.__categoryName, item.url].join(' ').toLowerCase().includes(q));
        }
        return items;
    },

    categories() {
        const ids = this.PRESET_CATEGORIES[this.tab] || ['all'];
        const names = {
            all: t('library.category.all', '全部'),
            video: t('library.category.video', '视频'),
            scene: t('library.category.scene', '场景'),
            ui: t('library.category.ui', '界面'),
            icon: t('library.category.icon', '符号'),
            clothing: t('library.category.clothing', '服装'),
            portrait: t('library.category.portrait', '立绘'),
            other: t('library.category.other', '其他'),
            characterSets: t('library.characterSets', '角色套图'),
            characters: t('library.characters', '角色')
        };
        return ids.map(id => ({ id, name: names[id] || id }));
    },

    presetCategoryLabel(id) {
        if (!id || !this.PRESET_IDS.includes(String(id))) return '';
        const names = {
            video: t('library.category.video', '视频'),
            scene: t('library.category.scene', '场景'),
            ui: t('library.category.ui', '界面'),
            icon: t('library.category.icon', '符号'),
            clothing: t('library.category.clothing', '服装'),
            portrait: t('library.category.portrait', '立绘'),
            other: t('library.category.other', '其他')
        };
        return names[id] || id;
    },

    itemKind(item) {
        const kind = String(item?.kind || item?.type || '').toLowerCase();
        const url = String(item?.url || '').toLowerCase();
        if (kind.includes('video') || /\.(mp4|webm|mov|m4v|mkv)(\?|#|$)/.test(url)) return 'video';
        if (kind.includes('audio') || /\.(mp3|wav|flac|ogg|m4a|aac)(\?|#|$)/.test(url)) return 'audio';
        return 'image';
    },

    assetMatchesCategory(item, catId) {
        if (catId === 'all') return true;
        if (catId === 'video') return this.itemKind(item) === 'video';
        const stored = String(item.category || '');
        if (this.PRESET_IDS.includes(stored)) {
            if (catId === 'other') return stored === 'other';
            return stored === catId;
        }
        const text = [item.name, item.__categoryName].join(' ').toLowerCase();
        const keywords = {
            scene: ['场景', 'scene', '环境', '室内', '室外'],
            ui: ['界面', 'ui', '交互'],
            icon: ['符号', 'icon', '图标'],
            clothing: ['服装', 'clothing', '服饰', '衣服'],
            portrait: ['立绘', 'portrait', '人物'],
            characterSets: ['套图', '角色套图', 'character set', '三视图', '多视图'],
            characters: ['角色', 'character', '人物']
        };
        if (catId === 'other') {
            const others = ['video', 'scene', 'ui', 'icon', 'clothing', 'portrait'];
            return !others.some(id => this.assetMatchesCategory(item, id));
        }
        const keywordsList = keywords[catId] || [];
        return keywordsList.some(keyword => text.includes(keyword));
    },

    findItem(id) {
        return this.allImageItems().find(item => item.id === id) || null;
    },

    render() {
        this.renderToolbar();
        this.renderCategories();
        this.renderContent();
    },

    renderToolbar() {
        const bar = document.querySelector('#assets-library-overlay .library-toolbar');
        if (!bar) return;
        if (this.tab !== 'myAssets') {
            bar.innerHTML = '';
            bar.classList.remove('show');
            return;
        }
        bar.classList.add('show');
        const timeOptions = this.TIME_OPTIONS.map(option =>
            `<option value="${option.id}" ${option.id === this.timeFilter ? 'selected' : ''}>${LibraryUtils.escapeHtml(option.name())}</option>`
        ).join('');
        bar.innerHTML = `
            <div class="library-toolbar-left">
                <select class="library-time-filter" data-time-filter>${timeOptions}</select>
            </div>
            <div class="library-toolbar-right" style="display:flex;gap:8px;">
                <button type="button" class="library-toolbar-btn" data-toolbar-new-folder><i data-lucide="folder-plus"></i>${LibraryUtils.escapeHtml(t('library.newFolder', '新建文件夹'))}</button>
                <button type="button" class="library-toolbar-btn ${this.manageMode ? 'active' : ''}" data-toolbar-manage><i data-lucide="settings-2"></i>${LibraryUtils.escapeHtml(this.manageMode ? t('library.manageDone', '完成') : t('library.manage', '管理'))}</button>
            </div>`;
        if (window.lucide) lucide.createIcons();
    },

    renderCategories() {
        const bar = document.querySelector('#assets-library-overlay .library-categories');
        if (!bar) return;
        bar.innerHTML = this.categories().map(cat =>
            `<button type="button" class="library-category ${cat.id === this.category ? 'active' : ''}" data-category="${LibraryUtils.escapeHtml(cat.id)}">${LibraryUtils.escapeHtml(cat.name)}</button>`
        ).join('');
        if (window.lucide) lucide.createIcons();
    },

    renderContent() {
        const content = document.querySelector('#assets-library-overlay .library-content');
        if (!content) return;
        if (this.folderDeleteTarget) {
            const folder = this.findFolder(this.folderDeleteTarget);
            content.innerHTML = `<div class="library-choice-panel">
                <div class="library-choice-title"><i data-lucide="folder-x"></i>${LibraryUtils.escapeHtml(t('library.deleteFolder', '删除文件夹'))}</div>
                <div class="library-choice-desc">${LibraryUtils.escapeHtml(folder ? `「${folder.name || '素材'}」` : '')}${LibraryUtils.escapeHtml(t('library.folderDeletePanelDesc', '将连同里面的素材一起删除（可进回收站恢复）'))}</div>
                <div class="library-choice-actions">
                    <button type="button" class="library-editor-btn" data-folder-delete-cancel>${LibraryUtils.escapeHtml(t('common.cancel', '取消'))}</button>
                    <button type="button" class="library-editor-btn danger" data-folder-delete-confirm>${LibraryUtils.escapeHtml(t('library.confirmDelete', '确认删除'))}</button>
                </div>
            </div>`;
            if (window.lucide) lucide.createIcons();
            return;
        }
        if (this.tab === 'myAssets' && !this.browseFolderId) {
            this.renderFolderRoot();
            return;
        }
        const items = this.currentItems();
        if (!items.length) {
            const backRow = (this.tab === 'myAssets' && this.browseFolderId)
                ? this.renderFolderBackRow()
                : '';
            content.innerHTML = `${backRow}<div class="library-empty"><div class="library-empty-icon"><i data-lucide="archive"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(t('library.empty', '暂无内容'))}</div><div class="library-empty-desc">${LibraryUtils.escapeHtml(this.emptyHint())}</div></div>`;
            if (window.lucide) lucide.createIcons();
            return;
        }
        const allSelected = items.length > 0 && items.every(item => this.selectedAssetIds.has(item.id));
        const manageBar = (this.manageMode && this.tab === 'myAssets')
            ? `<div class="library-manage-bar">
                <span class="library-manage-count">${LibraryUtils.escapeHtml(t('library.selectedCount', '已选 {n} 项').replace('{n}', this.selectedAssetIds.size))}</span>
                <button type="button" class="library-editor-btn" data-manage-select-all>${LibraryUtils.escapeHtml(allSelected ? t('library.deselectAll', '取消全选') : t('library.selectAll', '全选'))}</button>
                <button type="button" class="library-editor-btn danger" data-manage-delete ${this.selectedAssetIds.size ? '' : 'disabled'}><i data-lucide="trash-2"></i>${LibraryUtils.escapeHtml(t('library.batchDelete', '批量删除'))}</button>
                <button type="button" class="library-editor-btn primary" data-manage-done>${LibraryUtils.escapeHtml(t('library.manageDone', '完成'))}</button>
            </div>`
            : '';
        const backRow = (this.tab === 'myAssets' && this.browseFolderId)
            ? this.renderFolderBackRow()
            : '';
        content.innerHTML = `${backRow}<div class="library-grid">${items.map(item => this.cardHtml(item)).join('')}</div>${manageBar}`;
        if (window.lucide) lucide.createIcons();
    },

    renderFolderRoot() {
        const content = document.querySelector('#assets-library-overlay .library-content');
        if (!content) return;
        let folders = this.personalImageFolders();
        if (this.search) {
            const q = this.search;
            folders = folders.filter(folder => String(folder.name || '').toLowerCase().includes(q));
        }
        if (this.category !== 'all') {
            folders = folders.filter(folder => this.folderMatchesCategory(folder, this.category));
        }
        if (this.timeFilter !== 'all') {
            folders = folders.filter(folder => this.folderMatchesTime(folder));
        }
        if (!folders.length) {
            const filtered = this.category !== 'all' || this.timeFilter !== 'all' || this.search;
            const title = filtered
                ? t('library.noMatchingFolders', '没有符合条件的文件夹')
                : t('library.noFolders', '还没有文件夹');
            const desc = filtered
                ? t('library.noMatchingFoldersHint', '换个分类、时间或关键词试试')
                : t('library.noFoldersHint', '点右上角“新建文件夹”创建，上传时选择它作为保存位置');
            const cta = filtered ? '' : `<button type="button" class="library-empty-btn" data-toolbar-new-folder><i data-lucide="folder-plus"></i>${LibraryUtils.escapeHtml(t('library.newFolder', '新建文件夹'))}</button>`;
            content.innerHTML = `<div class="library-empty"><div class="library-empty-icon"><i data-lucide="folder"></i></div><div class="library-empty-title">${LibraryUtils.escapeHtml(title)}</div><div class="library-empty-desc">${LibraryUtils.escapeHtml(desc)}</div>${cta}</div>`;
            if (window.lucide) lucide.createIcons();
            return;
        }
        content.innerHTML = `<div class="library-grid library-folder-grid">${folders.map(folder => {
            const count = folder.items?.length || 0;
            const manageActions = this.manageMode ? `<div class="library-card-actions">
                <button type="button" class="library-card-action" data-folder-rename="${LibraryUtils.escapeHtml(folder.id)}"><i data-lucide="pencil"></i>${LibraryUtils.escapeHtml(t('library.renameFolder', '重命名'))}</button>
                <button type="button" class="library-card-action danger" data-folder-delete="${LibraryUtils.escapeHtml(folder.id)}"><i data-lucide="trash-2"></i>${LibraryUtils.escapeHtml(t('library.deleteFolder', '删除'))}</button>
            </div>` : '';
            return `<div class="library-card library-folder-card" data-open-folder="${LibraryUtils.escapeHtml(folder.id)}">
                <div class="library-card-cover">
                    <div class="placeholder">
                        <i data-lucide="folder"></i>
                    </div>
                </div>
                <div class="library-card-body">
                    <div class="library-card-title">${LibraryUtils.escapeHtml(folder.name || '素材')}</div>
                    <div class="library-card-meta"><span>${LibraryUtils.escapeHtml(t('library.folderItemCount', '{n} 个素材').replace('{n}', count))}</span></div>
                </div>
                ${manageActions}
            </div>`;
        }).join('')}</div>`;
        if (window.lucide) lucide.createIcons();
    },

    renderFolderBackRow() {
        const folder = this.personalImageFolders().find(item => item.id === this.browseFolderId);
        return `<div class="library-folder-back">
            <button type="button" class="library-folder-back-btn" data-folder-back><i data-lucide="arrow-left"></i>${LibraryUtils.escapeHtml(t('library.backToFolders', '返回文件夹'))}</button>
            <span class="library-folder-back-name">${LibraryUtils.escapeHtml(folder?.name || '')}</span>
        </div>`;
    },

    emptyHint() {
        if (this.tab === 'material') return '素材库暂无内容';
        if (this.tab === 'avatar') return '虚拟人像库暂无内容';
        return '右键画布中的图片或视频，选择“加入我的资产”即可上传到这里';
    },

    thumbHtml(item) {
        const kind = String(item.kind || item.type || 'image');
        const url = String(item.url || '');
        if (kind === 'video' || /\.(mp4|webm|mov|m4v|mkv)(\?|#|$)/i.test(url)) {
            return `<video src="${LibraryUtils.escapeHtml(url)}" muted preload="metadata" playsinline></video>`;
        }
        if (kind === 'audio' || /\.(mp3|wav|m4a|aac|ogg|flac)(\?|#|$)/i.test(url)) {
            return `<div class="placeholder"><i data-lucide="file-audio"></i></div>`;
        }
        return `<img src="${LibraryUtils.escapeHtml(url)}" alt="${LibraryUtils.escapeHtml(item.name || 'asset')}" loading="lazy" decoding="async">`;
    },

    cardHtml(item) {
        const manageCheck = (this.manageMode && this.tab === 'myAssets')
            ? `<span class="asset-manage-check ${this.selectedAssetIds.has(item.id) ? 'checked' : ''}" data-asset-check="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="check"></i></span>`
            : '';
        const presetLabel = this.presetCategoryLabel(item.category);
        const actions = [];
        actions.push(`<button type="button" class="library-card-action primary" data-action="apply" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="plus"></i>${LibraryUtils.escapeHtml(t('library.addToCanvas', '添加到画布'))}</button>`);
        if (this.isOwnItem(item) && !this.manageMode) {
            actions.push(`<button type="button" class="library-card-action danger" data-action="delete" data-id="${LibraryUtils.escapeHtml(item.id)}"><i data-lucide="trash-2"></i>${LibraryUtils.escapeHtml(t('library.delete', '删除'))}</button>`);
        }
        return `<div class="library-card">
            <div class="library-card-cover">
                ${manageCheck}
                ${this.thumbHtml(item)}
            </div>
            <div class="library-card-body">
                <div class="library-card-title">${LibraryUtils.escapeHtml(item.name || t('library.untitled', '未命名'))}</div>
                <div class="library-card-meta">
                    ${presetLabel ? `<span>${presetLabel}</span>` : ''}
                    <span>${LibraryUtils.escapeHtml(item.__categoryName || '素材')}</span>
                    ${item.owner_name ? `<span>${LibraryUtils.escapeHtml(item.owner_name)}</span>` : ''}
                </div>
            </div>
            <div class="library-card-actions">${actions.join('')}</div>
        </div>`;
    },

    async handleApply(id) {
        const item = this.findItem(id);
        if (!item) return;
        if (!window.applyLibraryAsset) {
            toast(t('library.canvasNotReady', '请先打开画布'));
            return;
        }
        window.applyLibraryAsset(item);
        LibraryModalManager.closeAll();
    },

    async deleteItem(id) {
        const item = this.findItem(id);
        if (!item) return;
        if (!confirm(t('library.deleteAssetConfirm', '确认删除这个素材？'))) return;
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

    toggleManage(force) {
        this.manageMode = typeof force === 'boolean' ? force : !this.manageMode;
        this.selectedAssetIds.clear();
        this.render();
    },

    toggleAssetSelection(id) {
        if (this.selectedAssetIds.has(id)) this.selectedAssetIds.delete(id);
        else this.selectedAssetIds.add(id);
        this.renderContent();
    },

    toggleSelectAllAssets() {
        const items = this.currentItems();
        const allSelected = items.length > 0 && items.every(item => this.selectedAssetIds.has(item.id));
        items.forEach(item => {
            if (allSelected) this.selectedAssetIds.delete(item.id);
            else this.selectedAssetIds.add(item.id);
        });
        this.renderContent();
    },

    async promptForNewFolder() {
        const name = prompt(t('library.newFolderName', '请输入新文件夹名称'), '');
        if (!name || !name.trim()) return;
        const lib = this.personalLibrary();
        if (!lib) {
            toast(t('library.personalLibraryMissing', '个人资产库尚未就绪'));
            return;
        }
        try {
            const res = await fetch('/api/asset-library/categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ library_id: lib.id, name: name.trim(), type: 'image' })
            });
            if (!res.ok) throw new Error('创建失败');
            await this.load();
            toast(t('library.folderCreated', '已创建文件夹'));
        } catch (err) {
            toast(err.message || '创建文件夹失败');
        }
    },

    async promptForRenameFolder(folderId) {
        const folder = this.findFolder(folderId);
        if (!folder) return;
        const name = prompt(t('library.renamePromptFolder', '请输入新的文件夹名称'), folder.name || '');
        if (!name || !name.trim() || name.trim() === folder.name) return;
        const lib = this.personalLibrary();
        try {
            const res = await fetch(`/api/asset-library/categories/${encodeURIComponent(folderId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ library_id: lib?.id || '', name: name.trim() })
            });
            if (!res.ok) throw new Error('重命名失败');
            await this.load();
            toast(t('library.folderRenamed', '已重命名'));
        } catch (err) {
            toast(err.message || '重命名失败');
        }
    },

    confirmDeleteFolder(folderId) {
        this.folderDeleteTarget = folderId;
        this.renderContent();
    },

    async deleteFolder(folderId) {
        const lib = this.personalLibrary();
        try {
            const params = new URLSearchParams({ library_id: lib?.id || '', mode: 'contents' });
            const res = await fetch(`/api/asset-library/categories/${encodeURIComponent(folderId)}?${params}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('删除失败');
            if (this.browseFolderId === folderId) this.browseFolderId = '';
            await this.load();
            toast(t('library.folderDeleted', '已删除文件夹'));
        } catch (err) {
            toast(err.message || '删除文件夹失败');
        }
    },

    async batchDeleteAssets() {
        if (!this.selectedAssetIds.size) return;
        if (!confirm(t('library.batchDeleteConfirm', '确认删除选中的 {n} 项素材？').replace('{n}', this.selectedAssetIds.size))) return;
        try {
            const res = await fetch('/api/asset-library/items/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: [...this.selectedAssetIds] })
            });
            if (!res.ok) throw new Error('删除失败');
            this.selectedAssetIds.clear();
            await this.load();
            toast(t('library.deleted', '已删除'));
        } catch (err) {
            toast(err.message || '删除失败');
        }
    },

    // ------------------------------------------------------------------
    // 上传到我的资产
    // ------------------------------------------------------------------
    bindUploadDialog() {
        const overlay = document.getElementById('assetUploadOverlay');
        if (!overlay) return;
        overlay.addEventListener('click', e => {
            if (e.target === overlay) this.closeUploadDialog();
            if (e.target.closest('[data-upload-close]') || e.target.closest('[data-upload-cancel]')) this.closeUploadDialog();
            if (e.target.closest('[data-upload-submit]')) this.submitUpload();
        });
        overlay.addEventListener('change', e => {
            if (e.target.matches('[data-upload-folder]')) {
                this.syncUploadFolderState();
                this.syncUploadSubmitState();
            }
        });
        overlay.addEventListener('input', e => {
            if (e.target.matches('[data-upload-name]') || e.target.matches('[data-upload-newfolder-name]')) {
                this.syncUploadSubmitState();
            }
        });
    },

    closeUploadDialog() {
        LibraryModalManager.releaseFocusTrap();
        document.getElementById('assetUploadOverlay')?.classList.remove('open');
        this.uploadState = null;
    },

    async openUploadDialog(nodeId, imageIndex) {
        const overlay = document.getElementById('assetUploadOverlay');
        if (!overlay) {
            toast(t('library.uploadUnavailable', '当前页面不支持上传到资产库'));
            return;
        }
        const node = (typeof nodes !== 'undefined' ? nodes : []).find(item => item?.id === nodeId);
        const media = node?.images?.[imageIndex];
        const image = typeof imageForDisplay === 'function' ? imageForDisplay(media) : media;
        if (!image?.url) {
            toast(t('library.uploadNoMedia', '未找到可上传的媒体'));
            return;
        }
        if (!this.library) await this.load();
        const kind = typeof mediaKindForItem === 'function' ? mediaKindForItem(image) : 'image';
        this.uploadState = { nodeId, imageIndex, media: image, kind: kind === 'video' ? 'video' : 'image' };
        this.fillUploadDialog(node);
        overlay.classList.add('open');
        LibraryModalManager.trapFocus(overlay);
        const newFolderName = overlay.querySelector('[data-upload-newfolder-name]');
        const hasFolders = this.personalImageFolders().length > 0;
        if (!hasFolders) newFolderName?.focus();
        else overlay.querySelector('[data-upload-name]')?.focus();
    },

    fillUploadDialog(node) {
        const overlay = document.getElementById('assetUploadOverlay');
        if (!overlay || !this.uploadState) return;
        const { media, kind } = this.uploadState;
        const preview = overlay.querySelector('.asset-upload-preview');
        if (preview) {
            const src = LibraryUtils.escapeHtml(String(media.url || ''));
            preview.innerHTML = kind === 'video'
                ? `<video src="${src}" controls muted playsinline></video>`
                : `<img src="${src}" alt="preview">`;
        }
        const nameInput = overlay.querySelector('[data-upload-name]');
        if (nameInput) nameInput.value = node?.title || media.name || '素材';
        const categorySelect = overlay.querySelector('[data-upload-category]');
        if (categorySelect) {
            const names = {
                video: t('library.category.video', '视频'),
                scene: t('library.category.scene', '场景'),
                ui: t('library.category.ui', '界面'),
                icon: t('library.category.icon', '符号'),
                clothing: t('library.category.clothing', '服装'),
                portrait: t('library.category.portrait', '立绘'),
                other: t('library.category.other', '其他')
            };
            const defaultCategory = kind === 'video' ? 'video' : 'other';
            categorySelect.innerHTML = this.PRESET_IDS.map(id =>
                `<option value="${id}" ${id === defaultCategory ? 'selected' : ''}>${LibraryUtils.escapeHtml(names[id] || id)}</option>`
            ).join('');
        }
        const folderSelect = overlay.querySelector('[data-upload-folder]');
        let hasFolders = true;
        if (folderSelect) {
            const folders = this.personalImageFolders();
            hasFolders = folders.length > 0;
            folderSelect.innerHTML = [
                `<option value="">${LibraryUtils.escapeHtml(t('library.selectFolderFirst', '请选择文件夹'))}</option>`,
                ...folders.map(folder => `<option value="${LibraryUtils.escapeHtml(folder.id)}">${LibraryUtils.escapeHtml(folder.name || '素材')}</option>`),
                `<option value="__new__">${LibraryUtils.escapeHtml(t('library.newFolderSelect', '新建文件夹…'))}</option>`
            ].join('');
            if (!hasFolders) folderSelect.value = '__new__';
        }
        const newFolderBox = overlay.querySelector('.asset-upload-newfolder');
        if (newFolderBox) newFolderBox.hidden = hasFolders;
        const newFolderName = overlay.querySelector('[data-upload-newfolder-name]');
        if (newFolderName) newFolderName.value = '';
        this.syncUploadSubmitState();
    },

    syncUploadFolderState() {
        const overlay = document.getElementById('assetUploadOverlay');
        if (!overlay) return;
        const folderSelect = overlay.querySelector('[data-upload-folder]');
        const newFolderBox = overlay.querySelector('.asset-upload-newfolder');
        if (newFolderBox) newFolderBox.hidden = String(folderSelect?.value || '') !== '__new__';
    },

    syncUploadSubmitState() {
        const overlay = document.getElementById('assetUploadOverlay');
        if (!overlay) return;
        const folderSelect = overlay.querySelector('[data-upload-folder]');
        const newFolderName = overlay.querySelector('[data-upload-newfolder-name]');
        const submit = overlay.querySelector('[data-upload-submit]');
        if (!submit) return;
        const folderValue = String(folderSelect?.value || '');
        const ready = folderValue === '__new__'
            ? Boolean(String(newFolderName?.value || '').trim())
            : Boolean(folderValue);
        submit.disabled = !ready;
    },

    isLocalMediaUrl(url) {
        return /^(\/assets\/|\/output\/)/.test(String(url || ''));
    },

    async ensureUploadedUrl(url, name) {
        if (this.isLocalMediaUrl(url)) return url;
        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error('下载媒体失败');
            const blob = await response.blob();
            const form = new FormData();
            form.append('files', blob, name || 'asset');
            const uploadRes = await fetch('/api/ai/upload', { method: 'POST', body: form });
            if (!uploadRes.ok) throw new Error('上传失败');
            const data = await uploadRes.json();
            const uploaded = data?.files?.[0];
            if (!uploaded?.url) throw new Error('上传失败');
            return uploaded.url;
        } catch (err) {
            throw new Error(err.message || '上传失败');
        }
    },

    async submitUpload() {
        const overlay = document.getElementById('assetUploadOverlay');
        if (!overlay || !this.uploadState) return;
        const { media, kind } = this.uploadState;
        const lib = this.personalLibrary();
        if (!lib) {
            toast(t('library.personalLibraryMissing', '个人资产库尚未就绪'));
            return;
        }
        const name = String(overlay.querySelector('[data-upload-name]')?.value || '').trim() || media.name || '素材';
        const category = overlay.querySelector('[data-upload-category]')?.value || 'other';
        const folderValue = String(overlay.querySelector('[data-upload-folder]')?.value || '');
        let categoryId = '';
        if (folderValue === '__new__') {
            const newFolderName = String(overlay.querySelector('[data-upload-newfolder-name]')?.value || '').trim();
            if (!newFolderName) {
                toast(t('library.folderNameRequired', '请输入文件夹名称'));
                return;
            }
            try {
                const createRes = await fetch('/api/asset-library/categories', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ library_id: lib.id, name: newFolderName, type: 'image' })
                });
                if (!createRes.ok) throw new Error('创建文件夹失败');
                const created = await createRes.json();
                categoryId = created?.category?.id || '';
            } catch (err) {
                toast(err.message || '创建文件夹失败');
                return;
            }
        } else if (folderValue) {
            categoryId = folderValue;
        } else {
            toast(t('library.selectFolderFirst', '请选择文件夹'));
            return;
        }
        const submit = overlay.querySelector('[data-upload-submit]');
        if (submit) {
            submit.disabled = true;
            submit.textContent = t('library.uploading', '正在上传...');
        }
        try {
            const url = await this.ensureUploadedUrl(media.url, name);
            const res = await fetch('/api/asset-library/items', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url,
                    name,
                    library_id: lib.id,
                    category_id: categoryId,
                    category: this.PRESET_IDS.includes(String(category)) ? category : 'other'
                })
            });
            if (!res.ok) throw new Error('入库失败');
            this.closeUploadDialog();
            toast(t('library.uploadSuccess', '已加入我的资产'));
            if (LibraryModalManager.isOpen('assets')) await this.load();
        } catch (err) {
            toast(err.message || t('library.uploadFailed', '上传失败'));
            if (submit) {
                submit.disabled = false;
                submit.textContent = t('library.uploadBtn', '上传');
            }
        }
    }
};

document.addEventListener('DOMContentLoaded', () => AssetLibrary.init());
window.openAssetLibrary = () => {
    LibraryModalManager.open('assets');
    AssetLibrary.load();
};
window.openAssetUploadDialog = (nodeId, imageIndex) => AssetLibrary.openUploadDialog(nodeId, imageIndex);



