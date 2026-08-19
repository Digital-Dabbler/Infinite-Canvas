(function(){
    const SNAP_KEY = 'smart_canvas_snap_enabled_v1';
    const minimap = document.getElementById('minimap');
    const snapButton = document.getElementById('rhSnapToggle');
    const zoomLabel = document.getElementById('rhZoomLabel');
    const agentPanel = document.getElementById('rhAgentPanel');
    const agentFrame = document.getElementById('rhAgentFrame');
    const accountPopover = document.getElementById('rhAccountPopover');
    const balancePopover = document.getElementById('rhBalancePopover');
    const oldTitle = document.getElementById('smartTitle');
    const shellTitle = document.getElementById('rhCanvasTitle');
    // 精细画布默认自由拖动；只有用户明确开启后才进行 20px 吸附。
    let snapEnabled = localStorage.getItem(SNAP_KEY) === '1';
    let user = null;
    window.isSmartCanvasSnapEnabled = () => snapEnabled;

    function initials(value){
        const source = String(value || 'IC').trim();
        return Array.from(source).slice(0,2).join('').toUpperCase();
    }
    function syncZoom(){
        if(zoomLabel && typeof viewport !== 'undefined') zoomLabel.textContent = `${Math.round(viewport.scale * 100)}%`;
    }
    function setScale(next){
        if(typeof viewport === 'undefined') return;
        const center = viewportCenter();
        viewport.scale = safeScale(next);
        viewport.x = shell.clientWidth / 2 - center.x * viewport.scale;
        viewport.y = shell.clientHeight / 2 - center.y * viewport.scale;
        applyViewport(); scheduleSave(); syncZoom();
    }
    function closePopovers(){
        accountPopover?.classList.remove('open');
        balancePopover?.classList.remove('open');
    }
    async function loadAccount(){
        try {
            const response = await fetch('/api/auth/me',{cache:'no-store'});
            const data = await response.json();
            user = data.user || {};
            const mark = initials(user.name || user.username);
            document.getElementById('rhAccountInitial').textContent = mark;
            document.getElementById('rhAccountLarge').textContent = mark;
            document.getElementById('rhAccountName').textContent = user.name || user.username || '用户';
            document.getElementById('rhAccountUsername').textContent = `@${user.username || 'user'}`;
            document.getElementById('rhAccountDepartment').textContent = user.department || '—';
            document.getElementById('rhAccountRole').textContent = user.role === 'admin' ? '管理员' : '普通用户';
            document.getElementById('rhAccountProfile').textContent = user.api_profile_name || '默认配置';
        } catch(e){}
    }
    function formatAmount(item){
        const amount = Number(item?.amount || 0).toFixed(2);
        const currency = String(item?.currency || '').toUpperCase();
        if(currency === 'CNY') return `¥${amount}`;
        if(currency === 'USD') return `$${amount}`;
        if(currency === 'EUR') return `€${amount}`;
        return `${currency} ${amount}`;
    }
    async function loadBalances(refresh=false){
        const details = document.getElementById('rhBalanceDetails');
        if(details) details.textContent = '正在加载…';
        try {
            const response = await fetch(`/api/provider-balances${refresh ? '?refresh=true' : ''}`,{cache:'no-store'});
            const data = await response.json();
            const totals = Array.isArray(data.totals) ? data.totals : [];
            document.getElementById('rhBalanceText').textContent = totals.length ? totals.map(formatAmount).join(' · ') : '余额详情';
            if(details){
                details.innerHTML = (data.balances || []).map(item => {
                    const value = item.status === 'ok'
                        ? (item.currency && item.amount != null ? formatAmount(item) : String(item.display || item.balance || '可用'))
                        : (item.status === 'unsupported' ? '暂不支持查询' : '查询失败');
                    return `<div class="rh-balance-item"><span>${escapeHtml(item.provider_name || item.provider || '平台')}</span><strong>${escapeHtml(value)}</strong></div>`;
                }).join('') || '<div class="rh-balance-item"><span>当前配置组</span><strong>暂无可查询余额</strong></div>';
            }
        } catch(e){
            document.getElementById('rhBalanceText').textContent = '余额不可用';
            if(details) details.textContent = '余额查询失败';
        }
    }
    function openAgent(){
        closePopovers();
        if(!agentFrame.src){
            agentFrame.src = `/static/gpt-chat.html?embed=canvas&canvas_id=${encodeURIComponent(canvasId || '')}`;
        }
        agentPanel.classList.add('open');
    }
    function selectedChatContext(){
        const ids = selectedNodeIds();
        const images = [];
        const videos = [];
        ids.forEach(id => {
            const node = nodes.find(item => item.id === id);
            (node?.images || []).forEach(raw => {
                const item = imageForDisplay(raw);
                if(!item?.url) return;
                const ref = {url:item.url,name:item.name || node.title || 'canvas media',kind:mediaKindForItem(item),source_node_id:node.id};
                if(ref.kind === 'video') videos.push(ref); else if(ref.kind === 'image') images.push(ref);
            });
        });
        return {images,videos};
    }
    function sendContextToChat(){
        const context = selectedChatContext();
        agentFrame.contentWindow?.postMessage({type:'canvas-chat-context',canvas_id:canvasId,...context},location.origin);
    }
    function insertChatImage(data){
        const url = String(data.url || '');
        if(!url) return;
        pushUndo();
        const node = createImageNodeAt(viewportCenter(), [{url,kind:'image',name:'Canvas AI'}], {select:true,skipUndo:true});
        node.chatSource = {conversation_id:data.conversation_id || '',message_id:data.message_id || ''};
        addSmartGenerationLog({run:{nodeId:node.id,nodeType:node.type,engine:'Canvas AI',prompt:data.prompt || ''},outputs:[{url,kind:'image'}]});
        render(); scheduleSave(); toast('已放入画布');
    }
    function insertChatText(data, kind){
        const text = String(data.text || '').trim();
        if(!text) return;
        const center = viewportCenter();
        if(kind === 'prompt'){
            const node = createPromptNode(center.x - 158,center.y - 97);
            node.text = text;
            node.chatSource = {conversation_id:data.conversation_id || '',message_id:data.message_id || ''};
            render(); scheduleSave();
        } else {
            const node = createSmartNote(center);
            node.text = text;
            node.chatSource = {conversation_id:data.conversation_id || '',message_id:data.message_id || ''};
            fitSmartNoteToText(node); render(); scheduleSave();
        }
        toast(kind === 'prompt' ? '已创建提示词节点' : '已创建便签');
    }
    // ---------------------------------------------------------------------
    // 统一开合控制：左侧工具栏与视图控件的每个入口都是「点一下打开、再点一下关闭」。
    // 面板可能被 Esc、面板自带关闭按钮或画布点击关闭，所以按钮状态不能只在点击时写死，
    // 统一由 MutationObserver 观察面板 class 回写 aria-expanded / .active。
    // ---------------------------------------------------------------------
    function t(key, fallback){
        const value = window.StudioI18n?.t ? window.StudioI18n.t(key) : key;
        return value === key ? (fallback ?? key) : value;
    }
    function isOpenEl(el, cls){ return Boolean(el?.classList?.contains(cls || 'open')); }
    const surfaces = {
        create: {
            labelKey:'smart.railCreate', labelFallback:'新建',
            el: () => document.getElementById('createMenu'),
            isOpen(){ return isOpenEl(this.el()); },
            open(button){
                const rect = button?.getBoundingClientRect?.();
                openCreateMenu({clientX:82, clientY:Math.max(90, rect ? rect.top : 90)});
            },
            close(){ closeCreateMenu(); },
        },        assets: {
            labelKey:'smart.railAssets', labelFallback:'资产',
            group:'dock',
            el: () => document.getElementById('assets-library-modal'),
            isOpen(){ return LibraryModalManager?.isOpen?.('assets'); },
            open(){ window.openAssetLibrary?.(); },
            close(){ LibraryModalManager?.closeAll?.(); },
        },
        prompts: {
            labelKey:'smart.railPrompts', labelFallback:'提示词',
            group:'dock',
            el: () => document.getElementById('prompts-library-modal'),
            isOpen(){ return LibraryModalManager?.isOpen?.('prompts'); },
            open(){ window.openPromptLibrary?.(); },
            close(){ LibraryModalManager?.closeAll?.(); },
        },        workflow: {
            labelKey:'smart.railWorkflow', labelFallback:'工作流',
            group:'dock',
            el: () => document.getElementById('workflows-library-modal'),
            isOpen(){ return LibraryModalManager?.isOpen?.('workflows'); },
            open(){ window.openWorkflowLibrary?.(); },
            close(){ LibraryModalManager?.closeAll?.(); },
        },
        outline: {
            labelKey:'smart.railOutline', labelFallback:'画布目录',
            group:'dock',
            el: () => document.getElementById('smartOutlinePanel'),
            isOpen(){ return isOpenEl(this.el()); },
            open(){ toggleSmartOutline(true); },
            close(){ toggleSmartOutline(false); },
        },
        history: {
            labelKey:'smart.railHistory', labelFallback:'生成历史',
            el: () => document.getElementById('smartLogModal'),
            isOpen(){ return isOpenEl(this.el()); },
            open(){ openSmartCanvasLog(); },
            close(){ closeSmartCanvasLog(); },
        },
        edit: {
            labelKey:'smart.railEdit', labelFallback:'编辑',
            el: () => document.getElementById('imageEditModal'),
            isOpen(){ return isOpenEl(this.el()); },
            open(){
                const target = currentMediaToolbarTarget();
                if(target?.kind === 'image') runImageToolbarAction('edit');
                else toast(t('smart.railEditNeedImage','请先选择一张图片'));
            },
            close(){ closeImageEditor(); },
        },
        shortcut: {
            labelKey:'smart.shortcuts', labelFallback:'快捷键',
            el: () => document.getElementById('smartShortcutModal'),
            isOpen(){ return isOpenEl(this.el()); },
            open(){ openSmartCanvasShortcuts(); },
            close(){ closeSmartCanvasShortcuts(); },
        },
        minimap: {
            labelKey:'smart.railMinimap', labelFallback:'小地图',
            el: () => minimap,
            isOpen(){ return isOpenEl(this.el(),'rh-open'); },
            open(){ minimap?.classList.add('rh-open'); renderMinimap(); },
            close(){ minimap?.classList.remove('rh-open'); },
        },
        clip: {
            labelKey:'smart.railClip', labelFallback:'剪辑',
            el: () => document.getElementById('smartClipModal'),
            isOpen(){ return isOpenEl(this.el()); },
            open(){ window.openSmartClipModal?.(); },
            close(){ window.closeSmartClipModal?.(); },
        },
        agent: {
            labelKey:'smart.railAgent', labelFallback:'AI 对话',
            el: () => agentPanel,
            isOpen(){ return isOpenEl(this.el()); },
            open(){ openAgent(); },
            close(){ agentPanel?.classList.remove('open'); },
        },
    };
    const surfaceButtons = new Map();
    function registerSurfaceButton(name, button){
        if(!button || !surfaces[name]) return;
        surfaceButtons.set(name, button);
        button.dataset.rhSurface = name;
        button.setAttribute('aria-expanded','false');
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            toggleSurface(name);
        });
    }
    function toggleSurface(name, force){
        const surface = surfaces[name];
        if(!surface) return;
        const button = surfaceButtons.get(name);
        const shouldOpen = typeof force === 'boolean' ? force : !surface.isOpen();
        // 头部账户/余额浮层与面板不应同时存在。
        closePopovers();
        if(shouldOpen){
            // 新建菜单是瞬时浮层，打开任何其他面板时都应先收起它。
            if(name !== 'create' && surfaces.create.isOpen()) surfaces.create.close();
            // 同区面板互相遮挡，打开一个就收起同组其他面板。
            if(surface.group){
                Object.entries(surfaces).forEach(([key, other]) => {
                    if(key !== name && other.group === surface.group && other.isOpen()) other.close();
                });
            }
            surface.open(button);
        } else {
            surface.close();
        }
        syncSurfaceStates();
    }
    function syncSurfaceStates(){
        surfaceButtons.forEach((button, name) => {
            const surface = surfaces[name];
            const open = surface.isOpen();
            button.classList.toggle('active', open);
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
            const label = t(surface.labelKey, surface.labelFallback);
            const tip = open
                ? t('smart.panelClickToClose','点击关闭{name}').replace('{name}', label)
                : t('smart.panelClickToOpen','点击打开{name}').replace('{name}', label);
            button.title = tip;
            button.setAttribute('aria-label', tip);
        });
    }
    window.syncSmartCanvasRailStates = syncSurfaceStates;
    document.querySelectorAll('[data-rh-action]').forEach(button => registerSurfaceButton(button.dataset.rhAction, button));
    registerSurfaceButton('minimap', document.getElementById('rhMinimapToggle'));
    registerSurfaceButton('shortcut', document.getElementById('rhShortcut'));
    registerSurfaceButton('agent', document.getElementById('rhAgentToggle'));
    // 面板可以被自身关闭按钮、Esc 或画布点击关闭，这里回写按钮开合态，避免出现「按钮亮着但面板已关」。
    const surfaceObserver = new MutationObserver(() => syncSurfaceStates());
    new Set(Object.values(surfaces).map(surface => surface.el()).filter(Boolean))
        .forEach(el => surfaceObserver.observe(el, {attributes:true, attributeFilter:['class']}));
    window.addEventListener('studio-lang-change', () => syncSurfaceStates());
    // Esc 也应能关闭这些侧栏/浮层，smart-canvas.js 只处理了一部分，这里补齐剩余面板。
    document.addEventListener('keydown', event => {
        if(event.key !== 'Escape') return;
        ['agent','workflow','outline','minimap','clip'].forEach(name => {
            if(surfaces[name].isOpen()) surfaces[name].close();
        });
        syncSurfaceStates();
    });
    snapButton.onclick = () => {
        snapEnabled = !snapEnabled; localStorage.setItem(SNAP_KEY,snapEnabled ? '1' : '0');
        snapButton.classList.toggle('active',snapEnabled);
        snapButton.setAttribute('aria-pressed',snapEnabled ? 'true' : 'false');
        toast(snapEnabled ? '已开启 20px 网格吸附' : '已关闭网格吸附');
    };
    snapButton.classList.toggle('active',snapEnabled);
    snapButton.setAttribute('aria-pressed',snapEnabled ? 'true' : 'false');
    document.getElementById('rhFitAll').onclick = () => { fitAllNodesViewport(); syncZoom(); };
    document.getElementById('rhZoomOut').onclick = () => setScale(viewport.scale / 1.15);
    document.getElementById('rhZoomIn').onclick = () => setScale(viewport.scale * 1.15);
    zoomLabel.onclick = () => setScale(1);
    document.getElementById('rhThemeToggle').onclick = () => {
        const nextTheme = document.documentElement.classList.contains('theme-dark') ? 'light' : 'dark';
        window.StudioTheme?.set(nextTheme);
    };
    document.getElementById('rhAgentClose').onclick = () => toggleSurface('agent', false);
    document.getElementById('rhAnnouncementBtn').onclick = () => parent.postMessage({type:'studio:open-announcement'},location.origin);
    document.getElementById('rhAccountBtn').onclick = event => { event.stopPropagation(); balancePopover.classList.remove('open'); accountPopover.classList.toggle('open'); };
    document.getElementById('rhBalanceBtn').onclick = event => { event.stopPropagation(); accountPopover.classList.remove('open'); balancePopover.classList.toggle('open'); };
    document.getElementById('rhBalanceRefresh').onclick = () => loadBalances(true);
    shellTitle.onclick = () => {
        if(!canvas) return;
        const title = prompt('画布名称',canvas.title || '');
        if(title?.trim()){
            pushUndo(); canvas.title = title.trim(); oldTitle.textContent = canvas.title; shellTitle.textContent = canvas.title;
            document.title = canvas.title; scheduleSave();
        }
    };
    if(oldTitle) new MutationObserver(() => { shellTitle.textContent = oldTitle.textContent || 'Untitled'; }).observe(oldTitle,{childList:true,characterData:true,subtree:true});
    window.addEventListener('message', event => {
        if(event.origin !== location.origin || event.data?.canvas_id !== canvasId) return;
        if(event.data.type === 'canvas-chat-request-context') sendContextToChat();
        if(event.data.type === 'canvas-chat-insert-image') insertChatImage(event.data);
        if(event.data.type === 'canvas-chat-insert-prompt') insertChatText(event.data,'prompt');
        if(event.data.type === 'canvas-chat-insert-note') insertChatText(event.data,'note');
    });
    document.addEventListener('click', event => {
        if(!event.target.closest('#rhAccountPopover,#rhAccountBtn')) accountPopover.classList.remove('open');
        if(!event.target.closest('#rhBalancePopover,#rhBalanceBtn')) balancePopover.classList.remove('open');
    });
    shell.addEventListener('wheel', () => requestAnimationFrame(syncZoom),{passive:true});
    shellTitle.textContent = oldTitle?.textContent || 'Untitled';
    loadAccount(); loadBalances(); syncZoom(); syncSurfaceStates(); refreshIcons();
})();
