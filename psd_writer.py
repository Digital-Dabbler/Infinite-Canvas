# -*- coding: utf-8 -*-
"""最小 PSD 写出器：把 {底图 + 元素图层列表} 打包为 Photoshop 文件（版本 1，平面图层）。

服务于"图片转 PSD"功能：RunningHub Dola Seedream 5.5 层分解 API 会把原图拆成
1 张底图 + N 个元素图层，每个图层 PNG 是独立小画布，按其包围盒（bbox）贴回原图
坐标系。本模块把目标盒写入 PSD 图层 bounds，并把 PNG 等比拉伸铺满该盒后按通道
拆为 Raw 数据，最后附一张"全部图层视为可见"的合并预览。

纯本地实现：只依赖标准库与 Pillow（仓库 requirements.txt 已含 pillow），无网络。
PSD 数值一律大端序（struct 用 ``>`` 前缀）；对齐细节见各私有函数注释。
"""

import io
import struct

from PIL import Image

# ---------- 常量 ----------
# 文件头：签名 4s + 版本 H + 保留 6s + 通道数 H + 高 I + 宽 I + 位深 H + 色彩模式 H（共 26 字节）
_HEADER_FMT = ">4sH6sHIIHH"
_MAGIC = b"8BPS"
_VERSION = 1            # 版本 1：标准 PSD（非 PSB）
_CHANNEL_COUNT = 4      # R, G, B, A
_BIT_DEPTH = 8          # 每通道 8 位
_RGB_MODE = 3           # 色彩模式 3 = RGB
_RAW_COMPRESSION = 0    # 0 = Raw 无压缩
# 通道类型顺序：R=0、G=1、B=2、A=-1
_CHANNEL_TYPES = (0, 1, 2, -1)
_MAX_LAYERS = 0xFFFF    # PSD 版本 1 的图层数由 u16 表示


def _pascal_name(name: str) -> bytes:
    """图层名的 PASCAL 字符串：1 个长度字节 + ASCII 正文，总长补零到 4 字节倍数。

    PSD 规定该字段只容纳 ASCII；中文等非 ASCII 字符在此截断，完整名称由
    'luni' Unicode 块（见 :func:`_luni_block`）承载。
    """
    ascii_part = "".join(ch for ch in name if ord(ch) < 0x80).encode("ascii")[:0xFF]
    buf = bytes([len(ascii_part)]) + ascii_part
    buf += b"\x00" * ((-len(buf)) % 4)  # 补零到 4 字节倍数
    return buf


