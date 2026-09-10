import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class StaticCacheStampTests(unittest.TestCase):
    """缓存戳必须由内容决定，而不是由文件 mtime 决定。

    mtime 会因为检出、复制、解压或 touch 而变化；一旦戳跟着 mtime 走，
    启动时的 sync_static_html_versions() 就会把所有引用它的 HTML 重写一遍，
    源码树里就会长期挂着十几个「已修改但其实没改」的页面。
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.static_dir = os.path.join(self.temp_dir.name, "static")
        os.makedirs(self.static_dir, exist_ok=True)
        self.js_path = os.path.join(self.static_dir, "app.js")
        self.html_path = os.path.join(self.static_dir, "page.html")
        Path(self.js_path).write_bytes(b"console.log(1);\n")
        Path(self.html_path).write_text(
            '<script src="/static/app.js?v=2020.01.01"></script>\n', encoding="utf-8"
        )
        self.patchers = [
            patch.object(main, "STATIC_DIR", self.static_dir),
            patch.object(main, "current_app_version", return_value="2026.09.10"),
            patch.object(main, "STATIC_STAMP_CACHE", {}),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def render(self):
        html = Path(self.html_path).read_text(encoding="utf-8")
        return main.versioned_static_html(html)

    def expected_stamp(self, payload: bytes):
        return hashlib.sha1(payload).hexdigest()[:10]

    def expected_version(self, payload: bytes):
        return "?v=2026.09.10." + self.expected_stamp(payload)

    def test_same_content_with_newer_mtime_keeps_the_same_stamp(self):
        first = self.render()
        newer = time.time() + 3600
        os.utime(self.js_path, (newer, newer))
        Path(self.js_path).chmod(0o644)

        second = self.render()

        self.assertEqual(first, second)
        self.assertIn(self.expected_version(b"console.log(1);\n"), second)

    def test_content_change_updates_the_stamp(self):
        first = self.render()
        Path(self.js_path).write_bytes(b"console.log(2);\n")

        second = self.render()

        self.assertNotEqual(first, second)
        self.assertIn(self.expected_version(b"console.log(2);\n"), second)

    def test_sync_stops_rewriting_html_when_content_is_unchanged(self):
        main.sync_static_html_versions()
        after_first = Path(self.html_path).read_text(encoding="utf-8")
        self.assertIn(self.expected_version(b"console.log(1);\n"), after_first)

        newer = time.time() + 7200
        os.utime(self.js_path, (newer, newer))
        main.sync_static_html_versions()

        self.assertEqual(Path(self.html_path).read_text(encoding="utf-8"), after_first)

    def test_line_ending_change_keeps_the_same_stamp(self):
        first = self.render()
        Path(self.js_path).write_bytes(b"console.log(1);\r\n")

        second = self.render()

        self.assertEqual(first, second)
        self.assertIn(self.expected_version(b"console.log(1);\n"), second)

    def test_missing_asset_falls_back_to_the_bare_version(self):
        Path(self.js_path).unlink()

        rendered = self.render()

        self.assertIn("?v=2026.09.10", rendered)
        self.assertNotIn("?v=2020.01.01", rendered)


if __name__ == "__main__":
    unittest.main()
