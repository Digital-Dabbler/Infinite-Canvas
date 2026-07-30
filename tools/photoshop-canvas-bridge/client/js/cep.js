(function (global) {
    "use strict";
    function evalScript(script) {
        return new Promise(function (resolve, reject) {
            if (!global.__adobe_cep__ || !global.__adobe_cep__.evalScript) {
                reject(new Error("当前环境不是 Photoshop CEP 面板"));
                return;
            }
            global.__adobe_cep__.evalScript(script, function (result) {
                if (result === "EvalScript error.") {
                    reject(new Error(result));
                    return;
                }
                resolve(result || "");
            });
        });
    }
    function call(name, args) {
        var encoded = [];
        var i;
        for (i = 0; i < (args || []).length; i += 1) {
            encoded.push(JSON.stringify(args[i]));
        }
        return evalScript(name + "(" + encoded.join(",") + ")").then(function (text) {
            var parsed;
            try { parsed = JSON.parse(text || "{}"); }
            catch (error) { throw new Error(text || "Photoshop 返回了无效结果"); }
            if (parsed && parsed.error) { throw new Error(parsed.error); }
            return parsed;
        });
    }
    function nodeRequire(name) {
        if (global.cep_node && global.cep_node.require) { return global.cep_node.require(name); }
        if (typeof require === "function") { return require(name); }
        throw new Error("CEP Node.js 不可用");
    }
    function dispatchApplicationEvent(type, data) {
        if (!global.__adobe_cep__ || !global.__adobe_cep__.dispatchEvent) { return false; }
        var extensionId = "";
        var appId = "PHXS";
        try { extensionId = global.__adobe_cep__.getExtensionId(); } catch (ignore) {}
        try {
            var environment = JSON.parse(global.__adobe_cep__.getHostEnvironment() || "{}");
            appId = environment.appId || appId;
        } catch (ignoreEnvironment) {}
        global.__adobe_cep__.dispatchEvent(JSON.stringify({
            type:type,
            scope:"APPLICATION",
            appId:appId,
            extensionId:extensionId || "com.daxiong.infinitecanvas.bridge.panel",
            data:data || ""
        }));
        return true;
    }
    function requestPersistent() {
        return dispatchApplicationEvent("com.adobe.PhotoshopPersistent", "");
    }
    global.BridgeCEP = {
        evalScript:evalScript,
        call:call,
        require:nodeRequire,
        dispatchApplicationEvent:dispatchApplicationEvent,
        requestPersistent:requestPersistent
    };
}(this));
