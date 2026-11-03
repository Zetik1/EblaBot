import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from docker.proxy_config import build_config, refresh_subscription, run


class ProxyConfigTests(unittest.TestCase):
    def test_only_telegram_uses_subscription_and_no_direct_fallback(self):
        config = build_config("https://example.com/private-subscription")
        self.assertEqual(config["rules"][-1], "MATCH,REJECT")
        self.assertEqual(config["proxy-groups"][0]["use"], ["subscription"])
        self.assertEqual(config["proxy-groups"][0]["url"], "https://api.telegram.org/")
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["REJECT"])
        self.assertNotIn("DIRECT", config["rules"])

    def test_insecure_subscription_rejected(self):
        with self.assertRaises(ValueError):
            build_config("http://example.com/subscription")


class SubscriptionRefreshTests(unittest.TestCase):
    def test_failed_or_invalid_download_preserves_saved_list(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subscription.yaml"
            target.write_bytes(b"previous working list")
            for content in (b"<html>error</html>", b"proxies: []", b"proxies: ["):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = content
                with patch("docker.proxy_config.urlopen", return_value=response):
                    self.assertFalse(refresh_subscription("https://example.com/sub", target))
                self.assertEqual(target.read_bytes(), b"previous working list")
            with patch("docker.proxy_config.urlopen", side_effect=URLError("secret URL")):
                with self.assertLogs(level="WARNING") as logs:
                    self.assertFalse(refresh_subscription("https://example.com/sub", target))
                self.assertNotIn("secret URL", str(logs.output))
            self.assertEqual(target.read_bytes(), b"previous working list")

    def test_success_replaces_subscription_after_failure(self):
        content = b"proxies: [{name: example, type: socks5, server: example.com, port: 443}]"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subscription.yaml"
            response = MagicMock()
            response.__enter__.return_value.read.return_value = content
            with patch("docker.proxy_config.urlopen", side_effect=TimeoutError):
                self.assertFalse(refresh_subscription("https://example.com/sub", target))
            self.assertFalse(target.exists())
            with patch("docker.proxy_config.urlopen", return_value=response):
                self.assertTrue(refresh_subscription("https://example.com/sub", target))
            self.assertEqual(target.read_bytes(), content)
            self.assertFalse(target.with_suffix(".yaml.tmp").exists())

    def test_scheduler_retries_failure_then_uses_hourly_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = MagicMock()
            stop.is_set.side_effect = [False, False, True]
            with (
                patch.dict("os.environ", {"PROXY_SUBSCRIPTION_URL": "https://example.com/sub"}),
                patch("docker.proxy_config.Path", return_value=Path(directory)),
                patch("docker.proxy_config.Event", return_value=stop),
                patch("docker.proxy_config.signal.signal"),
                patch("docker.proxy_config.refresh_subscription", side_effect=[False, True]),
            ):
                run()
            self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [60, 3600])
