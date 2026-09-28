import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

from fastapi import HTTPException
from PIL import Image


def decode_dib(dib):
    """把 CF_DIB 字节还原成 PIL 图片，用来验证编码结果（不接触真实剪贴板）。"""
    header = struct.unpack("<IiiHHIIiiII", dib[:40])
    size, width, height, planes, bits, compression, size_image = header[:7]
    if size != 40 or bits != 24 or compression != 0 or height <= 0:
        raise AssertionError(f"unexpected DIB header: {header}")
    row_bytes = width * 3
    stride = (row_bytes + 3) & ~3
    body = dib[40:]
    rows = [body[row * stride:row * stride + row_bytes] for row in range(height)]
    rows.reverse()
    return Image.frombytes("RGB", (width, height), b"".join(rows), "raw", "BGR")


def sample_image(width=7, height=5):
    """构造红蓝通道差异明显的样本图，R/B 互换时对比会失败。"""
    image = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), ((x * 37) % 256, (y * 53) % 256, (x * 11 + y * 7) % 256))
    return image


class SystemClipboardEncodingTests(unittest.TestCase):
    """服务端剪贴板回退必须写出 Photoshop 能识别的 CF_DIB，且像素逐一还原。"""

    def test_dib_round_trip_preserves_every_pixel(self):
        source = sample_image()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            source.save(path)
            dib = main.image_to_dib_bytes(path)

        decoded = decode_dib(dib)

        self.assertEqual(decoded.size, source.size)
        self.assertEqual(decoded.tobytes(), source.tobytes())
        # CF_DIB 要求 BGR 排列，按 RGB 写入会让红蓝互换。
        self.assertEqual(decoded.getpixel((1, 0)), (37, 0, 11))

    def test_dib_header_describes_a_bottom_up_24_bit_image(self):
        source = sample_image(5, 3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            source.save(path)
            dib = main.image_to_dib_bytes(path)

        header = struct.unpack("<IiiHHIIiiII", dib[:40])
        self.assertEqual(header[0], 40)
        self.assertEqual(header[1], 5)
        self.assertEqual(header[2], 3)
        self.assertEqual(header[3], 1)
        self.assertEqual(header[4], 24)
        self.assertEqual(header[5], 0)
        # 每行 15 字节补齐到 4 字节对齐后是 16 字节。
        self.assertEqual(header[6], 16 * 3)
        self.assertEqual(len(dib), 40 + 16 * 3)
        # 行自下而上：DIB 第一行是源图最后一行。
        bottom = source.crop((0, 2, 5, 3)).tobytes("raw", "BGR")
        self.assertEqual(dib[40:55], bottom)

    def test_dib_row_padding_is_zero_filled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            Image.new("RGB", (5, 1), (10, 20, 30)).save(path)
            dib = main.image_to_dib_bytes(path)

        self.assertEqual(dib[40:55], bytes([30, 20, 10]) * 5)
        self.assertEqual(dib[55:56], b"\x00")

    def test_png_payload_is_reused_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            Image.new("RGB", (4, 4), (200, 100, 50)).save(path)

            self.assertEqual(main.image_to_clipboard_png_bytes(path), path.read_bytes())

    def test_other_formats_are_re_encoded_as_png(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.bmp"
            Image.new("RGB", (4, 4), (200, 100, 50)).save(path)
            payload = main.image_to_clipboard_png_bytes(path)

        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(payload)) as decoded:
            self.assertEqual(decoded.size, (4, 4))
            self.assertEqual(decoded.convert("RGB").getpixel((0, 0)), (200, 100, 50))


class SystemClipboardSourceTests(unittest.TestCase):
    """只有智能画布上的站内本地图片才允许写进服务端所在电脑的剪贴板。"""

    def test_endpoint_is_registered(self):
        paths = {getattr(route, "path", "") for route in main.app.routes}

        self.assertIn("/api/system-clipboard/image", paths)

    def test_rejects_non_smart_canvas(self):
        with self.assertRaises(HTTPException) as caught:
            main.system_clipboard_node_image({"kind": "whiteboard", "nodes": []}, "node-1", 0)

        self.assertEqual(caught.exception.status_code, 400)

    def test_rejects_unknown_node(self):
        with self.assertRaises(HTTPException) as caught:
            main.system_clipboard_node_image({"kind": "smart", "nodes": []}, "missing", 0)

        self.assertEqual(caught.exception.status_code, 404)

    def test_rejects_out_of_range_image_index(self):
        canvas = {"kind": "smart", "nodes": [{"id": "node-1", "images": [{"url": "/assets/output/a.png"}]}]}

        with self.assertRaises(HTTPException) as caught:
            main.system_clipboard_node_image(canvas, "node-1", 3)

        self.assertEqual(caught.exception.status_code, 400)

    def test_rejects_images_without_a_local_file(self):
        canvas = {"kind": "smart", "nodes": [{"id": "node-1", "images": [{"url": "https://example.com/a.png"}]}]}

        with self.assertRaises(HTTPException) as caught:
            main.system_clipboard_node_image(canvas, "node-1", 0)

        self.assertEqual(caught.exception.status_code, 400)

    def test_rejects_non_image_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("not an image", encoding="utf-8")
            canvas = {"kind": "smart", "nodes": [{"id": "node-1", "images": [{"localUrl": "/assets/output/notes.txt"}]}]}

            with patch.object(main, "output_file_from_url", side_effect=lambda url: str(path) if url else None):
                with self.assertRaises(HTTPException) as caught:
                    main.system_clipboard_node_image(canvas, "node-1", 0)

        self.assertEqual(caught.exception.status_code, 400)

    def test_resolves_the_local_file_of_a_canvas_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            Image.new("RGB", (4, 4), (1, 2, 3)).save(path)
            canvas = {"kind": "smart", "nodes": [{"id": "node-1", "images": [{"localUrl": "/assets/output/sample.png"}]}]}

            with patch.object(main, "output_file_from_url", side_effect=lambda url: str(path) if url else None):
                resolved = main.system_clipboard_node_image(canvas, "node-1", 0)

        self.assertEqual(resolved, str(path))


class SystemClipboardPlatformTests(unittest.TestCase):
    def test_availability_matches_the_platform(self):
        self.assertEqual(main.system_clipboard_available(), os.name == "nt")

    def test_copy_refuses_on_unsupported_platforms(self):
        with patch.object(main, "system_clipboard_available", return_value=False):
            with self.assertRaises(RuntimeError):
                main.copy_image_to_system_clipboard("unused.png")


if __name__ == "__main__":
    unittest.main()
