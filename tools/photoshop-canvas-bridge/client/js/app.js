(function (global) {
    "use strict";
    var net = global.BridgeNet;
    var cep = global.BridgeCEP;
    var fs = cep.require("fs");
    var os = cep.require("os");
    var path = cep.require("path");
    var tasks = [];
    var canvases = [];
    var projects = {};
    var connected = false;
    var targetCanvasId = "";
    var socket = null;
    var socketHistoryIds = {};
    var hiddenTaskIds = {};
    var reconnectTimer = null;
    var heartbeatTimer = null;
    var socketGeneration = 0;
    var lastSocketActivity = 0;
    var inboxBaselineReady = false;
    var opening = {};
    var thumbnailUrls = {};
    var fullImageUrls = {};
    var previewIndex = -1;
    var choiceTaskId = "";
    var tileClickTimer = null;
    var collapsedProjects = {};
    var lastDocumentState = {hasDocument:false, selection:null};
    var documentPollTimer = null;
    var smallSelectionContinue = null;
    var currentUser = null;
    var LS_HOST = "infinite_canvas_bridge_host";
    var LS_TOKEN = "infinite_canvas_bridge_token";
    var LS_TARGET = "infinite_canvas_bridge_target_canvas";
    var LS_INSTANCE = "infinite_canvas_bridge_instance_id";
    var instanceId = "";

    var el = {
        setup:document.getElementById("setupPanel"),
        server:document.getElementById("serverInput"),
        username:document.getElementById("usernameInput"),
        password:document.getElementById("passwordInput"),
        connect:document.getElementById("connectButton"),
        status:document.getElementById("statusBar"),
        dot:document.getElementById("connectionDot"),
        clear:document.getElementById("clearButton"),
        refresh:document.getElementById("refreshButton"),
        summary:document.getElementById("taskSummary"),
        galleryStage:document.getElementById("galleryStage"),
        gallery:document.getElementById("galleryGrid"),
        empty:document.getElementById("emptyState"),
        preview:document.getElementById("previewLayer"),
        previewImage:document.getElementById("previewImage"),
        previewName:document.getElementById("previewName"),
        previewCanvas:document.getElementById("previewCanvas"),
        previewPrevious:document.getElementById("previewPrevious"),
        previewNext:document.getElementById("previewNext"),
        previewClose:document.getElementById("previewClose"),
        previewPlace:document.getElementById("previewPlace"),
        previewOpen:document.getElementById("previewOpen"),
        targetButton:document.getElementById("canvasTargetButton"),
        targetName:document.getElementById("canvasTargetName"),
        activeDocument:document.getElementById("activeDocumentName"),
        scopeText:document.getElementById("exportScopeText"),
        send:document.getElementById("sendButton"),
        choice:document.getElementById("choiceDialog"),
        choiceName:document.getElementById("choiceName"),
        choicePlace:document.getElementById("choicePlace"),
        choiceOpen:document.getElementById("choiceOpen"),
        picker:document.getElementById("canvasPicker"),
        canvasSearch:document.getElementById("canvasSearch"),
        canvasList:document.getElementById("canvasList"),
        smallDialog:document.getElementById("smallSelectionDialog"),
        smallText:document.getElementById("smallSelectionText"),
        smallCancel:document.getElementById("cancelSmallSelection"),
        smallContinue:document.getElementById("continueSmallSelection")
    };

    function randomId() {
        return "ps-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
    }
    function loadState() {
        el.server.value = localStorage.getItem(LS_HOST) || "127.0.0.1:3000";
        net.state.host = net.cleanHost(el.server.value);
        net.state.token = localStorage.getItem(LS_TOKEN) || "";
        targetCanvasId = localStorage.getItem(LS_TARGET) || "";
        instanceId = localStorage.getItem(LS_INSTANCE) || randomId();
        localStorage.setItem(LS_INSTANCE, instanceId);
    }
    function setStatus(text, kind) {
        el.status.textContent = text || "";
        el.status.className = "status-bar" + (kind ? " " + kind : "");
    }
    function setConnected(value) {
        connected = value;
        el.dot.className = "connection-dot" + (value ? " online" : "");
        el.clear.disabled = !value;
        el.refresh.disabled = !value;
        el.setup.className = "setup-panel" + (value ? " compact" : "");
        el.connect.textContent = value ? "切换账号" : "连接画布";
        renderSendState();
    }
    function clearTaskInbox(resetHistory) {
        if (!resetHistory) {
            tasks.forEach(function (task) {
                if (task && task.id) { hiddenTaskIds[task.id] = true; }
            });
        }
        tasks = [];
        if (resetHistory) {
            socketHistoryIds = {};
            hiddenTaskIds = {};
            inboxBaselineReady = false;
        }
        closePreview();
        releaseUnusedImages();
        renderGallery();
    }
    function finishAccountSwitch() {
        if (socket) { try { socket.close(); } catch (ignore) {} }
        socket = null;
        clearTimeout(reconnectTimer);
        clearInterval(heartbeatTimer);
        clearInterval(documentPollTimer);
        net.state.token = "";
        currentUser = null;
        localStorage.removeItem(LS_TOKEN);
        el.password.value = "";
        clearTaskInbox(true);
        setConnected(false);
        setStatus("已退出当前账号，请输入另一位 Infinite Canvas 用户的账号和密码。");
        try { el.username.focus(); } catch (ignoreFocus) {}
    }
    function switchAccount() {
        el.connect.disabled = true;
        net.post("/api/auth/logout", {}).catch(function () {}).then(function () {
            finishAccountSwitch();
            el.connect.disabled = false;
        });
    }
    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = String(text || "");
        return div.innerHTML;
    }
    function taskById(id) {
        var i;
        for (i = 0; i < tasks.length; i += 1) {
            if (tasks[i].id === id) { return tasks[i]; }
        }
        return null;
    }
    function canvasById(id) {
        var i;
        for (i = 0; i < canvases.length; i += 1) {
            if (canvases[i].id === id) { return canvases[i]; }
        }
        return null;
    }
    function canvasIcon(canvas) {
        var value = String((canvas && canvas.icon) || "");
        var named = {
            sparkles:"✦", layers:"▱", image:"▧", images:"▧",
            palette:"◒", wand:"✣", star:"★", box:"◇"
        };
        return named[value] || (value.length > 0 && value.length <= 2 ? value : "✦");
    }
    function statusText(status) {
        return {
            pending:"待打开",
            opening:"打开中",
            opened:"已打开",
            open_failed:"打开失败",
            claimed:"已打开",
            completed:"已回传",
            cancelled:"已取消"
        }[status] || status || "未知";
    }
    function setTargetCanvas(canvasId, fallbackName) {
        targetCanvasId = canvasId || "";
        if (targetCanvasId) { localStorage.setItem(LS_TARGET, targetCanvasId); }
        else { localStorage.removeItem(LS_TARGET); }
        var canvas = canvasById(targetCanvasId);
        el.targetName.textContent = canvas ? (canvas.title || "智能画布") : (fallbackName || "选择智能画布");
        renderCanvasList(el.canvasSearch.value || "");
        renderSendState();
    }
    function mergeTask(task) {
        var found = false;
        var i;
        for (i = 0; i < tasks.length; i += 1) {
            if (tasks[i].id === task.id) {
                tasks[i] = task;
                found = true;
                break;
            }
        }
        if (!found) { tasks.unshift(task); }
        tasks.sort(function (a, b) { return Number(b.created_at || 0) - Number(a.created_at || 0); });
        if (tasks.length > 50) { tasks = tasks.slice(0, 50); }
        renderGallery();
    }
    function mediaPath(task, thumbnail) {
        var source = task.source_url || "";
        var extension = String(source).split("?")[0].split(".").pop().toLowerCase();
        if (thumbnail) {
            return "/api/media-preview?url=" + encodeURIComponent(source) + "&w=420";
        }
        if (/^(webp|avif|heic|heif|tiff?)$/.test(extension)) {
            return "/api/image-jpeg?url=" + encodeURIComponent(source);
        }
        return source;
    }
    function extensionForTask(task) {
        var extension = String(task.source_url || "").split("?")[0].split(".").pop().toLowerCase();
        if (/^(webp|avif|heic|heif|tiff?)$/.test(extension)) { return "jpg"; }
        return /^(png|jpe?g|gif|bmp)$/.test(extension) ? extension : "png";
    }
    function objectUrlFromBuffer(buffer, mime) {
        return URL.createObjectURL(new Blob([buffer], {type:mime || "image/jpeg"}));
    }
    function loadThumbnail(task, image) {
        if (thumbnailUrls[task.id]) {
            image.src = thumbnailUrls[task.id];
            return;
        }
        net.download(mediaPath(task, true)).then(function (buffer) {
            thumbnailUrls[task.id] = objectUrlFromBuffer(buffer, "image/jpeg");
            if (document.body.contains(image)) { image.src = thumbnailUrls[task.id]; }
        }).catch(function () {
            image.alt = "缩略图加载失败";
        });
    }
    function sizeGalleryCard(card) {
        if (!card || !el.gallery) { return; }
        var styles = global.getComputedStyle(el.gallery);
        var row = parseFloat(styles.getPropertyValue("grid-auto-rows")) || 5;
        var gap = parseFloat(styles.getPropertyValue("row-gap")) || 8;
        var height = card.getBoundingClientRect().height;
        card.style.gridRowEnd = "span " + Math.max(1, Math.ceil((height + gap) / (row + gap)));
    }
    function releaseUnusedImages() {
        var keep = {};
        var i;
        for (i = 0; i < tasks.length; i += 1) { keep[tasks[i].id] = true; }
        Object.keys(thumbnailUrls).forEach(function (id) {
            if (!keep[id]) {
                URL.revokeObjectURL(thumbnailUrls[id]);
                delete thumbnailUrls[id];
            }
        });
        Object.keys(fullImageUrls).forEach(function (id) {
            if (!keep[id]) {
                URL.revokeObjectURL(fullImageUrls[id]);
                delete fullImageUrls[id];
            }
        });
    }
    function renderGallery() {
        var html = "";
        var i;
        el.summary.textContent = tasks.length ? ("最近 7 天 · " + tasks.length + " 张") : "等待画布发送图片";
        el.empty.className = "empty-state" + (tasks.length ? " hidden" : "");
        for (i = 0; i < tasks.length; i += 1) {
            var task = tasks[i];
            html += '<article class="gallery-tile" tabindex="0" data-task="' + escapeHtml(task.id) + '">' +
                '<img data-task-image="' + escapeHtml(task.id) + '" alt="' + escapeHtml(task.source_name || "画布图片") + '">' +
                '<span class="tile-badge' + (i === 0 ? " latest" : "") + '">' + (i === 0 ? "最新" : escapeHtml(statusText(task.status))) + '</span>' +
                '<span class="tile-status ' + escapeHtml(task.status || "pending") + '" title="' + escapeHtml(statusText(task.status)) + '"></span>' +
                '<div class="tile-copy"><strong>' + escapeHtml(task.source_name || "画布图片") + '</strong><span>' + escapeHtml(task.canvas_title || "智能画布") + '</span>' +
                (task.error ? '<span class="tile-error" title="' + escapeHtml(task.error) + '">' + escapeHtml(task.error) + '</span>' : "") + '</div>' +
                '</article>';
        }
        el.gallery.innerHTML = html;
        Array.prototype.forEach.call(el.gallery.querySelectorAll(".gallery-tile"), function (card) {
            var task = taskById(card.getAttribute("data-task"));
            var image = card.querySelector("img");
            image.addEventListener("load", function () { sizeGalleryCard(card); });
            loadThumbnail(task, image);
            sizeGalleryCard(card);
        });
        releaseUnusedImages();
        if (previewIndex >= tasks.length) { closePreview(); }
    }
    function fullPreviewUrl(task) {
        if (fullImageUrls[task.id]) { return Promise.resolve(fullImageUrls[task.id]); }
        return net.download(mediaPath(task, false)).then(function (buffer) {
            var extension = extensionForTask(task);
            var mime = extension === "png" ? "image/png" : "image/jpeg";
            fullImageUrls[task.id] = objectUrlFromBuffer(buffer, mime);
            return fullImageUrls[task.id];
        });
    }
    function showPreview(index) {
        if (!tasks.length) { return; }
        previewIndex = (index + tasks.length) % tasks.length;
        var task = tasks[previewIndex];
        el.preview.className = "preview-layer open";
        el.preview.setAttribute("aria-hidden", "false");
        el.previewName.textContent = task.source_name || "画布图片";
        el.previewCanvas.textContent = (task.canvas_title || "智能画布") + (task.error ? " · " + task.error : "");
        el.previewImage.src = thumbnailUrls[task.id] || "";
        el.previewPlace.disabled = !lastDocumentState.hasDocument;
        fullPreviewUrl(task).then(function (url) {
            if (tasks[previewIndex] && tasks[previewIndex].id === task.id) { el.previewImage.src = url; }
        }).catch(function (error) {
            setStatus("预览加载失败：" + (error.message || error), "error");
        });
    }
    function closePreview() {
        previewIndex = -1;
        el.preview.className = "preview-layer";
        el.preview.setAttribute("aria-hidden", "true");
        el.previewImage.removeAttribute("src");
    }
    function openChoice(task) {
        if (!task) { return; }
        choiceTaskId = task.id;
        el.choiceName.textContent = task.source_name || "画布图片";
        el.choicePlace.disabled = !lastDocumentState.hasDocument;
        el.choice.className = "modal-layer open";
        el.choice.setAttribute("aria-hidden", "false");
    }
    function closeChoice() {
        el.choice.className = "modal-layer";
        el.choice.setAttribute("aria-hidden", "true");
        choiceTaskId = "";
    }
    function tempFile(name, extension) {
        var safe = String(name || "canvas-image").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 50);
        return path.join(os.tmpdir(), "infinite_canvas_" + safe + "_" + Date.now() + "." + extension);
    }
    function writeArrayBuffer(filePath, buffer) {
        var bytes = new Uint8Array(buffer);
        var nodeBuffer = new Buffer(bytes.length);
        var i;
        for (i = 0; i < bytes.length; i += 1) { nodeBuffer[i] = bytes[i]; }
        fs.writeFileSync(filePath, nodeBuffer);
    }
    function downloadTaskFile(task) {
        var filePath = tempFile(task.source_name, extensionForTask(task));
        return net.download(mediaPath(task, false)).then(function (buffer) {
            writeArrayBuffer(filePath, buffer);
            return filePath;
        });
    }
    function reportOpenFailure(task, error) {
        return net.post("/api/photoshop-bridge/tasks/" + encodeURIComponent(task.id) + "/open-failed", {
            client_instance_id:instanceId,
            error:String(error && (error.message || error) || "打开失败")
        }).then(function (data) {
            mergeTask(data.task);
        }).catch(function () {});
    }
    function performNewDocumentOpen(task, reportState) {
        if (!task || opening[task.id]) { return Promise.resolve(); }
        opening[task.id] = true;
        setStatus("正在打开“" + (task.source_name || "画布图片") + "”…");
        return downloadTaskFile(task).then(function (filePath) {
            return cep.call("bridgeOpenDocument", [filePath]);
        }).then(function () {
            if (!reportState) { return null; }
            return net.post("/api/photoshop-bridge/tasks/" + encodeURIComponent(task.id) + "/opened", {
                client_instance_id:instanceId
            });
        }).then(function (data) {
            if (data && data.task) { mergeTask(data.task); }
            setStatus("已在 Photoshop 中打开“" + (task.source_name || "画布图片") + "”。", "success");
            pollDocumentState();
        }).catch(function (error) {
            setStatus("打开失败：" + (error.message || error), "error");
            if (reportState) { return reportOpenFailure(task, error); }
        }).then(function () {
            delete opening[task.id];
        });
    }
    function claimAndOpen(task, automatic) {
        if (!task || opening[task.id]) { return Promise.resolve(); }
        if (task.status !== "pending" && task.status !== "open_failed") {
            return performNewDocumentOpen(task, false);
        }
        opening[task.id] = true;
        setStatus((automatic ? "收到新图片，" : "") + "正在认领打开任务…");
        return net.post("/api/photoshop-bridge/tasks/" + encodeURIComponent(task.id) + "/claim", {
            client_instance_id:instanceId
        }).then(function (data) {
            mergeTask(data.task);
            delete opening[task.id];
            if (!data.should_open) {
                if (automatic) { setStatus("图片已由另一个 Photoshop 客户端接收。"); }
                return null;
            }
            return performNewDocumentOpen(data.task, true);
        }).catch(function (error) {
            delete opening[task.id];
            setStatus("认领任务失败：" + (error.message || error), "error");
        });
    }
    function placeTask(task) {
        if (!task || !lastDocumentState.hasDocument) {
            setStatus("请先打开一个 Photoshop 文档。", "error");
            return;
        }
        setStatus("正在置入智能对象…");
        downloadTaskFile(task).then(function (filePath) {
            return cep.call("bridgePlaceSmartObject", [filePath]);
        }).then(function () {
            setStatus("已作为智能对象置入当前文档。", "success");
            pollDocumentState();
        }).catch(function (error) {
            setStatus("置入失败：" + (error.message || error), "error");
        });
    }
    function refreshTasks(openNewTasks) {
        return net.get("/api/photoshop-bridge/tasks?limit=50").then(function (data) {
            var incoming = data.tasks || [];
            var newlyReceived = [];
            incoming.forEach(function (task) {
                if (inboxBaselineReady && task && task.id && !socketHistoryIds[task.id]) {
                    newlyReceived.push(task);
                }
                if (task && task.id) { socketHistoryIds[task.id] = true; }
            });
            tasks = incoming.filter(function (task) {
                return !task || !task.id || !hiddenTaskIds[task.id];
            });
            tasks.sort(function (a, b) { return Number(b.created_at || 0) - Number(a.created_at || 0); });
            renderGallery();
            inboxBaselineReady = true;
            if (openNewTasks) {
                newlyReceived.forEach(function (task) {
                    if (task.status !== "pending" && task.status !== "open_failed") { return; }
                    setTargetCanvas(task.canvas_id, task.canvas_title);
                    claimAndOpen(task, true);
                });
            }
        });
    }
    function loadCanvases() {
        return Promise.all([net.get("/api/canvases"), net.get("/api/projects")]).then(function (results) {
            projects = {};
            (results[1].projects || []).forEach(function (project) { projects[project.id] = project.name || "默认项目"; });
            canvases = (results[0].canvases || []).filter(function (canvas) { return canvas.kind === "smart"; });
            canvases.sort(function (a, b) { return Number(b.updated_at || 0) - Number(a.updated_at || 0); });
            if (!canvasById(targetCanvasId)) { targetCanvasId = canvases[0] ? canvases[0].id : ""; }
            setTargetCanvas(targetCanvasId);
        });
    }
    function renderCanvasList(query) {
        var keyword = String(query || "").toLowerCase().replace(/^\s+|\s+$/g, "");
        var filtered = canvases.filter(function (canvas) {
            var projectName = projects[canvas.project] || "默认项目";
            return !keyword || String(canvas.title || "").toLowerCase().indexOf(keyword) >= 0 || projectName.toLowerCase().indexOf(keyword) >= 0;
        });
        var groups = {};
        var groupList = [];
        filtered.forEach(function (canvas) {
            var projectId = String(canvas.project || "__default__");
            var projectName = projects[canvas.project] || "默认项目";
            if (!groups[projectId]) {
                groups[projectId] = {id:projectId, name:projectName, canvases:[]};
                groupList.push(groups[projectId]);
            }
            groups[projectId].canvases.push(canvas);
        });
        groupList.sort(function (a, b) {
            return String(a.name).localeCompare(String(b.name), "zh-CN");
        });
        el.canvasList.innerHTML = groupList.length ? groupList.map(function (group) {
            var containsTarget = group.canvases.some(function (canvas) { return canvas.id === targetCanvasId; });
            var collapsed = !keyword && !containsTarget && Boolean(collapsedProjects[group.id]);
            var cards = group.canvases.map(function (canvas) {
                return '<button class="canvas-card' + (canvas.id === targetCanvasId ? " selected" : "") + '" type="button" data-canvas="' + escapeHtml(canvas.id) + '">' +
                    '<span class="canvas-icon">' + escapeHtml(canvasIcon(canvas)) + '</span><span class="canvas-card-copy"><strong>' + escapeHtml(canvas.title || "智能画布") +
                    '</strong><span>' + escapeHtml(formatTime(canvas.updated_at)) + '</span></span></button>';
            }).join("");
            return '<section class="canvas-project' + (collapsed ? " collapsed" : "") + '" data-project="' + escapeHtml(group.id) + '">' +
                '<button class="canvas-project-heading" type="button" data-project-toggle="' + escapeHtml(group.id) + '" aria-expanded="' + (collapsed ? "false" : "true") + '">' +
                '<span class="project-chevron">⌄</span><strong>' + escapeHtml(group.name) + '</strong><span class="project-count">' +
                group.canvases.length + ' 个画布</span></button><div class="canvas-project-items">' + cards + '</div></section>';
        }).join("") : '<div class="empty-state"><strong>没有匹配的智能画布</strong></div>';
    }
    function formatTime(timestamp) {
        if (!timestamp) { return "未记录时间"; }
        var date = new Date(Number(timestamp));
        return (date.getMonth() + 1) + "/" + date.getDate() + " " +
            ("0" + date.getHours()).slice(-2) + ":" + ("0" + date.getMinutes()).slice(-2);
    }
    function openCanvasPicker() {
        el.picker.className = "modal-layer open";
        el.picker.setAttribute("aria-hidden", "false");
        el.canvasSearch.value = "";
        renderCanvasList("");
        setTimeout(function () { el.canvasSearch.focus(); }, 20);
    }
    function closeCanvasPicker() {
        el.picker.className = "modal-layer";
        el.picker.setAttribute("aria-hidden", "true");
    }
    function renderDocumentState(state) {
        lastDocumentState = state || {hasDocument:false, selection:null};
        if (!lastDocumentState.hasDocument) {
            el.activeDocument.textContent = "没有活动文档";
            el.scopeText.textContent = "打开文档后即可发送";
        } else {
            el.activeDocument.textContent = lastDocumentState.name || "当前 Photoshop 文档";
            if (lastDocumentState.selection) {
                el.scopeText.textContent = "将按选区 " + lastDocumentState.selection.width + " × " + lastDocumentState.selection.height + " px 裁切";
            } else {
                el.scopeText.textContent = "将发送整张画布";
            }
        }
        el.previewPlace.disabled = !lastDocumentState.hasDocument;
        el.choicePlace.disabled = !lastDocumentState.hasDocument;
        renderSendState();
    }
    function pollDocumentState() {
        cep.call("bridgeDocumentState", []).then(renderDocumentState).catch(function () {
            renderDocumentState({hasDocument:false, selection:null});
        });
    }
    function renderSendState() {
        var canvas = canvasById(targetCanvasId);
        if (canvas) { el.targetName.textContent = canvas.title || "智能画布"; }
        el.send.disabled = !connected || !lastDocumentState.hasDocument || !targetCanvasId;
    }
    function confirmSmallSelection(bounds, continuation) {
        smallSelectionContinue = continuation;
        el.smallText.textContent = "当前选区为 " + bounds.width + " × " + bounds.height + " px，宽或高小于 64px。仍然发送可能得到过小图片。";
        el.smallDialog.className = "modal-layer open";
        el.smallDialog.setAttribute("aria-hidden", "false");
    }
    function closeSmallSelection() {
        el.smallDialog.className = "modal-layer";
        el.smallDialog.setAttribute("aria-hidden", "true");
        smallSelectionContinue = null;
    }
    function executeSendToCanvas(documentState) {
        var canvas = canvasById(targetCanvasId);
        if (!canvas) {
            setStatus("请先选择目标智能画布。", "error");
            return;
        }
        var useSelection = Boolean(documentState.selection);
        var filePath = tempFile("Photoshop-send", "png");
        el.send.disabled = true;
        setStatus(useSelection ? "正在按选区裁切并导出…" : "正在导出整张 Photoshop 画布…");
        cep.call("bridgeExportDocument", [filePath, useSelection]).then(function (exported) {
            var base64 = fs.readFileSync(exported.path).toString("base64");
            setStatus("正在上传 Photoshop 图片…");
            return net.post("/api/ai/upload-base64", {
                data:base64,
                name:(exported.name || "Photoshop") + ".png",
                content_type:"image/png"
            }).then(function (uploaded) {
                return {exported:exported, uploaded:uploaded};
            });
        }).then(function (result) {
            var uploadedFile = (result.uploaded.files || [])[0];
            if (!uploadedFile || !uploadedFile.url) { throw new Error("服务器没有返回上传地址"); }
            return net.post("/api/photoshop-bridge/canvases/" + encodeURIComponent(canvas.id) + "/images", {
                url:uploadedFile.url,
                name:uploadedFile.name || "Photoshop.png",
                export_scope:result.exported.scope || "document",
                selection_bounds:result.exported.selection || {}
            });
        }).then(function () {
            setStatus("已发送到智能画布“" + (canvas.title || "智能画布") + "”。", "success");
        }).catch(function (error) {
            setStatus("发送失败：" + (error.message || error), "error");
        }).then(renderSendState);
    }
    function sendToCanvas() {
        if (el.send.disabled) { return; }
        cep.call("bridgeDocumentState", []).then(function (state) {
            renderDocumentState(state);
            if (!state.hasDocument) { throw new Error("Photoshop 中没有打开的文档"); }
            var selection = state.selection;
            if (selection && (selection.width < 64 || selection.height < 64)) {
                confirmSmallSelection(selection, function () { executeSendToCanvas(state); });
                return;
            }
            executeSendToCanvas(state);
        }).catch(function (error) {
            setStatus("无法读取当前文档：" + (error.message || error), "error");
        });
    }
    function openSocket() {
        if (socket) { try { socket.close(); } catch (ignore) {} }
        clearTimeout(reconnectTimer);
        clearInterval(heartbeatTimer);
        socketGeneration += 1;
        var generation = socketGeneration;
        var clientId = "photoshop-canvas-bridge:" + instanceId;
        var url = "ws://" + net.state.host + "/ws/stats?client_id=" + encodeURIComponent(clientId) +
            "&access_token=" + encodeURIComponent(net.state.token);
        var currentSocket = new WebSocket(url);
        socket = currentSocket;
        currentSocket.onopen = function () {
            if (generation !== socketGeneration) { return; }
            lastSocketActivity = Date.now();
            setConnected(true);
            refreshTasks(true).catch(function () {});
            heartbeatTimer = setInterval(function () {
                if (generation !== socketGeneration || currentSocket.readyState !== WebSocket.OPEN) { return; }
                if (Date.now() - lastSocketActivity > 45000) {
                    setStatus("实时连接已中断，正在重新连接…", "error");
                    try { currentSocket.close(); } catch (ignoreClose) {}
                    return;
                }
                try { currentSocket.send("ping"); } catch (ignorePing) {}
            }, 15000);
        };
        currentSocket.onmessage = function (event) {
            if (generation !== socketGeneration) { return; }
            lastSocketActivity = Date.now();
            var message;
            try { message = JSON.parse(event.data); } catch (ignore) { return; }
            if (message.type === "photoshop_edit_requested" && message.task) {
                var isHistorical = Boolean(socketHistoryIds[message.task.id]);
                socketHistoryIds[message.task.id] = true;
                if (hiddenTaskIds[message.task.id]) { return; }
                mergeTask(message.task);
                if (isHistorical) { return; }
                setTargetCanvas(message.task.canvas_id, message.task.canvas_title);
                claimAndOpen(message.task, true);
            }
        };
        currentSocket.onclose = function () {
            if (generation !== socketGeneration) { return; }
            clearInterval(heartbeatTimer);
            socket = null;
            setConnected(false);
            if (net.state.token) { reconnectTimer = setTimeout(openSocket, 3000); }
        };
        currentSocket.onerror = function () {
            if (generation !== socketGeneration) { return; }
            try { currentSocket.close(); } catch (ignore) {}
        };
    }
    function connect() {
        var host = net.cleanHost(el.server.value);
        var username = String(el.username.value || "").replace(/^\s+|\s+$/g, "");
        var password = String(el.password.value || "");
        if (!host) { setStatus("请填写画布服务地址。", "error"); return; }
        if (connected) {
            switchAccount();
            return;
        }
        net.state.host = host;
        localStorage.setItem(LS_HOST, host);
        el.connect.disabled = true;
        var wantsLogin = Boolean(username || password);
        if (wantsLogin && (!username || !password)) {
            setStatus("请输入完整的账号和密码。", "error");
            el.connect.disabled = false;
            return;
        }
        var authentication = wantsLogin ? net.login(username, password) :
            (net.state.token ? Promise.resolve({access_token:net.state.token}) : Promise.reject(new Error("请输入账号和密码")));
        authentication.then(function (data) {
            if (data.access_token) {
                net.state.token = data.access_token;
                localStorage.setItem(LS_TOKEN, data.access_token);
            }
            return net.get("/api/auth/me");
        }).then(function (data) {
            currentUser = data.user || null;
            clearTaskInbox(true);
            cep.requestPersistent();
            setStatus("当前账号：" + ((currentUser && (currentUser.name || currentUser.username)) || "未知用户") +
                "。收件箱只显示此账号的互传记录。", "success");
            return Promise.all([refreshTasks(), loadCanvases()]);
        }).then(function () {
            openSocket();
            pollDocumentState();
            clearInterval(documentPollTimer);
            documentPollTimer = setInterval(pollDocumentState, 1500);
        }).catch(function (error) {
            net.state.token = "";
            currentUser = null;
            localStorage.removeItem(LS_TOKEN);
            setConnected(false);
            setStatus("连接失败：" + (error.message || error), "error");
        }).then(function () { el.connect.disabled = false; });
    }

    el.connect.addEventListener("click", connect);
    el.clear.addEventListener("click", function () {
        clearTaskInbox(false);
        setStatus("已清空当前窗口的图片；需要恢复时可点击旁边的刷新按钮。", "success");
    });
    el.refresh.addEventListener("click", function () {
        hiddenTaskIds = {};
        refreshTasks().then(loadCanvases).catch(function (error) {
            setStatus("刷新失败：" + (error.message || error), "error");
        });
    });
    el.gallery.addEventListener("click", function (event) {
        var card = event.target.closest ? event.target.closest("[data-task]") : null;
        if (!card) { return; }
        clearTimeout(tileClickTimer);
        var id = card.getAttribute("data-task");
        tileClickTimer = setTimeout(function () {
            var index = tasks.map(function (task) { return task.id; }).indexOf(id);
            showPreview(index);
        }, 220);
    });
    el.gallery.addEventListener("dblclick", function (event) {
        var card = event.target.closest ? event.target.closest("[data-task]") : null;
        if (!card) { return; }
        clearTimeout(tileClickTimer);
        openChoice(taskById(card.getAttribute("data-task")));
    });
    el.previewPrevious.addEventListener("click", function () { showPreview(previewIndex - 1); });
    el.previewNext.addEventListener("click", function () { showPreview(previewIndex + 1); });
    el.previewClose.addEventListener("click", closePreview);
    el.preview.addEventListener("mousedown", function (event) {
        if (event.target === el.preview) { closePreview(); }
    });
    el.previewOpen.addEventListener("click", function () {
        var task = tasks[previewIndex];
        if (task) { claimAndOpen(task, false); }
    });
    el.previewPlace.addEventListener("click", function () { placeTask(tasks[previewIndex]); });
    el.choiceOpen.addEventListener("click", function () {
        var task = taskById(choiceTaskId);
        closeChoice();
        claimAndOpen(task, false);
    });
    el.choicePlace.addEventListener("click", function () {
        var task = taskById(choiceTaskId);
        closeChoice();
        placeTask(task);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-close-dialog]"), function (button) {
        button.addEventListener("click", closeChoice);
    });
    el.targetButton.addEventListener("click", openCanvasPicker);
    Array.prototype.forEach.call(document.querySelectorAll("[data-close-picker]"), function (button) {
        button.addEventListener("click", closeCanvasPicker);
    });
    el.canvasSearch.addEventListener("input", function () { renderCanvasList(el.canvasSearch.value); });
    el.canvasSearch.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") { return; }
        var cards = el.canvasList.querySelectorAll("[data-canvas]");
        if (cards.length === 1) { cards[0].click(); return; }
        var exact = canvases.filter(function (canvas) {
            return String(canvas.title || "").toLowerCase() === String(el.canvasSearch.value || "").toLowerCase().replace(/^\s+|\s+$/g, "");
        });
        if (exact.length === 1) { setTargetCanvas(exact[0].id); closeCanvasPicker(); }
    });
    el.canvasList.addEventListener("click", function (event) {
        var toggle = event.target.closest ? event.target.closest("[data-project-toggle]") : null;
        if (toggle) {
            var projectId = toggle.getAttribute("data-project-toggle");
            var section = toggle.parentElement;
            var willCollapse = !section.classList.contains("collapsed");
            collapsedProjects[projectId] = willCollapse;
            section.classList.toggle("collapsed", willCollapse);
            toggle.setAttribute("aria-expanded", willCollapse ? "false" : "true");
            return;
        }
        var card = event.target.closest ? event.target.closest("[data-canvas]") : null;
        if (!card) { return; }
        setTargetCanvas(card.getAttribute("data-canvas"));
        closeCanvasPicker();
    });
    el.send.addEventListener("click", sendToCanvas);
    el.smallCancel.addEventListener("click", closeSmallSelection);
    el.smallContinue.addEventListener("click", function () {
        var continuation = smallSelectionContinue;
        closeSmallSelection();
        if (continuation) { continuation(); }
    });
    [el.choice, el.picker].forEach(function (layer) {
        layer.addEventListener("mousedown", function (event) {
            if (event.target !== layer) { return; }
            if (layer === el.choice) { closeChoice(); }
            else { closeCanvasPicker(); }
        });
    });
    global.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closePreview();
            closeChoice();
            closeCanvasPicker();
            closeSmallSelection();
        }
    });
    global.addEventListener("resize", function () {
        Array.prototype.forEach.call(el.gallery.querySelectorAll(".gallery-tile"), sizeGalleryCard);
    });

    loadState();
    // Photoshop Persistent 必须在面板生命周期尽可能早地注册；登录成功后还会再确认一次。
    cep.requestPersistent();
    renderGallery();
    renderDocumentState({hasDocument:false, selection:null});
    if (net.state.token) { connect(); }
}(this));
