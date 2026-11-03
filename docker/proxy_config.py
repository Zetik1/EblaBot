import json
import os
import logging
import signal
from threading import Event
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yaml


def build_config(subscription_url: str) -> dict:
    if urlparse(subscription_url).scheme != "https":
        raise ValueError("PROXY_SUBSCRIPTION_URL must be an HTTPS URL")
    probe = "https://api.telegram.org/"
    return {
        "mixed-port": 7890,
        "allow-lan": True,
        "bind-address": "*",
        "mode": "rule",
        "external-controller": "127.0.0.1:9090",
        "log-level": "warning",
        "proxy-providers": {
            "subscription": {
                "type": "file",
                "path": "./subscription.yaml",
                "interval": 10,
                "health-check": {
                    "enable": True, "url": probe, "interval": 60,
                    "timeout": 5000, "lazy": False,
                    "expected-status": "200/302",
                },
            }
        },
        "proxy-groups": [{
            "name": "telegram-fastest", "type": "url-test",
            "use": ["subscription"], "proxies": ["REJECT"], "url": probe,
            "interval": 60, "tolerance": 50, "lazy": False,
            "expected-status": "200/302",
        }],
        "rules": [
            "DOMAIN,api.telegram.org,telegram-fastest",
            "DOMAIN-SUFFIX,telegram.org,telegram-fastest",
            "MATCH,REJECT",
        ],
    }


MAX_SUBSCRIPTION_BYTES = 2 * 1024 * 1024


def validate_subscription(content: bytes) -> None:
    document = yaml.safe_load(content)
    proxies = document.get("proxies") if isinstance(document, dict) else None
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("Subscription must contain a nonempty proxy list")
    names = set()
    for proxy in proxies:
        if not isinstance(proxy, dict):
            raise ValueError("Invalid proxy entry")
        name = proxy.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Missing or duplicate proxy name")
        if name in {"REJECT", "DIRECT", "COMPATIBLE", "telegram-fastest"}:
            raise ValueError("Reserved proxy name")
        if not isinstance(proxy.get("type"), str) or not proxy["type"]:
            raise ValueError("Missing proxy type")
        names.add(name)


def write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def refresh_subscription(subscription_url: str, destination: Path) -> bool:
    try:
        request = Request(subscription_url, headers={"User-Agent": "clash.meta"})
        with urlopen(request, timeout=30) as response:
            content = response.read(MAX_SUBSCRIPTION_BYTES + 1)
        if len(content) > MAX_SUBSCRIPTION_BYTES:
            raise ValueError("Subscription exceeds size limit")
        validate_subscription(content)
        write_atomic(destination, content)
    except Exception as error:
        logging.warning("Subscription refresh failed (%s); keeping saved list", type(error).__name__)
        return False
    logging.info("Subscription refreshed")
    return True


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    subscription_url = os.environ["PROXY_SUBSCRIPTION_URL"]
    config = build_config(subscription_url)
    directory = Path("/config")
    destination = directory / "subscription.yaml"
    ready = directory / "ready"
    ready.unlink(missing_ok=True)
    stop = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.is_set():
        refreshed = refresh_subscription(subscription_url, destination)
        if destination.exists() and not ready.exists():
            try:
                validate_subscription(destination.read_bytes())
            except (ValueError, yaml.YAMLError):
                logging.warning("Saved subscription is invalid; waiting for download")
            else:
                write_atomic(directory / "config.yaml", json.dumps(config).encode())
                ready.touch()
        stop.wait(3600 if refreshed else 60)


if __name__ == "__main__":
    run()
