/* ============================================
   Library Shared JS - 三个库共享逻辑
   ============================================ */

const LibraryShared = {
    currentUser: null,
    isAdminCache: null,
    
    async init() {
        await this.loadUser();
    },
    
    async loadUser() {
        try {
            const res = await fetch('/api/auth/me');
            const data = await res.json();
            this.currentUser = data?.user || null;
            this.isAdminCache = this.currentUser?.role === 'admin';
        } catch (e) {
            console.warn('[Library] Failed to load user:', e);
            this.currentUser = null;
            this.isAdminCache = false;
        }
    },
    
    isAdmin() {
        return this.isAdminCache === true;
    },
    
    getUser() {
        return this.currentUser;
    }
};

const LibraryModalManager = {
    currentOpen: null,
    _lastFocus: null,
    _trapHandler: null,
    _trapRoot: null,
    
    presentationMode() {
        const explicitMode = document.documentElement.dataset.libraryPresentation;
        if(explicitMode === 'panel' || explicitMode === 'standalone' || explicitMode === 'modal') return explicitMode;
        return window.self === window.top && document.body?.classList.contains('library-standalone') ? 'standalone' : 'modal';
    },

    isDismissibleModal() {
        return this.presentationMode() === 'modal';
    },

    init() {
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isDismissibleModal()) {
                this.closeAll();
            }
        });
        
        document.querySelectorAll('.library-modal-overlay').forEach(overlay => {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay && this.isDismissibleModal()) {
                    this.closeAll();
                }
            });
        });
    },
    
    open(type) {
        this.closeAll();
        this._lastFocus = document.activeElement;
        this.currentOpen = type;
        const overlay = document.getElementById(`${type}-library-overlay`);
        overlay?.classList.add('open');
        this.focusSearch(type);
        this.trapFocus(overlay);
    },
    
    closeAll() {
        // Standalone and workbench-panel library pages share modal markup, but their library
        // content is the page itself. Only a true embedded modal may remove its open state.
        if (!this.isDismissibleModal()) return;
        document.querySelectorAll('.library-modal-overlay').forEach(o => o.classList.remove('open'));
        this.releaseFocusTrap();
        this.currentOpen = null;
        if (this._lastFocus && typeof this._lastFocus.focus === 'function') {
            try { this._lastFocus.focus(); } catch (e) { /* ignore */ }
        }
        this._lastFocus = null;
    },

    trapFocus(overlay) {
        this.releaseFocusTrap();
        if (!overlay) return;
        this._trapRoot = overlay;
        this._trapHandler = (e) => {
            if (e.key !== 'Tab') return;
            const focusables = overlay.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
            const list = Array.from(focusables).filter(el => el.offsetParent !== null || el === document.activeElement);
            if (!list.length) return;
            const first = list[0];
            const last = list[list.length - 1];
            const inside = overlay.contains(document.activeElement);
            if (e.shiftKey && (!inside || document.activeElement === first)) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
                e.preventDefault();
                first.focus();
            }
        };
        document.addEventListener('keydown', this._trapHandler, true);
    },

    releaseFocusTrap() {
        if (this._trapHandler) {
            document.removeEventListener('keydown', this._trapHandler, true);
            this._trapHandler = null;
            this._trapRoot = null;
        }
    },
    
    focusSearch(type) {
        const search = document.querySelector(`#${type}-library-overlay .library-search input`);
        search?.focus();
    },
    
    isOpen(type) {
        return this.currentOpen === type;
    }
};

const LibrarySearch = {
    setup(containerId, onSearch) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        const input = container.querySelector('.library-search input');
        const clearBtn = container.querySelector('.library-search-clear');
        
        if (!input) return;
        
        let debounceTimer = null;
        
        input.addEventListener('input', () => {
            const value = input.value.trim();
            clearBtn?.classList.toggle('show', value.length > 0);
            
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                onSearch?.(value);
            }, 200);
        });
        
        clearBtn?.addEventListener('click', () => {
            input.value = '';
            clearBtn.classList.remove('show');
            onSearch?.('');
        });
    },
    
    clear(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        const input = container.querySelector('.library-search input');
        const clearBtn = container.querySelector('.library-search-clear');
        
        if (input) input.value = '';
        if (clearBtn) clearBtn.classList.remove('show');
    }
};

const LibraryUtils = {
    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
    
    truncate(str, maxLen) {
        if (!str) return '';
        if (str.length <= maxLen) return str;
        return str.slice(0, maxLen - 1) + '…';
    },
    
    formatDate(date) {
        if (!date) return '';
        const d = new Date(date);
        return d.toLocaleDateString('zh-CN', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    },
    
    debounce(fn, delay) {
        let timer = null;
        return function(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    },
    
    safeJsonParse(str, fallback = null) {
        try {
            return JSON.parse(str);
        } catch {
            return fallback;
        }
    }
};

function t(key, fallback = '') {
    if (window.StudioI18n?.t) {
        const translated = window.StudioI18n.t(key);
        return translated === key ? (fallback || translated) : translated;
    }
    const dict = {
        'library.search': '搜索...',
        'library.close': '关闭',
        'library.empty': '暂无内容',
        'library.apply': '应用',
        'library.publish': '发布',
        'library.unpublish': '取消发布',
        'library.favorite': '收藏',
        'library.unfavorite': '取消收藏',
        'library.delete': '删除',
        'library.edit': '编辑',
        'library.markRead': '标记已读',
        'library.addToCanvas': '添加到画布',
        'library.view': '查看',
        'library.createFolder': '新建文件夹',
        'library.upload': '上传',
        'library.myAssets': '我的资产',
        'library.materialLibrary': '素材库',
        'library.avatarLibrary': '虚拟人像库',
        'library.inspiration': '灵感库',
        'library.myFavorites': '我的收藏',
        'library.myPrompts': '我的提示词',
        'library.myWorkflows': '我的工作流',
        'library.characterSets': '角色套图',
        'library.characters': '角色',
        'library.category.all': '全部',
        'library.category.video': '视频',
        'library.category.scene': '场景',
        'library.category.ui': '界面',
        'library.category.icon': '符号',
        'library.category.clothing': '服装',
        'library.category.portrait': '立绘',
        'library.category.other': '其他'
    };
    return dict[key] || fallback;
}

document.addEventListener('DOMContentLoaded', async () => {
    await LibraryShared.init();
    LibraryModalManager.init();
});
