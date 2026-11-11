# -*- coding: utf-8 -*-
"""RunningHub 图层拆分（Dola Seedream 5.5 Layer Decomposition）纯函数适配层。

本模块只负责两件事，不做任何网络或磁盘 IO，方便离线单元测试：
1. build_layer_body：按产品页参数拼提交 body（图片 URL 必须已上传为外链）。
2. parse_layer_decomposition_results：把任务 query 返回的 results[] 整理成画布节点
   node.psd.layers 需要的结构（原图像素坐标，底图恒为第 0 层）。

实测事实（2026-11-11/12 两次成功任务验证）：
- 每个图层 PNG 的画布尺寸等于自身包围盒尺寸（内容在盒内可有内缩透明边），
  因此前端把整幅 PNG 拉伸贴到 bbox 矩形即像素正确。
- boundingBox 同时给出 absolute[x1,y1,x2,y2]（原图像素）与 normalized[0-1000 千分比]；
  个别结果可能缺 absolute，用 normalized/1000*底图宽高换算。键名即语义，不做数值猜测。
- 底图 zindex=0、通常无 name、bbox 为 null、background=opaque，size 为整画布尺寸。
"""

import re
from typing import Any, Dict, List, Tuple

LAYER_DECOMPOSE_MODEL = "seedream-v5.5-layer-decomposition"
LAYER_DECOMPOSE_PATH = "seedream-v5.5/layer-decomposition"
# RunningHub 图层拆分对输入图有最低分辨率要求：长边不足 512 像素时上游直接以
# errorCode 1013「文件处理失败」拒绝（两台项目实测一致），提交前先在本地拦截。
LAYER_MIN_EDGE = 512
LAYER_PROMPT_MAX = 2000
LAYER_DEFAULT_MAX_IMAGES = 10
LAYER_MIN_MAX_IMAGES = 2
LAYER_MAX_MAX_IMAGES = 100
LAYER_RESOLUTIONS = {"auto", "1k", "1.5k", "2k"}
_SIZE_RE = re.compile(r"^\s*(\d+)\s*[xX*×]\s*(\d+)\s*$")
_Z_KEYS = ("zindex", "zIndex", "z_index", "z")


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def build_layer_body(
    image_url: str,
    prompt: str = "",
    resolution: str = "auto",
    max_images: int = LAYER_DEFAULT_MAX_IMAGES,
    safety_identifier: str = "",
) -> Dict[str, Any]:
    """拼装 layer-decomposition 提交 body；绝不包含 apiKey 字段（Bearer-only 鉴权）。"""
    url = str(image_url or "").strip()
    if not url:
        raise ValueError("缺少已上传的图片 URL")
    body: Dict[str, Any] = {
        "imageUrl": url,
        "resolution": str(resolution or "auto").strip().lower() or "auto",
        "outputFormat": "png",
        # 产品页把布尔项声明为字符串枚举，沿用旧实测可用的写法。
        "watermark": "false",
        "optimizePromptMode": "standard",
        "maxImages": _clamp_int(max_images, LAYER_DEFAULT_MAX_IMAGES, LAYER_MIN_MAX_IMAGES, LAYER_MAX_MAX_IMAGES),
        "textRecognition": "false",
    }
    text = str(prompt or "").strip()
    if text:
        body["prompt"] = text[:LAYER_PROMPT_MAX]
    safety = str(safety_identifier or "").strip()
    if safety:
        body["safetyIdentifier"] = safety[:200]
    return body


def _parse_size(value: Any) -> Tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(float(value[0])), int(float(value[1]))
        except (TypeError, ValueError):
            return 0, 0
    match = _SIZE_RE.match(str(value or ""))
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


