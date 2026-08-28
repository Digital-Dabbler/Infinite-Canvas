(function(){
    const workbench = document.getElementById('workbenchFrame');
    const popover = document.getElementById('settingsPopover');
    const backdrop = document.getElementById('settingsBackdrop');
    const panel = document.getElementById('panelOverlay');
    const panelFrameHost = document.getElementById('panelFrameHost');
    const panelTitle = document.getElementById('panelTitle');
    const accountCard = document.getElementById('accountCard');
    const themeAction = document.getElementById('themeAction');
    const themeCurrentMode = document.getElementById('themeCurrentMode');
    const themeOtherMode = document.getElementById('themeOtherMode');
    const languageAction = document.getElementById('languageAction');
    const languageCurrentMode = document.getElementById('languageCurrentMode');
    const languageOtherMode = document.getElementById('languageOtherMode');
    const panelVersion = '2026.08.19-panel-cache';
    const panelRoutes = Object.freeze({
        assets: { path:'/static/library.html', title:'资产库', presentation:'panel' },
        'api-settings': { path:'/static/api-settings.html', title:'API 设置' },
        admin: { path:'/static/admin.html', title:'管理台' }
    });
    const panelFrames = new Map();
    let activePanelFrame = null;
    let activePanelName = '';
    let currentUser = null;
    let currentFontScale = 1;

    function clampFontScale(value){
        const next = Number(value);
        return Number.isFinite(next) ? Math.max(0.8, Math.min(1.5, next)) : 1;
    }
    function fontScaleCacheKey(userId){
        return `canvas_font_scale:${String(userId || '').trim()}`;
    }
    function syncFontScaleUI(){
        const percent = Math.round(currentFontScale * 100);
        const slider = document.getElementById('accountFontScale');
        const output = document.getElementById('accountFontScaleValue');
        if(slider) slider.value = String(percent);
        if(output) output.textContent = `${percent}%`;
        notifyFrames('studio-font-scale', { scale: currentFontScale });
    }
    function setCanvasFontScale(scale, persist=false){
        currentFontScale = clampFontScale(scale);
        try {
            if(currentUser?.id) localStorage.setItem(fontScaleCacheKey(currentUser.id), String(currentFontScale));
        } catch(e) {}
        syncFontScaleUI();
        if(persist) saveFontScalePreference(currentFontScale);
    }
    async function saveFontScalePreference(scale){
        try {
            const res = await fetch('/api/auth/me/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ canvas_font_scale: scale })
            });
            if(!res.ok) throw new Error('save failed');
            const data = await res.json();
            if(currentUser) currentUser.preferences = data.preferences || {};
        } catch(e) {
            // 保存失败时回滚到服务端值，避免本地与线上不一致。
            const serverScale = clampFontScale(currentUser?.preferences?.canvas_font_scale ?? 1);
            setCanvasFontScale(serverScale, false);
        }
    }

    function initials(user){
        const source = String(user?.name || user?.username || 'IC').trim();
        return Array.from(source).slice(0, 2).join('').toUpperCase();
    }
    function setOpen(element, open){ element?.classList.toggle('open', Boolean(open)); }
    function closeFloating(){
        setOpen(popover, false); setOpen(backdrop, false); setOpen(accountCard, false);
        backdrop?.classList.remove('modal');
    }
    function toggleSettings(){
        const open = !popover.classList.contains('open');
        closeFloating(); setOpen(popover, open); setOpen(backdrop, open);
    }
    function notifyFrames(type, payload={}){
        [workbench, ...panelFrames.values()].forEach(frame => {
            try { frame?.contentWindow?.postMessage({ type, ...payload }, location.origin); } catch(e){}
        });
    }
    function syncFramePreferences(frame){
        if(!frame?.contentWindow) return;
        const theme = document.documentElement.classList.contains('theme-dark') ? 'dark' : 'light';
        const lang = currentLanguage();
        try {
            frame.contentWindow.postMessage({type:'studio-theme',theme},location.origin);
            frame.contentWindow.postMessage({type:'studio-lang',lang},location.origin);
            frame.contentWindow.postMessage({type:'studio-font-scale',scale:currentFontScale},location.origin);
        } catch(e){}
    }
    function configurePanelFrame(name, frame){
        frame.className = 'studio-panel-frame';
        frame.title = panelRoutes[name].title;
        frame.dataset.panelName = name;
        frame.addEventListener('load', () => {
            frame.removeAttribute('data-loading');
            if(activePanelName !== name || activePanelFrame !== frame || !panel.classList.contains('open')) return;
            syncFramePreferences(frame);
            if(name === 'assets') {
                try {
                    const libraryBar = frame.contentDocument?.querySelector('.library-standalone-bar');
                    if(libraryBar) { libraryBar.hidden = true; libraryBar.style.display = 'none'; }
                } catch(e) {}
            }
        });
        panelFrameHost.appendChild(frame);
    }
    function panelFrameFor(name){
        let frame = panelFrames.get(name);
        if(frame) return frame;
        const route = panelRoutes[name];
        frame = document.createElement('iframe');
        frame.src = `${route.path}?${new URLSearchParams({v:panelVersion,...(route.presentation ? {presentation:route.presentation} : {})}).toString()}`;
        frame.setAttribute('data-loading','true');
        configurePanelFrame(name, frame);
        panelFrames.set(name, frame);
        return frame;
    }
    function showPanelFrame(name){
        const frame = panelFrameFor(name);
        panelFrames.forEach((item, key) => item.classList.toggle('active', key === name));
        activePanelName = name;
        activePanelFrame = frame;
        syncFramePreferences(frame);
        return frame;
    }
    function openPanel(name, push=true){
        const route = panelRoutes[name];
        if(!route) return;
        const replacingPanel = panel.classList.contains('open') || Boolean(new URLSearchParams(location.search).get('panel'));
        closeFloating();
        panelTitle.textContent = route.title;
        panel.dataset.name = name;
        panel.setAttribute('aria-hidden','false');
        panel.classList.add('open');
        const frame = showPanelFrame(name);
        if(push){
            const url = `/?panel=${encodeURIComponent(name)}`;
            if(replacingPanel) history.replaceState({ panel:name },'',url);
            else history.pushState({ panel:name },'',url);
        }
    }
    function closePanel(push=true){
        panel.classList.remove('open');
        panel.setAttribute('aria-hidden','true');
        delete panel.dataset.name;
        activePanelName = '';
        activePanelFrame = null;
        panelFrames.forEach(frame => frame.classList.remove('active'));
        if(push){
            if(history.state?.panel) history.back();
            else history.replaceState({},'', '/');
        }
    }
    function showAccount(){
        closeFloating();
        const user = currentUser || {};
        const avatar = initials(user);
        document.getElementById('accountAvatar').textContent = avatar;
        document.getElementById('accountName').textContent = user.name || user.username || '用户';
        document.getElementById('accountUsername').textContent = `@${user.username || 'user'}`;
        document.getElementById('accountRealName').textContent = user.name || '—';
        document.getElementById('accountDepartment').textContent = user.department || '—';
        document.getElementById('accountRole').textContent = user.role === 'admin' ? '管理员' : '普通用户';
        document.getElementById('accountProfile').textContent = user.api_profile_name || '默认配置';
        syncFontScaleUI();
        backdrop?.classList.add('modal');
        setOpen(accountCard, true); setOpen(backdrop, true);
    }
    async function loadSession(){
        try {
            const res = await fetch('/api/auth/me',{cache:'no-store'});
            if(res.status === 401){ location.href='/static/login.html'; return; }
            const data = await res.json();
            currentUser = data.user || {};
            currentFontScale = clampFontScale(currentUser.preferences?.canvas_font_scale ?? 1);
            const avatar = initials(currentUser);
            document.getElementById('settingsAvatar').textContent = avatar;
            document.getElementById('settingsUserName').textContent = currentUser.name || currentUser.username || 'Infinite Canvas';
            document.getElementById('settingsUserMeta').textContent = [currentUser.department,currentUser.api_profile_name].filter(Boolean).join(' · ') || '本地创作工作台';
            document.getElementById('adminEntry').hidden = currentUser.role !== 'admin';
            document.getElementById('apiSettingsEntry').hidden = currentUser.role !== 'admin';
            syncFontScaleUI();
        } catch(e){}
    }
    async function loadVersion(){
        try {
            const res = await fetch('/api/app-info',{cache:'no-store'});
            const data = await res.json();
            document.getElementById('versionText').textContent = `Infinite Canvas ${data.version || ''}`;
        } catch(e){ document.getElementById('versionText').textContent='Infinite Canvas'; }
    }
    async function checkUpdate(){
        const label = document.getElementById('updateStatus');
        label.textContent='检测中…';
        try {
            const res = await fetch('/api/check-update',{cache:'no-store'});
            const data = await res.json();
            label.textContent = data.has_update || data.update_available ? '发现新版本' : '已是最新';
        } catch(e){ label.textContent='检测失败'; }
    }
    async function logout(){
        try { await fetch('/api/auth/logout',{method:'POST'}); } finally { location.href='/static/login.html'; }
    }
    function currentLanguage(){
        return window.StudioI18n?.lang?.() || localStorage.getItem('studio_lang') || 'zh';
    }
    function updatePreferenceLabels(theme){
        const english = currentLanguage().startsWith('en');
        const dark = theme === 'dark';
        const currentMode = english ? (dark ? 'Dark' : 'Light') : (dark ? '深色' : '浅色');
        const otherMode = english ? (dark ? 'Light' : 'Dark') : (dark ? '浅色' : '深色');
        const themeLabel = english
            ? `Current mode: ${currentMode}. Switch to ${otherMode} mode`
            : `当前为${currentMode}模式，点击切换为${otherMode}模式`;
        themeCurrentMode.textContent = currentMode;
        themeOtherMode.textContent = otherMode;
        themeAction.setAttribute('aria-label', themeLabel);
        themeAction.setAttribute('aria-pressed', String(dark));
        themeAction.title = themeLabel;
        const currentLanguageLabel = english ? 'English' : '中文';
        const otherLanguageLabel = english ? '中文' : 'English';
        const languageLabel = english
            ? 'Current language: English. Switch to Chinese'
            : '当前语言为中文，点击切换为英文';
        languageCurrentMode.textContent = currentLanguageLabel;
        languageOtherMode.textContent = otherLanguageLabel;
        languageAction.setAttribute('aria-label', languageLabel);
        languageAction.setAttribute('aria-pressed', String(english));
        languageAction.title = languageLabel;
    }
    function applyTheme(theme, persist=false){
        const next = theme === 'dark' ? 'dark' : 'light';
        const dark = next === 'dark';
        if(persist){
            try {
                localStorage.setItem('studio_theme',next);
                localStorage.setItem('canvas_theme',next);
            } catch(e){}
        }
        [document.documentElement, document.body].forEach(element => {
            element?.classList.toggle('theme-dark',dark);
            element?.classList.toggle('studio-theme-dark',dark);
        });
        updatePreferenceLabels(next);
        window.dispatchEvent(new CustomEvent('studio-theme-change',{detail:{theme:next}}));
        notifyFrames('studio-theme',{theme:next});
    }
    function toggleTheme(){
        const current = document.documentElement.classList.contains('theme-dark') ? 'dark' : 'light';
        applyTheme(current === 'dark' ? 'light' : 'dark',true);
    }
    function toggleLanguage(){
        const current = currentLanguage();
        const next = current.startsWith('zh') ? 'en' : 'zh';
        if(window.StudioI18n?.set) window.StudioI18n.set(next);
        else {
            try { localStorage.setItem('studio_lang', next); } catch(e){}
            document.documentElement.setAttribute('lang', next === 'en' ? 'en' : 'zh-CN');
        }
        updatePreferenceLabels(document.documentElement.classList.contains('theme-dark') ? 'dark' : 'light');
        notifyFrames('studio-lang',{lang:next});
    }
    function handleAction(action){
        if(action==='account') showAccount();
        else if(action==='assets'||action==='api-settings'||action==='admin') openPanel(action);
        else if(action==='theme') toggleTheme();
        else if(action==='language') toggleLanguage();
        else if(action==='announcement'){ closeFloating(); window.openSiteAnnouncement?.(); }
        else if(action==='github') window.open('https://github.com/hero8152/Infinite-Canvas','_blank','noopener');
        else if(action==='update') checkUpdate();
        else if(action==='logout') logout();
    }
    document.addEventListener('click', event => {
        const action = event.target.closest('[data-action]')?.dataset.action;
        if(action) handleAction(action);
    });
    backdrop.onclick = closeFloating;
    document.getElementById('accountClose').onclick = closeFloating;
    document.getElementById('panelClose').onclick = () => closePanel();
    window.addEventListener('message', event => {
        if(event.origin !== location.origin) return;
        const message = event.data || {};
        const fromWorkbench = event.source === workbench?.contentWindow;
        const fromActivePanel = event.source === activePanelFrame?.contentWindow && panel.classList.contains('open');
        if(message.type==='studio:toggle-settings' && fromWorkbench) toggleSettings();
        if(message.type==='studio:close-panel' && fromActivePanel) closePanel();
        if(message.type==='studio:open-announcement' && fromWorkbench) window.openSiteAnnouncement?.();
        if(message.type==='studio-font-scale' && fromWorkbench){
            currentFontScale = clampFontScale(message.scale || 1);
            setCanvasFontScale(currentFontScale, false);
        }
    });
    workbench.addEventListener('load', () => syncFramePreferences(workbench));
    window.addEventListener('popstate', () => {
        const name = new URLSearchParams(location.search).get('panel');
        if(name) openPanel(name,false); else closePanel(false);
    });
    document.addEventListener('keydown', event => {
        if(event.key!=='Escape') return;
        if(panel.classList.contains('open')) closePanel(); else closeFloating();
    });
    const initialPanel = new URLSearchParams(location.search).get('panel');
    if(initialPanel) openPanel(initialPanel,false);
    const initialTheme = localStorage.getItem('studio_theme') || localStorage.getItem('canvas_theme') || 'dark';
    applyTheme(initialTheme);
    const fontScaleSlider = document.getElementById('accountFontScale');
    const fontScaleReset = document.getElementById('accountFontScaleReset');
    if(fontScaleSlider){
        fontScaleSlider.addEventListener('input', () => setCanvasFontScale(Number(fontScaleSlider.value) / 100, false));
        fontScaleSlider.addEventListener('change', () => setCanvasFontScale(Number(fontScaleSlider.value) / 100, true));
    }
    if(fontScaleReset){
        fontScaleReset.addEventListener('click', () => setCanvasFontScale(1, true));
    }
    window.addEventListener('storage', event => {
        if(event.key === 'studio_theme' || event.key === 'canvas_theme'){
            applyTheme(localStorage.getItem('studio_theme') || localStorage.getItem('canvas_theme') || 'dark');
        }
        if(currentUser?.id && event.key === fontScaleCacheKey(currentUser.id)){
            currentFontScale = clampFontScale(event.newValue || 1);
            syncFontScaleUI();
        }
    });
    loadSession(); loadVersion(); lucide?.createIcons();
})();
