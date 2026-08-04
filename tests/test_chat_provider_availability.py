import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class ChatProviderAvailabilityTests(unittest.TestCase):
    def test_api_chat_provider_requires_its_profile_key(self):
        provider = {
            "id": "example",
            "name": "Example",
            "protocol": "openai",
            "chat_models": ["chat-model"],
        }
        with patch.object(main, "provider_env_key_value", return_value=""):
            public = main.public_provider(provider, "team-a")
        self.assertFalse(public["chat_configured"])

        with patch.object(main, "provider_env_key_value", return_value="secret"):
            public = main.public_provider(provider, "team-a")
        self.assertTrue(public["chat_configured"])

    def test_local_cli_chat_provider_uses_installation_state(self):
        provider = {
            "id": "local-codex",
            "name": "OpenAI CLI",
            "protocol": "codex",
            "chat_models": ["gpt-5"],
        }
        with (
            patch.object(main, "provider_env_key_value", return_value=""),
            patch.object(main, "codex_cli_executable", return_value=""),
        ):
            self.assertFalse(main.public_provider(provider, "team-a")["chat_configured"])

        with (
            patch.object(main, "provider_env_key_value", return_value=""),
            patch.object(main, "codex_cli_executable", return_value="codex.exe"),
        ):
            self.assertTrue(main.public_provider(provider, "team-a")["chat_configured"])


if __name__ == "__main__":
    unittest.main()
