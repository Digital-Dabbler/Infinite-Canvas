import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


SAMPLE_DEVICE_FLOW = (
    "请使用浏览器完成 OAuth Device Flow 登录。\n"
    "verification_uri: https://jimeng.jianying.com/ai-tool/cli-auth?verification_uri=https%3A%2F%2Fpassport%2Fscan%3Fuser_code%3Dabc\n"
    "user_code: abc123\n"
    "device_code: dev123\n"
    "poll_interval: 1s\n"
    "expires_at: 2026-09-08T15:00:00+08:00\n"
)


class JimengDeviceFlowFieldTests(unittest.TestCase):
    def test_device_fields_parsed_from_cli_lines(self):
        fields = main.jimeng_login_device_fields(SAMPLE_DEVICE_FLOW)
        self.assertEqual(fields.get("verification_uri"), SAMPLE_DEVICE_FLOW.splitlines()[1].split(": ", 1)[1])
        self.assertEqual(fields.get("user_code"), "abc123")
        self.assertEqual(fields.get("device_code"), "dev123")
        self.assertEqual(fields.get("poll_interval"), "1s")
        self.assertEqual(fields.get("expires_at"), "2026-09-08T15:00:00+08:00")

    def test_device_fields_empty_on_plain_text(self):
        self.assertEqual(main.jimeng_login_device_fields("已登录，无需重新授权"), {})
        self.assertEqual(main.jimeng_login_device_fields(""), {})

    def test_duplicate_key_keeps_first_value(self):
        text = "user_code: first\nuser_code: second\n"
        fields = main.jimeng_login_device_fields(text)
        self.assertEqual(fields.get("user_code"), "first")


class JimengLoginQrFromTextRegressionTests(unittest.TestCase):
    """旧实现会把 device-flow 的 verification_uri 网页地址当 qr_url 返回，
    前端按 <img> 渲染就出现空白二维码框。新版只接受真正的图片/深链数据。"""

    def test_webpage_url_is_never_treated_as_qr(self):
        # 该输入模拟新 CLI 输出：只有 device-flow 字段，没有二维码图片。
        self.assertEqual(main.jimeng_login_qr_from_text(SAMPLE_DEVICE_FLOW), "")

    def test_data_image_is_still_a_qr(self):
        data_url = "data:image/png;base64,AAAA"
        text = f"qr: {data_url}\n"
        self.assertEqual(main.jimeng_login_qr_from_text(text), data_url)

    def test_dreamina_deep_link_is_still_a_qr(self):
        deep = "dreamina://login/abc"
        text = f"scan: {deep}\n"
        self.assertEqual(main.jimeng_login_qr_from_text(text), deep)

    def test_plain_http_is_empty(self):
        self.assertEqual(main.jimeng_login_qr_from_text("visit https://example.com/foo"), "")


if __name__ == "__main__":
    unittest.main()
