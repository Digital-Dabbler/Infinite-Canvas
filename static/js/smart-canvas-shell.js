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
    let snapEnabled = localStorage.getItem(SNAP_KEY) !== '0';
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
    document.querySelectorAll('[data-rh-action]').forEach(button => button.addEventListener('click', event => {
        event.stopPropagation();
        const action = button.dataset.rhAction;
        if(action === 'create') openCreateMenu({clientX:82,clientY:Math.max(90,event.currentTarget.getBoundingClientRect().top)});
        if(action === 'assets') toggleAssetLibrary();
        if(action === 'workflow') openSmartWorkflowTransferModal();
        if(action === 'history') openSmartCanvasLog();
        if(action === 'outline') toggleSmartOutline();
        if(action === 'edit') {
            const target = currentMediaToolbarTarget();
            if(target?.kind === 'image') runImageToolbarAction('edit');
            else toast('请先选择一张图片');
        }
    }));
    document.getElementById('rhMinimapToggle').onclick = event => {
        const open = !minimap.classList.contains('rh-open');
        minimap.classList.toggle('rh-open',open); event.currentTarget.classList.toggle('active',open);
        if(open) updateMinimap();
    };
    snapButton.onclick = () => {
        snapEnabled = !snapEnabled; localStorage.setItem(SNAP_KEY,snapEnabled ? '1' : '0');
        snapButton.classList.toggle('active',snapEnabled); toast(snapEnabled ? '已开启 20px 网格吸附' : '已关闭网格吸附');
    };
    snapButton.classList.toggle('active',snapEnabled);
    document.getElementById('rhFitAll').onclick = () => { fitAllNodesViewport(); syncZoom(); };
    document.getElementById('rhZoomOut').onclick = () => setScale(viewport.scale / 1.15);
    document.getElementById('rhZoomIn').onclick = () => setScale(viewport.scale * 1.15);
    zoomLabel.onclick = () => setScale(1);
    document.getElementById('rhShortcut').onclick = openSmartCanvasShortcuts;
    document.getElementById('rhAgentToggle').onclick = openAgent;
    document.getElementById('rhAgentClose').onclick = () => agentPanel.classList.remove('open');
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
    loadAccount(); loadBalances(); syncZoom(); refreshIcons();
})();
