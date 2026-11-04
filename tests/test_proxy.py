import json
import runpy
import signal
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import yaml

from docker.proxy_config import (
    MAX_SUBSCRIPTION_BYTES,
    build_config,
    refresh_subscription,
    run,
    validate_subscription,
    write_atomic,
)


class ProxyConfigTests(unittest.TestCase):
    def test_only_telegram_uses_subscription_and_no_direct_fallback(self):
        config = build_config("https://example.com/private-subscription")
        self.assertEqual(config["rules"][-1], "MATCH,REJECT")
        self.assertEqual(config["proxy-groups"][0]["use"], ["subscription"])
        self.assertEqual(config["proxy-groups"][0]["url"], "https://api.telegram.org/")
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["REJECT"])
        self.assertNotIn("DIRECT", config["rules"])

    def test_insecure_subscription_rejected(self):
        for url in ("http://example.com/subscription", "file:///subscription", "", "example.com"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    build_config(url)


class SubscriptionValidationTests(unittest.TestCase):
    def test_nonempty_unique_proxy_list_is_accepted(self):
        content = yaml.safe_dump({"proxies": [
            {"name": "first", "type": "socks5"},
            {"name": "second", "type": "ss"},
        ]}).encode()
        validate_subscription(content)

    def test_invalid_documents_are_rejected(self):
        documents = (None, [], "text", {}, {"proxies": None}, {"proxies": {}}, {"proxies": []})
        for document in documents:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    validate_subscription(yaml.safe_dump(document).encode())

    def test_invalid_entries_names_and_types_are_rejected(self):
        entries = (
            None, "proxy", {}, {"name": "", "type": "ss"},
            {"name": 123, "type": "ss"}, {"name": "valid"},
            {"name": "valid", "type": ""}, {"name": "valid", "type": 123},
        )
        for entry in entries:
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    validate_subscription(yaml.safe_dump({"proxies": [entry]}).encode())

    def test_reserved_and_duplicate_names_are_rejected(self):
        for name in ("DIRECT", "REJECT", "COMPATIBLE", "telegram-fastest"):
            with self.subTest(name=name):
                content = yaml.safe_dump({"proxies": [{"name": name, "type": "ss"}]}).encode()
                with self.assertRaises(ValueError):
                    validate_subscription(content)
        duplicate = {"name": "same", "type": "ss"}
        with self.assertRaises(ValueError):
            validate_subscription(yaml.safe_dump({"proxies": [duplicate, duplicate]}).encode())

    def test_malformed_yaml_is_rejected(self):
        with self.assertRaises(yaml.YAMLError):
            validate_subscription(b"proxies: [")


class SubscriptionRefreshTests(unittest.TestCase):
    def test_size_limit_rejects_oversized_download_and_accepts_exact_boundary(self):
        prefix = b"proxies: [{name: example, type: socks5}]\n#"
        content = prefix + b"x" * (MAX_SUBSCRIPTION_BYTES - len(prefix))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subscription.yaml"
            target.write_bytes(b"saved")
            response = MagicMock()
            with patch("docker.proxy_config.urlopen", return_value=response) as download:
                response.__enter__.return_value.read.return_value = content + b"x"
                with self.assertLogs(level="WARNING"):
                    self.assertFalse(refresh_subscription("https://example.com/sub", target))
                self.assertEqual(target.read_bytes(), b"saved")
                response.__enter__.return_value.read.assert_called_once_with(
                    MAX_SUBSCRIPTION_BYTES + 1
                )
                response.__enter__.return_value.read.return_value = content
                self.assertTrue(refresh_subscription("https://example.com/sub", target))
                self.assertEqual(target.read_bytes(), content)
                self.assertEqual(download.call_args.kwargs["timeout"], 30)
                self.assertEqual(download.call_args.args[0].get_header("User-agent"), "clash.meta")

    def test_failed_atomic_replacement_keeps_previous_file_and_reports_failure(self):
        content = b"proxies: [{name: example, type: socks5}]"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subscription.yaml"
            target.write_bytes(b"saved")
            response = MagicMock()
            response.__enter__.return_value.read.return_value = content
            with (
                patch("docker.proxy_config.urlopen", return_value=response),
                patch.object(Path, "replace", side_effect=PermissionError("private path")),
                self.assertLogs(level="WARNING") as logs,
            ):
                self.assertFalse(refresh_subscription("https://example.com/sub", target))
            self.assertEqual(target.read_bytes(), b"saved")
            self.assertNotIn("private path", " ".join(logs.output))

    def test_atomic_write_creates_and_replaces_complete_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subscription.yaml"
            for content in (b"first", b"replacement"):
                write_atomic(target, content)
                self.assertEqual(target.read_bytes(), content)
                self.assertFalse(target.with_suffix(".yaml.tmp").exists())

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


class ProxyStartupTests(unittest.TestCase):
    def run_once(self, directory, *, content=None):
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        response = MagicMock()
        response.__enter__.return_value.read.return_value = content
        with (
            patch.dict("os.environ", {"PROXY_SUBSCRIPTION_URL": "https://example.com/sub"}),
            patch("docker.proxy_config.Path", return_value=directory),
            patch("docker.proxy_config.Event", return_value=stop),
            patch("docker.proxy_config.signal.signal") as signals,
            patch("docker.proxy_config.urlopen", return_value=response) as download,
        ):
            if content is None:
                download.side_effect = TimeoutError
            run()
        return stop, signals

    def test_first_success_creates_configuration_and_readiness(self):
        content = b"proxies: [{name: example, type: socks5}]"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            stop, signals = self.run_once(directory, content=content)
            self.assertEqual((directory / "subscription.yaml").read_bytes(), content)
            self.assertTrue((directory / "ready").exists())
            config = json.loads((directory / "config.yaml").read_bytes())
            self.assertEqual(config, build_config("https://example.com/sub"))
            stop.wait.assert_called_once_with(3600)
            self.assertEqual([call.args[0] for call in signals.call_args_list], [
                signal.SIGTERM, signal.SIGINT
            ])
            for call in signals.call_args_list:
                call.args[1](call.args[0], None)
            self.assertEqual(stop.set.call_count, 2)

    def test_saved_valid_subscription_allows_offline_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "subscription.yaml").write_bytes(
                b"proxies: [{name: example, type: socks5}]"
            )
            with self.assertLogs(level="WARNING"):
                stop, _ = self.run_once(directory)
            self.assertTrue((directory / "ready").exists())
            self.assertTrue((directory / "config.yaml").exists())
            stop.wait.assert_called_once_with(60)

    def test_missing_or_invalid_saved_subscription_does_not_mark_proxy_ready(self):
        for content in (None, b"proxies: []", b"proxies: ["):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "ready").touch()
                if content is not None:
                    (directory / "subscription.yaml").write_bytes(content)
                with self.assertLogs(level="WARNING"):
                    stop, _ = self.run_once(directory)
                self.assertFalse((directory / "ready").exists())
                self.assertFalse((directory / "config.yaml").exists())
                stop.wait.assert_called_once_with(60)

    def test_refresh_keeps_configuration_once_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            stop = MagicMock()
            stop.is_set.side_effect = [False, False, True]
            response = MagicMock()
            response.__enter__.return_value.read.side_effect = [
                b"proxies: [{name: first, type: socks5}]",
                b"proxies: [{name: second, type: socks5}]",
            ]
            with (
                patch.dict("os.environ", {"PROXY_SUBSCRIPTION_URL": "https://example.com/sub"}),
                patch("docker.proxy_config.Path", return_value=directory),
                patch("docker.proxy_config.Event", return_value=stop),
                patch("docker.proxy_config.signal.signal"),
                patch("docker.proxy_config.urlopen", return_value=response),
                patch("docker.proxy_config.write_atomic", wraps=write_atomic) as write,
            ):
                run()
            config_writes = [call for call in write.call_args_list if call.args[0].name == "config.yaml"]
            self.assertEqual(len(config_writes), 1)
            self.assertIn(b"second", (directory / "subscription.yaml").read_bytes())
            self.assertTrue((directory / "ready").exists())

    def test_console_start_clears_stale_readiness_and_honors_shutdown(self):
        script = Path(__file__).resolve().parents[1] / "docker" / "proxy_config.py"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "ready").touch()
            stop = Event()
            stop.set()
            with (
                patch.dict("os.environ", {"PROXY_SUBSCRIPTION_URL": "https://example.com/sub"}),
                patch("pathlib.Path", return_value=directory),
                patch("threading.Event", return_value=stop),
                patch("signal.signal"),
                patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected download")),
            ):
                runpy.run_path(str(script), run_name="__main__")
            self.assertFalse((directory / "ready").exists())
            self.assertFalse((directory / "subscription.yaml").exists())
