import asyncio
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main


LOGIN_HTML = (ROOT / "static" / "login.html").read_text(encoding="utf-8")


class AuthUsernameValidationTests(unittest.TestCase):
    def test_registration_rejects_email_username_without_silently_rewriting_it(self):
        payload = main.AuthRegisterRequest(
            username="designer@example.com",
            password="a-secure-password",
            name="设计师",
            department="UI",
        )

        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main.auth_register(payload))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("字母、数字、点、下划线或连字符", caught.exception.detail)

    def test_registration_form_applies_the_same_username_contract(self):
        self.assertIn('maxlength="40"', LOGIN_HTML)
        self.assertIn('pattern="[A-Za-z0-9._-]{3,40}"', LOGIN_HTML)
        self.assertIn("不能使用邮箱", LOGIN_HTML)


if __name__ == "__main__":
    unittest.main()
