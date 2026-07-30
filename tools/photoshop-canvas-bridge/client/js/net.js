(function (global) {
    "use strict";
    var state = { host:"", token:"" };
    function cleanHost(value) {
        return String(value || "").replace(/^\s+|\s+$/g, "").replace(/^[a-z]+:\/\//i, "").replace(/[\/?#].*$/, "");
    }
    function base() { return state.host ? "http://" + state.host : ""; }
    function headers(extra) {
        var result = extra || {};
        result["X-Client-Source"] = "photoshop";
        if (state.token) { result.Authorization = "Bearer " + state.token; }
        return result;
    }
    function request(method, path, body, responseType) {
        return new Promise(function (resolve, reject) {
            var xhr = new XMLHttpRequest();
            xhr.open(method, base() + path, true);
            var hs = headers(body !== undefined ? {"Content-Type":"application/json"} : {});
            Object.keys(hs).forEach(function (key) { xhr.setRequestHeader(key, hs[key]); });
            if (responseType) { xhr.responseType = responseType; }
            xhr.onload = function () {
                var text;
                if (xhr.status < 200 || xhr.status >= 300) {
                    try { text = xhr.responseText || ""; } catch (ignore) { text = ""; }
                    try { text = JSON.parse(text).detail || text; } catch (ignore) {}
                    reject(new Error(text || ("HTTP " + xhr.status)));
                    return;
                }
                if (responseType) { resolve(xhr.response); return; }
                try { resolve(JSON.parse(xhr.responseText || "{}")); }
                catch (error) { reject(new Error("服务器返回了无效数据")); }
            };
            xhr.onerror = function () { reject(new Error("无法连接 Infinite Canvas")); };
            xhr.send(body !== undefined ? JSON.stringify(body) : null);
        });
    }
    function login(username, password) {
        return request("POST", "/api/auth/login", {username:username, password:password});
    }
    function get(path) { return request("GET", path); }
    function post(path, body) { return request("POST", path, body || {}); }
    function download(url) {
        var path = url;
        if (/^https?:\/\//i.test(url)) {
            path = url.replace(/^https?:\/\/[^/]+/i, "");
        }
        return request("GET", path, undefined, "arraybuffer");
    }
    global.BridgeNet = {
        state:state, cleanHost:cleanHost, base:base, login:login, get:get, post:post, download:download
    };
}(this));
