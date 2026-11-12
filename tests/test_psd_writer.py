# -*- coding: utf-8 -*-
"""psd_writer 的纯 unittest 测试：无网络，直调 build_psd 并用内置极简 PSD
解析函数（struct 手写）逐字段核对文件头、图层记录、flags、luni 名、通道 Raw
数据与合并预览。解析约定与 psd_writer.py 的写出约定一一对应。
"""

import io
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import psd_writer


def _png_bytes(size, color, mode="RGBA"):
    """用 Pillow 生成纯色 PNG 字节（size=(w,h)）。"""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------- 极简 PSD 解析器（仅覆盖本 writer 会产出的结构） ----------

def _parse_psd(data: bytes) -> dict:
    """按大端序逐段解析 build_psd 的输出，返回结构化 dict。

    返回键：magic/version/channels/width/height/depth/mode/layer_count/
    records（PSD 文件顺序：顶层在前，每项含 bounds、chans、blend、opacity、
    flags、pascal、luni、data）/image_comp/image_data。
    """
    magic, version, reserved = struct.unpack_from(">4sH6s", data, 0)
    channels, height, width, depth, mode = struct.unpack_from(">HIIHH", data, 12)
    result = {
        "magic": magic,
        "version": version,
        "reserved": reserved,
        "channels": channels,
        "height": height,
        "width": width,
        "depth": depth,
        "mode": mode,
    }
    off = 26
    (cm_len,) = struct.unpack_from(">I", data, off)
    off += 4 + cm_len
    (ir_len,) = struct.unpack_from(">I", data, off)
    off += 4 + ir_len
    (lm_len,) = struct.unpack_from(">I", data, off)
    lm_end = off + 4 + lm_len
    off += 4
    (li_len,) = struct.unpack_from(">I", data, off)
    li_end = off + 4 + li_len

    p = off + 4
    (layer_count,) = struct.unpack_from(">H", data, p)
    p += 2
    result["layer_count"] = layer_count
    records = []
    for _ in range(layer_count):
        rec = {}
        rec["bounds"] = struct.unpack_from(">iiii", data, p)  # top,left,bottom,right
        p += 16
        (nch,) = struct.unpack_from(">H", data, p)
        p += 2
        rec["chans"] = [struct.unpack_from(">hI", data, p + i * 6) for i in range(nch)]
        p += 6 * nch
        blend_sig, blend_mode, opacity, clip, flags, filler = struct.unpack_from(
            ">4s4sBBBB", data, p
        )
        p += 12
        rec.update(blend_sig=blend_sig, blend=blend_mode, opacity=opacity,
                   clip=clip, flags=flags, filler=filler)
        (extra_len,) = struct.unpack_from(">I", data, p)
        p += 4
        extra = data[p:p + extra_len]
        p += extra_len
        # 解析 extra：mask(u32 长) + ranges(u32 长) + PASCAL 名 + 若干 '8BIM' 块
        # （Adobe 规范：两个长度字段都是 4 字节；按 u16 解析会与 Photoshop 错位）
        e = 0
        (mask_len,) = struct.unpack_from(">I", extra, e)
        e += 4 + mask_len
        (range_len,) = struct.unpack_from(">I", extra, e)
        e += 4 + range_len
        name_len = extra[e]
        rec["pascal"] = extra[e + 1:e + 1 + name_len].decode("ascii", "ignore")
        pascal_field = ((1 + name_len + 3) // 4) * 4  # 已补零到 4 倍数
        e += pascal_field
        rec["luni"] = None
        # '8BIM' 块结构：签名 4 + key 4 + 长度 u32 4 = 头 12 字节，随后是数据
        while e + 12 <= len(extra):
            sig, key, blk_len = struct.unpack_from(">4s4sI", extra, e)
            if sig == b"8BIM" and key == b"luni":
                payload = extra[e + 12:e + 12 + blk_len]
                (nchars,) = struct.unpack_from(">I", payload, 0)
                rec["luni"] = payload[4:4 + nchars * 2].decode("utf-16-be")
                break
            e += 12 + blk_len
        records.append(rec)
    # 通道图像数据：所有记录之后，按记录顺序、每层每通道 (i16 压缩 + len-2 字节)
    q = p
    for rec in records:
        planes = {}
        for ctype, clen in rec["chans"]:
            (comp,) = struct.unpack_from(">h", data, q)
            q += 2
            planes[ctype] = (comp, data[q:q + clen - 2])
            q += clen - 2
        rec["data"] = planes
    # 严格自洽：记录+通道数据必须正好落在 layer_info 段尾的 0..3 字节补齐范围内，
    # 任何长度字段写错（如 mask/blending 用 u16）都会让 Photoshop 读出文件尾。
    assert 0 <= li_end - q < 4, "通道数据结束位置 %r 与 layer_info 段尾 %r 不自洽" % (q, li_end)
    result["records"] = records
    # Image Data 段
    off = lm_end
    (image_comp,) = struct.unpack_from(">H", data, off)
    off += 2
    n = channels * width * height
    result["image_comp"] = image_comp
    result["image_data"] = data[off:off + n]
    result["trailing"] = data[off + n:]
    return result


def _planes_of(rec):
    """取某图层记录 4 个通道的裸字节，按 R/G/B/A 排成列表。"""
    return [rec["data"][t][1] for t in (0, 1, 2, -1)]


# ---------- 用例 ----------

class PsdWriterTwoLayerTests(unittest.TestCase):
    """核心用例：底图 RGB 4×4 全红 + 上层 RGBA 2×2 半透明蓝（盒 (1,1,2,2)）。"""

    def _layers(self):
        return [
            {"png_bytes": _png_bytes((4, 4), (255, 0, 0), "RGB"),
             "x": 0, "y": 0, "w": 4, "h": 4, "name": "底图", "visible": True},
            {"png_bytes": _png_bytes((2, 2), (0, 0, 255, 128)),
             "x": 1, "y": 1, "w": 2, "h": 2, "name": "上层·蓝", "visible": False},
        ]

    def test_header_and_structure(self):
        parsed = _parse_psd(psd_writer.build_psd(4, 4, self._layers()))
        self.assertEqual(parsed["magic"], b"8BPS")
        self.assertEqual(parsed["version"], 1)
        self.assertEqual(parsed["reserved"], b"\x00" * 6)
        self.assertEqual(parsed["channels"], 4)
        self.assertEqual((parsed["width"], parsed["height"]), (4, 4))
        self.assertEqual(parsed["depth"], 8)
        self.assertEqual(parsed["mode"], 3)
        self.assertEqual(parsed["layer_count"], 2)
        self.assertEqual(parsed["trailing"], b"")  # 各段长度自洽，文件无多余尾巴

    def test_bottom_layer_written_first(self):
        """PSD 记录顺序必须"自底向上"：layers[0]（底图）应排在记录 0。

        （psd-tools 源码与 Photoshop 实测一致：record[0]=背景层；
        曾反序写导致 PS 面板图层顺序整体颠倒。）
        """
        parsed = _parse_psd(psd_writer.build_psd(4, 4, self._layers()))
        rec_base, rec_top = parsed["records"][0], parsed["records"][1]
        self.assertEqual(rec_base["luni"], "底图")
        self.assertEqual(rec_top["luni"], "上层·蓝")   # 中文名靠 luni 块还原
        self.assertEqual(rec_top["pascal"], "")         # 非 ASCII 名截断为空
        self.assertEqual(rec_base["bounds"], (0, 0, 4, 4))
        self.assertEqual(rec_top["bounds"], (1, 1, 3, 3))    # top,left,bottom,right

    def test_visible_flag_blend_and_opacity(self):
        parsed = _parse_psd(psd_writer.build_psd(4, 4, self._layers()))
        rec_base, rec_top = parsed["records"]
        self.assertEqual(rec_top["flags"] & 1, 1)   # visible=False → bit0 置位（隐藏）
        self.assertEqual(rec_base["flags"] & 1, 0)  # visible=True  → bit0 为 0
        self.assertEqual(rec_top["blend_sig"], b"8BIM")  # 规范：混合签名恒为 '8BIM'
        self.assertEqual(rec_top["blend"], b"norm")
        self.assertEqual(rec_top["opacity"], 255)
        self.assertEqual(rec_top["clip"], 0)
        self.assertEqual(rec_top["filler"], 0)

    def test_channel_records_and_raw_lengths(self):
        parsed = _parse_psd(psd_writer.build_psd(4, 4, self._layers()))
        for rec, (w, h) in zip(parsed["records"], [(4, 4), (2, 2)]):
            self.assertEqual([t for t, _ in rec["chans"]], [0, 1, 2, -1])
            for _ctype, clen in rec["chans"]:
                self.assertEqual(clen, 2 + w * h)     # 长度含 2 字节压缩标志
            for ctype in (0, 1, 2, -1):
                comp, raw = rec["data"][ctype]
                self.assertEqual(comp, 0)             # Raw 无压缩
                self.assertEqual(len(raw), w * h)     # 每通道裸数据 = w*h

    def test_merged_preview_pixels(self):
        """合并预览：长度=width*height*4；蓝叠红像素按 alpha≈128 混色。"""
        parsed = _parse_psd(psd_writer.build_psd(4, 4, self._layers()))
        self.assertEqual(parsed["image_comp"], 0)
        planes = parsed["image_data"]
        self.assertEqual(len(planes), 4 * 4 * 4)
        r, g, b, a = (planes[i * 16:(i + 1) * 16] for i in range(4))

        def px(x, y):
            idx = y * 4 + x
            return (r[idx], g[idx], b[idx], a[idx])

        # 盒外：纯红底（RGB 底图转 RGBA 后 alpha=255）
        self.assertEqual(px(0, 0), (255, 0, 0, 255))
        self.assertEqual(px(3, 0), (255, 0, 0, 255))
        # 盒内 (1,1)：src=(0,0,255,128) over dst=(255,0,0,255)
        # R = 255*(1-128/255) ≈ 127，B = 255*128/255 ≈ 128，容差 ±1 防取整差异
        mr, mg, mb, ma = px(1, 1)
        self.assertIn(mr, (126, 127, 128))
        self.assertEqual(mg, 0)
        self.assertIn(mb, (127, 128, 129))
        self.assertEqual(ma, 255)
        self.assertEqual(px(2, 2), px(1, 1))  # 2×2 半透明蓝均匀，盒内各点一致


class PsdWriterStretchTests(unittest.TestCase):
    """渲染语义：PNG 整幅等比拉伸铺满目标盒，通道取色与源一致。"""

    def test_solid_color_stretched_to_box(self):
        png = _png_bytes((4, 4), (20, 60, 140, 255))  # 源 4×4，目标盒 2×3
        layers = [{"png_bytes": png, "x": 0, "y": 0, "w": 2, "h": 3,
                   "name": "L", "visible": True}]
        parsed = _parse_psd(psd_writer.build_psd(2, 3, layers))
        rec = parsed["records"][0]
        self.assertEqual(rec["bounds"], (0, 0, 3, 2))
        pr, pg, pb, pa = _planes_of(rec)
        self.assertEqual(len(pr), 2 * 3)
        self.assertTrue(all(v == 20 for v in pr))
        self.assertTrue(all(v == 60 for v in pg))
        self.assertTrue(all(v == 140 for v in pb))
        self.assertTrue(all(v == 255 for v in pa))


class PsdWriterNegativeBoundsTests(unittest.TestCase):
    """负坐标与超画布盒：bounds 有符号原样写入，不裁剪。"""

    def test_negative_and_oversized_bounds(self):
        png = _png_bytes((2, 2), (9, 9, 9, 255))
        wide = _png_bytes((4, 4), (30, 30, 30, 255))
        layers = [
            {"png_bytes": wide, "x": 0, "y": 0, "w": 4, "h": 4, "name": "底", "visible": True},
            {"png_bytes": png, "x": -3, "y": -2, "w": 2, "h": 2, "name": "负", "visible": True},
            {"png_bytes": png, "x": 2, "y": 2, "w": 4, "h": 4, "name": "出界", "visible": True},
        ]
        parsed = _parse_psd(psd_writer.build_psd(4, 4, layers))
        recs = parsed["records"]  # 自底向上：底、负、出界
        self.assertEqual(recs[0]["bounds"], (0, 0, 4, 4))
        self.assertEqual(recs[1]["bounds"], (-2, -3, 0, -1))  # 负坐标原样写
        self.assertEqual(recs[2]["bounds"], (2, 2, 6, 6))     # 盒超出画布按原值写
        # 通道数据仍为完整 w*h
        self.assertEqual(len(recs[0]["data"][0][1]), 4 * 4)
        self.assertEqual(len(recs[1]["data"][0][1]), 2 * 2)
        # 越界层被合并预览自动裁剪：画布角仍是底图颜色
        planes = parsed["image_data"]
        self.assertEqual(planes[1 * 16 + 0], 30)   # 左上角 G=30（底图灰）
        self.assertEqual(planes[3 * 16 + 0], 255)  # alpha=255


class PsdWriterEmptyLayersTests(unittest.TestCase):
    """无图层：仍产出合法 PSD，合并预览为透明底。"""

    def test_no_layers(self):
        parsed = _parse_psd(psd_writer.build_psd(2, 2, []))
        self.assertEqual(parsed["magic"], b"8BPS")
        self.assertEqual(parsed["layer_count"], 0)
        self.assertEqual(parsed["records"], [])
        self.assertEqual(parsed["image_comp"], 0)
        self.assertEqual(len(parsed["image_data"]), 2 * 2 * 4)
        self.assertEqual(parsed["image_data"], b"\x00" * 16)  # 全透明


if __name__ == "__main__":
    unittest.main()
