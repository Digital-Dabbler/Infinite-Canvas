(function(){
    // 画布入口的 v 由服务端根据相关代码修改时间生成；同版本复用缓存，
    // 词条变化时版本才会更新。普通页面则沿用当前加载器自身的版本。
    const pageVersion = new URLSearchParams(window.location.search).get('v');
    const loaderVersion = (() => {
        try { return new URL(document.currentScript?.src || '', window.location.href).searchParams.get('v'); }
        catch (_) { return ''; }
    })();
    const VERSION = pageVersion || loaderVersion || 'current';
    const scripts = [
        '/static/js/i18n-core.js',
        '/static/js/i18n/common.js',
        '/static/js/i18n/studio.js',
        '/static/js/i18n/api-settings.js',
        '/static/js/i18n/smart-canvas.js',
        '/static/js/i18n/comfyui-settings.js',
        '/static/js/i18n/library.js',
    ];
    const tags = scripts.map(src => '<script src="' + src + '?v=' + VERSION + '"></script>').join('');
    if(document.readyState === 'loading' && document.currentScript){
        document.write(tags);
        return;
    }
    scripts.reduce((promise, src) => promise.then(() => new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src + '?v=' + VERSION;
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    })), Promise.resolve()).then(() => window.StudioI18n?.apply?.()).catch(err => console.error('Failed to load i18n modules', err));
})();
