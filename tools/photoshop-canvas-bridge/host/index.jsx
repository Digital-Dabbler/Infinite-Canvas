#target photoshop

function bridgeEscapeJson(text) {
    return String(text)
        .replace(/\\/g, "\\\\")
        .replace(/"/g, '\\"')
        .replace(/\r/g, "\\r")
        .replace(/\n/g, "\\n")
        .replace(/\t/g, "\\t");
}

function bridgeJson(value) {
    if (value === null) { return "null"; }
    var type = typeof value;
    if (type === "string") { return '"' + bridgeEscapeJson(value) + '"'; }
    if (type === "number") { return isFinite(value) ? String(value) : "null"; }
    if (type === "boolean") { return value ? "true" : "false"; }
    if (value instanceof Array) {
        var arrayItems = [];
        for (var i = 0; i < value.length; i += 1) { arrayItems.push(bridgeJson(value[i])); }
        return "[" + arrayItems.join(",") + "]";
    }
    if (type === "object") {
        var objectItems = [];
        for (var key in value) {
            if (value.hasOwnProperty(key) && typeof value[key] !== "undefined") {
                objectItems.push('"' + bridgeEscapeJson(key) + '":' + bridgeJson(value[key]));
            }
        }
        return "{" + objectItems.join(",") + "}";
    }
    return "null";
}

function bridgeError(error) {
    return bridgeJson({error:String(error)});
}

function bridgePixelNumber(value) {
    var number = NaN;
    try {
        if (value && typeof value.as === "function") {
            number = Number(value.as("px"));
        }
    } catch (ignoreAs) {}
    if (!isFinite(number)) {
        try {
            if (value && typeof value.value !== "undefined") {
                number = Number(value.value);
            } else {
                number = Number(value);
            }
        } catch (ignoreValue) {}
    }
    return isFinite(number) ? number : null;
}

function bridgeDocumentInfo(doc) {
    var previousRulerUnits = app.preferences.rulerUnits;
    try {
        app.preferences.rulerUnits = Units.PIXELS;
        var width = bridgePixelNumber(doc.width);
        var height = bridgePixelNumber(doc.height);
        if (width === null || height === null) {
            throw new Error("无法读取 Photoshop 文档的像素尺寸");
        }
        return {
            documentId:Number(doc.id),
            name:String(doc.name),
            width:Math.round(width),
            height:Math.round(height)
        };
    } finally {
        app.preferences.rulerUnits = previousRulerUnits;
    }
}

function bridgeSelectionBounds(doc) {
    var previousRulerUnits = app.preferences.rulerUnits;
    try {
        app.preferences.rulerUnits = Units.PIXELS;
        var bounds = null;
        try {
            bounds = doc.selection.bounds;
        } catch (noSelection) {
            return null;
        }
        if (!bounds || bounds.length < 4) { return null; }
        var left = bridgePixelNumber(bounds[0]);
        var top = bridgePixelNumber(bounds[1]);
        var right = bridgePixelNumber(bounds[2]);
        var bottom = bridgePixelNumber(bounds[3]);
        var documentWidth = bridgePixelNumber(doc.width);
        var documentHeight = bridgePixelNumber(doc.height);
        if (
            left === null || top === null || right === null || bottom === null ||
            documentWidth === null || documentHeight === null
        ) {
            throw new Error("无法将 Photoshop 选区边界转换为像素");
        }
        left = Math.max(0, Math.min(documentWidth, Math.floor(left)));
        top = Math.max(0, Math.min(documentHeight, Math.floor(top)));
        right = Math.max(0, Math.min(documentWidth, Math.ceil(right)));
        bottom = Math.max(0, Math.min(documentHeight, Math.ceil(bottom)));
        if (right <= left || bottom <= top) {
            throw new Error("Photoshop 选区为空或没有与画布重叠");
        }
        return {
            left:left,
            top:top,
            right:right,
            bottom:bottom,
            width:right - left,
            height:bottom - top
        };
    } finally {
        app.preferences.rulerUnits = previousRulerUnits;
    }
}

function bridgeOpenDocument(filePath) {
    try {
        var file = new File(filePath);
        if (!file.exists) { return bridgeJson({error:"下载的临时图片不存在"}); }
        var doc = app.open(file);
        return bridgeJson(bridgeDocumentInfo(doc));
    } catch (error) {
        return bridgeError(error);
    }
}

function bridgePlaceSmartObject(filePath) {
    try {
        if (!app.documents.length) { return bridgeJson({error:"Photoshop 中没有打开的文档"}); }
        var file = new File(filePath);
        if (!file.exists) { return bridgeJson({error:"下载的临时图片不存在"}); }
        var descriptor = new ActionDescriptor();
        descriptor.putPath(charIDToTypeID("null"), file);
        descriptor.putEnumerated(
            charIDToTypeID("FTcs"),
            charIDToTypeID("QCSt"),
            charIDToTypeID("Qcsa")
        );
        executeAction(charIDToTypeID("Plc "), descriptor, DialogModes.NO);
        return bridgeJson(bridgeDocumentInfo(app.activeDocument));
    } catch (error) {
        return bridgeError(error);
    }
}

function bridgeDocumentState() {
    try {
        if (!app.documents.length) { return bridgeJson({hasDocument:false, selection:null}); }
        var info = bridgeDocumentInfo(app.activeDocument);
        info.hasDocument = true;
        info.selection = bridgeSelectionBounds(app.activeDocument);
        return bridgeJson(info);
    } catch (error) {
        return bridgeError(error);
    }
}

function bridgeExportDocument(filePath, cropSelection) {
    var source = null;
    var temporary = null;
    try {
        if (!app.documents.length) { return bridgeJson({error:"Photoshop 中没有打开的文档"}); }
        source = app.activeDocument;
        var selection = cropSelection ? bridgeSelectionBounds(source) : null;
        var target = new File(filePath);
        var options = new PNGSaveOptions();
        options.compression = 6;
        options.interlaced = false;
        temporary = source.duplicate("Infinite Canvas Export", false);
        if (selection) {
            temporary.crop([
                UnitValue(selection.left, "px"),
                UnitValue(selection.top, "px"),
                UnitValue(selection.right, "px"),
                UnitValue(selection.bottom, "px")
            ]);
        }
        temporary.flatten();
        temporary.saveAs(target, options, true, Extension.LOWERCASE);
        var result = {
            name:String(source.name).replace(/\.[^.]+$/, "") || "Photoshop",
            path:target.fsName,
            scope:selection ? "selection" : "document",
            selection:selection
        };
        temporary.close(SaveOptions.DONOTSAVECHANGES);
        temporary = null;
        app.activeDocument = source;
        return bridgeJson(result);
    } catch (error) {
        try {
            if (temporary) { temporary.close(SaveOptions.DONOTSAVECHANGES); }
            if (source) { app.activeDocument = source; }
        } catch (ignore) {}
        return bridgeError(error);
    }
}
