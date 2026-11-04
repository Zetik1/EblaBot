import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from config import PROJECT_ROOT, Settings


class SettingsValidationTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.values = {
            "bot_token": "123456:local-test-token", "owner_id": 1, "group_id": -1
        }

    def test_owner_must_be_positive_and_group_must_be_negative(self):
        for field, invalid_values in (
            ("owner_id", (0, -1, "not-an-id")),
            ("group_id", (0, 1, "not-an-id")),
        ):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    values = {**self.values, field: value}
                    with self.assertRaises(ValidationError) as caught:
                        Settings(_env_file=None, **values)
                    self.assertEqual(caught.exception.errors()[0]["loc"], (field,))
                    self.assertNotIn(self.values["bot_token"], str(caught.exception))

    def test_absolute_and_relative_database_paths(self):
        absolute = Path(tempfile.gettempdir()) / "eblabot-test.sqlite3"
        for supplied, expected in (
            (absolute, absolute),
            (Path("custom/test.sqlite3"), PROJECT_ROOT / "custom/test.sqlite3"),
        ):
            with self.subTest(path=supplied):
                settings = Settings(_env_file=None, **self.values, database_path=supplied)
                self.assertEqual(settings.database_path, expected)

    def test_environment_values_and_optional_proxy(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": self.values["bot_token"], "OWNER_ID": "42",
            "GROUP_ID": "-100123", "DATABASE_PATH": "custom/db.sqlite3",
            "TELEGRAM_PROXY_URL": "socks5://127.0.0.1:1080",
        }):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.owner_id, 42)
        self.assertEqual(settings.group_id, -100123)
        self.assertEqual(settings.database_path, PROJECT_ROOT / "custom/db.sqlite3")
        self.assertEqual(settings.telegram_proxy_url, "socks5://127.0.0.1:1080")
        self.assertIsNone(Settings(_env_file=None, **self.values).telegram_proxy_url)

    def test_environment_overrides_utf8_env_file_and_extra_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                f"BOT_TOKEN={self.values['bot_token']}\nOWNER_ID=1\nGROUP_ID=-1\n"
                "DATABASE_PATH=данные/bot.sqlite3\nFUTURE_SETTING=ignored\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GROUP_ID": "-999"}):
                settings = Settings(_env_file=env_file)
        self.assertEqual(settings.group_id, -999)
        self.assertEqual(settings.owner_id, 1)
        self.assertEqual(settings.database_path, PROJECT_ROOT / "данные/bot.sqlite3")