def _extract_items(payload: Any) -> List[dict]:
    """query 原始响应 → results 列表；容忍直接传列表。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for container in (payload, payload.get("data"), payload.get("result")):
        if isinstance(container, dict):
            for key in ("results", "layers", "list"):
                items = container.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
    return []


def _bbox_of(item: dict) -> Dict[str, Any]:
    box = item.get("boundingBox")
    if box is None:
        box = item.get("bounding_box")
    if isinstance(box, (list, tuple)):
        # 直接给 [x1,y1,x2,y2] 序列时按原图像素处理。
        return {"absolute": list(box)}
    return box if isinstance(box, dict) else {}


def _floats(values: Any, count: int = 4) -> List[float]:
    if not isinstance(values, (list, tuple)) or len(values) < count:
        return []
    try:
        return [float(v) for v in values[:count]]
    except (TypeError, ValueError):
        return []


def _box_to_rect(box: Dict[str, Any], layer_w: int, layer_h: int, base_w: int, base_h: int) -> Tuple[int, int, int, int]:
    """盒子 → (x, y, w, h) 原图像素。absolute=像素，normalized=千分比，缺失则铺满（底图）。"""
    nums = _floats(box.get("absolute"))
    if nums:
        x1, y1, x2, y2 = nums
    else:
        nums = _floats(box.get("normalized"))
        if nums and base_w > 0 and base_h > 0:
            x1, y1, x2, y2 = (nums[0] / 1000.0 * base_w, nums[1] / 1000.0 * base_h,
                              nums[2] / 1000.0 * base_w, nums[3] / 1000.0 * base_h)
        else:
            # 无盒子：底图路径（或异常结果），铺满整幅。
            return 0, 0, (base_w or layer_w), (base_h or layer_h)
    width = x2 - x1
    height = y2 - y1
    if width <= 0:
        width = layer_w or width
    if height <= 0:
        height = layer_h or height
    return int(round(x1)), int(round(y1)), max(1, int(round(width))), max(1, int(round(height)))


def _z_of(item: dict, index: int) -> Tuple[bool, float]:
    for key in _Z_KEYS:
        if item.get(key) is not None:
            try:
                return True, float(item.get(key))
            except (TypeError, ValueError):
                return False, float(index)
    return False, float(index)


def parse_layer_decomposition_results(payload: Any, base_w: int, base_h: int, id_seed: str = "psd") -> Dict[str, Any]:
    """results[] → {layers:[...], strategy, baseIndex}；layers[0] 恒为底图。

    layers 项：id/name/description/url/x/y/width/height/zIndex/visible/isBase。
    """
    items = _extract_items(payload)
    if not items:
        return {"layers": [], "strategy": "empty", "baseIndex": -1}

    z_pairs = [_z_of(item, index) for index, item in enumerate(items)]
    explicit_z = sum(1 for has_z, _ in z_pairs if has_z)
    # 只有多个显式 z 才敢排序；否则尊重数组顺序（旧实测数组序即叠放序）。
    order = (sorted(range(len(items)), key=lambda i: z_pairs[i][1])
             if explicit_z > 1 else list(range(len(items))))

    layers: List[Dict[str, Any]] = []
    base_done = False
    for slot, index in enumerate(order):
        item = items[index]
        url = str(item.get("url") or item.get("fileUrl") or item.get("imageUrl") or "").strip()
        if not url:
            continue
        layer_w, layer_h = _parse_size(item.get("size") or item.get("imageSize") or item.get("resolution"))
        box = _bbox_of(item)
        x, y, w, h = _box_to_rect(box, layer_w, layer_h, base_w, base_h)
        name = str(item.get("name") or item.get("label") or "").strip()
        description = str(item.get("description") or "").strip()
        background = str(item.get("background") or "").strip().lower()
        z_present, z_value = z_pairs[index]
        is_base = (not box and not name) or (z_present and z_value == 0) or (background == "opaque" and not base_done)
        if is_base and not base_done:
            base_done = True
            x, y = 0, 0
            w = w or base_w
            h = h or base_h
            name = name or "背景"
        elif not base_done and slot == len(order) - 1:
            # 全列表都找不到明显底图时，最后一张兜底当底图，保证 layers[0] 契约。
            is_base = True
            base_done = True
            x, y = 0, 0
            w, h = base_w or w, base_h or h
            name = name or "背景"
        if not name:
            name = f"图层 {len(layers) + 1}"
        layers.append({
            "name": name,
            "description": description,
            "url": url,
            "x": x,
            "y": y,
            "width": int(w),
            "height": int(h),
            "visible": True,
            "isBase": bool(is_base),
        })

    # 底图挪回 0 位（若上游把底图排在中间），统一重编号与 zIndex。
    base_items = [layer for layer in layers if layer.get("isBase")]
    if len(base_items) > 1:
        for extra in base_items[1:]:
            extra["isBase"] = False
    if base_items and layers[0] is not base_items[0]:
        layers.remove(base_items[0])
        layers.insert(0, base_items[0])
    for z, layer in enumerate(layers):
        layer["zIndex"] = z
        layer["id"] = f"{'base' if layer['isBase'] else 'layer'}_{z}_{id_seed}"
    if layers and not layers[0]["isBase"]:
        layers[0]["isBase"] = True
        layers[0]["x"], layers[0]["y"] = 0, 0
    strategy = "result_fields" if any(_bbox_of(item) for item in items) else "array_order"
    return {"layers": layers, "strategy": strategy, "baseIndex": 0 if layers else -1}


def normalize_psd_layers(node_psd: Any) -> Dict[str, Any]:
    """把 node.psd 里前端可编辑的 layers 数组按当前顺序重编 zIndex，并保证底图在 0 位。

    导出 PSD 与回读共用：调用方传入编辑后的 layers（可见性、坐标、顺序可被用户改过），
    返回带稳定 id 与 zIndex 的规范化副本。"""
    if not isinstance(node_psd, dict):
        return {}
    layers = [dict(item) for item in (node_psd.get("layers") or []) if isinstance(item, dict)]
    bases = [item for item in layers if item.get("isBase")]
    if bases:
        base = bases[0]
        layers = [base] + [item for item in layers if item is not base]
        for extra in bases[1:]:
            extra["isBase"] = False
    for z, layer in enumerate(layers):
        layer["zIndex"] = z
        layer["x"] = int(layer.get("x") or 0)
        layer["y"] = int(layer.get("y") or 0)
        layer["width"] = max(1, int(layer.get("width") or 1))
        layer["height"] = max(1, int(layer.get("height") or 1))
        layer["visible"] = bool(layer.get("visible", True))
        layer["isBase"] = bool(layer.get("isBase")) or z == 0
    node_psd = dict(node_psd)
    node_psd["layers"] = layers
    return node_psd