def _luni_block(name: str) -> bytes:
    """'8BIM' + 'luni' 附加图层信息块：u32 数据长度 +（u32 字符数 + UTF-16BE 串）。

    供 Photoshop 读取中文图层名；字符数按 UTF-16 码元计（len 字节数 // 2）。
    """
    encoded = name.encode("utf-16-be")
    payload = struct.pack(">I", len(encoded) // 2) + encoded
    return b"8BIMluni" + struct.pack(">I", len(payload)) + payload


def _decode_layer_png(png_bytes: bytes, w: int, h: int) -> Image.Image:
    """打开图层 PNG、统一转 RGBA，并整幅等比拉伸铺满目标盒 (w, h)。

    渲染语义与层分解 API 预览一致：不管原 PNG 画布尺寸，直接 resize 到盒尺寸
    （宽高比误差实测 <1%，无需保比居中）。w/h 必须为正。
    """
    if w <= 0 or h <= 0:
        raise ValueError("图层目标盒尺寸必须为正：w=%r h=%r" % (w, h))
    img = Image.open(io.BytesIO(png_bytes))
    img = img.convert("RGBA")
    if img.size != (w, h):
        img = img.resize((w, h))
    return img


def _channel_data(img: Image.Image) -> bytes:
    """把已拉伸到盒尺寸的 RGBA 图拆成 4 通道 Raw 数据。

    每通道 = i16 压缩标志(0=Raw) + w*h 字节；通道顺序与记录里的通道信息表
    一致：R、G、B、A。
    """
    parts = []
    for plane in img.split():  # RGBA 拆分得到 4 个单通道 L 图
        parts.append(struct.pack(">h", _RAW_COMPRESSION))
        parts.append(plane.tobytes())
    return b"".join(parts)


def _layer_record(layer: dict, img: Image.Image) -> bytes:
    """构造单个图层记录（不含通道图像数据，数据在所有记录之后统一写）。

    字段顺序：bounds(top,left,bottom,right i32) → u16 通道数 → 每通道
    (类型 i16 + 数据长度 u32，长度含 2 字节压缩标志) → 混合签名 '8BIM' +
    模式 'norm' + opacity/clipping/flags/filler 各 u8 → u32 附加数据长度 +
    附加数据（mask u32=0、blending ranges u32=0、PASCAL 名、luni 块，
    整体补零到 4 字节倍数）。

    注意两处 Adobe 规范硬约束（实测 Photoshop 打不开即出在这）：
    1) 混合模式签名必须是 '8BIM'（不是文件头 '8BPS'），否则 PS 报
       "这个文件使用了不受支持的混合模式"；
    2) 附加数据里的 mask 长度与 blending ranges 长度都是 u32（各 4 字节），
       若按 u16 写会少 4 字节，PS 把 PASCAL 名当 blending 长度解析，
       后续全部错位 → "意外地遇到文件尾"。

    bounds 是有符号 i32：x/y 允许为负、盒允许超出画布，按原值写入不裁剪。
    """
    x, y, w, h = int(layer["x"]), int(layer["y"]), int(layer["w"]), int(layer["h"])
    top, left, bottom, right = y, x, y + h, x + w

    out = [struct.pack(">iiii", top, left, bottom, right)]
    # 通道信息表：4 个通道（R=0,G=1,B=2,A=-1），每通道数据长度 = 2 + w*h
    out.append(struct.pack(">H", len(_CHANNEL_TYPES)))
    chan_len = 2 + w * h
    for ctype in _CHANNEL_TYPES:
        out.append(struct.pack(">hI", ctype, chan_len))
    # 混合：签名必须是 '8BIM'（规范原文 "Always '8BIM'"，不是文件头的 '8BPS'）
    # + 模式 'norm' + 不透明/剪辑/标志/填充
    flags = 0 if layer.get("visible", True) else 1  # bit0=1 表示隐藏
    out.append(struct.pack(">4s4sBBBB", b"8BIM", b"norm", 255, 0, flags, 0))

    # 附加图层数据：mask 长 u32 0 + blending ranges 长 u32 0 + PASCAL 名 + luni 块
    # （两个长度字段都是 4 字节，写成 u16 会让 Photoshop 错位解析后续全部崩坏）
    name = str(layer.get("name", ""))
    extra = struct.pack(">II", 0, 0) + _pascal_name(name) + _luni_block(name)
    extra += b"\x00" * ((-len(extra)) % 4)  # 补零到 4 倍数，保证下一条记录对齐
    out.append(struct.pack(">I", len(extra)))
    out.append(extra)
    return b"".join(out)


def _merge_image(width: int, height: int, layers: list, images: list) -> Image.Image:
    """生成合并预览：从底图开始逐层 alpha_composite 到整幅画布。

    按规格使用"全部图层可见"版本（忽略 visible=False 的隐藏标志）；
    无任何图层时得到透明底画布。负坐标用离屏 overlay 承载，paste 自动裁剪。
    """
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer, img in zip(layers, images):
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay.paste(img, (int(layer["x"]), int(layer["y"])))  # 越界部分自动裁剪
        canvas = Image.alpha_composite(canvas, overlay)
    return canvas


def build_psd(width: int, height: int, layers) -> bytes:
    """把图层列表打包为 Photoshop PSD（版本1，平面图层）。

    width/height：整幅画布像素尺寸（= 原图尺寸）。
    layers：列表，索引 0 = 最底层（底图）。每项 dict：
      - png_bytes: bytes，该层 PNG（RGBA 或 RGB；其画布尺寸可不同于目标盒）
      - x, y, w, h: int，该层在画布上的目标盒（像素）。渲染语义：整幅 png
        等比拉伸铺满该盒（宽高比≈盒，实测误差<1%，直接 resize 到 (w,h) 即可）
      - name: str（可含中文）
      - visible: bool，默认 True
    返回 PSD 文件字节。x/y 允许为负或盒超出画布（PSD 边界框是有符号的，
    按原值写入，不要裁剪）。
    """
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("画布尺寸必须为正：width=%r height=%r" % (width, height))
    if len(layers) > _MAX_LAYERS:
        # PSD 版本 1 的 layerCount 是 u16，超出属规格外
        raise ValueError("图层数量超过 PSD 版本1 上限 %d" % _MAX_LAYERS)

    # 先统一解码拉伸，合并预览与通道数据共用同一批 RGBA 图
    images = [
        _decode_layer_png(layer["png_bytes"], int(layer["w"]), int(layer["h"]))
        for layer in layers
    ]

    # ---- 文件头 + 空 Color Mode Data + 空 Image Resources ----
    chunks = [
        struct.pack(_HEADER_FMT, _MAGIC, _VERSION, b"\x00" * 6,
                    _CHANNEL_COUNT, height, width, _BIT_DEPTH, _RGB_MODE),
        struct.pack(">I", 0),  # Color Mode Data 长度 0
        struct.pack(">I", 0),  # Image Resources 长度 0
    ]

    # ---- Layer and Mask Information ----
    # 写入顺序为"顶层在前"：layers[0] 是底图，故整条记录流反序。
    ordered = list(reversed(layers))
    ordered_imgs = list(reversed(images))
    records = b"".join(_layer_record(l, im) for l, im in zip(ordered, ordered_imgs))
    channel_blocks = b"".join(_channel_data(im) for im in ordered_imgs)

    layer_info = struct.pack(">H", len(ordered)) + records + channel_blocks
    layer_info += b"\x00" * ((-len(layer_info)) % 4)  # 长度向上取整到 4 的倍数

    section = struct.pack(">I", len(layer_info)) + layer_info
    section += struct.pack(">I", 0)  # 全局图层遮罩信息长度 0
    section += b"\x00" * ((-len(section)) % 4)
    chunks.append(struct.pack(">I", len(section)))
    chunks.append(section)

    # ---- Image Data：u16 压缩=0(Raw) + 合并预览（R/G/B/A 四平面，行优先）----
    merged = _merge_image(width, height, layers, images)
    chunks.append(struct.pack(">H", _RAW_COMPRESSION))
    for plane in merged.split():
        chunks.append(plane.tobytes())

    return b"".join(chunks)
