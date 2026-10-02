from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Any, Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


JsonFetcher = Callable[[str], Awaitable[Any]]


class SmcDiscoveryError(RuntimeError):
    """Raised when an SMC source cannot provide its required inventory."""


@dataclass(frozen=True)
class SmcSourceConfig:
    host: str
    port: int = 80
    scheme: str = "http"
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        host = self.host.strip()
        scheme = self.scheme.strip().lower()
        if not host or any(char in host for char in "/?#"):
            raise ValueError("SMC host must be a hostname or IP address")
        if scheme not in {"http", "https"}:
            raise ValueError("SMC scheme must be http or https")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("SMC port must be between 1 and 65535")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "scheme", scheme)

    @property
    def base_url(self) -> str:
        default_port = 80 if self.scheme == "http" else 443
        authority = self.host if self.port == default_port else f"{self.host}:{self.port}"
        return f"{self.scheme}://{authority}"

    @property
    def key(self) -> str:
        return self.base_url


def smc_config_from_record(record: dict[str, Any]) -> SmcSourceConfig:
    return SmcSourceConfig(
        host=str(record.get("host") or "").strip(),
        port=int(record.get("port") or 80),
        scheme=str(record.get("scheme") or "http"),
    )


async def discover_smc(
    config: SmcSourceConfig,
    fetch_json: JsonFetcher | None = None,
) -> dict[str, Any]:
    fetch = fetch_json or _network_fetcher(config)
    required_paths = ("/api/localmachine", "/api/networkadapters")
    optional_paths = (
        "/api/chassis/stats",
        "/api/chassis/power/status",
        "/api/vfcs",
        "/api/session",
        "/api/smc",
    )

    required_results = await asyncio.gather(*(fetch(path) for path in required_paths), return_exceptions=True)
    for path, result in zip(required_paths, required_results, strict=True):
        if isinstance(result, BaseException):
            raise SmcDiscoveryError(f"SMC request failed for {path}: {result}") from result

    optional_results = await asyncio.gather(*(fetch(path) for path in optional_paths), return_exceptions=True)
    optional: dict[str, Any] = {}
    optional_errors: dict[str, str] = {}
    for path, result in zip(optional_paths, optional_results, strict=True):
        if isinstance(result, BaseException):
            optional_errors[path] = str(result)
        else:
            optional[path] = result

    machine = required_results[0]
    adapters = required_results[1]
    if not isinstance(machine, dict):
        raise SmcDiscoveryError("SMC localmachine response is not an object")
    if not isinstance(adapters, list):
        raise SmcDiscoveryError("SMC networkadapters response is not a list")

    hostname = str(machine.get("hostname") or config.host).strip()
    serial = str(machine.get("serial") or "").strip()
    model = str(machine.get("type") or "Disguise media server").strip()
    device_id = f"smc-{_slug(serial or hostname or config.host)}"
    interfaces = _network_interfaces(device_id, adapters)
    power = optional.get("/api/chassis/power/status")
    power_state = str(power.get("System Power") if isinstance(power, dict) else "unknown").strip().lower()
    status = "up" if power_state == "on" else "down" if power_state == "off" else "unknown"
    now_data = {
        "key": config.key,
        "host": config.host,
        "port": config.port,
        "scheme": config.scheme,
    }
    device = {
        "id": device_id,
        "name": hostname,
        "name_source": "smc",
        "ip": config.host,
        "management_ip": config.host,
        "vendor": "Disguise",
        "model": model,
        "serial": serial,
        "status": status,
        "device_type": "media-server",
        "fingerprint": f"smc+serial-{serial}" if serial else f"smc+hostname-{hostname}",
        "smc_source_key": config.key,
        "smc_hardware": optional.get("/api/smc") if isinstance(optional.get("/api/smc"), dict) else {},
        "chassis_stats": optional.get("/api/chassis/stats")
        if isinstance(optional.get("/api/chassis/stats"), dict)
        else {},
        "power_status": power if isinstance(power, dict) else {},
        "vfcs": optional.get("/api/vfcs") if isinstance(optional.get("/api/vfcs"), list) else [],
        "session": optional.get("/api/session") if isinstance(optional.get("/api/session"), dict) else {},
        "smc_optional_errors": optional_errors,
        "alerting_enabled": True,
        "layout": {"x": 860, "y": 280, "locked": False, "source": "auto"},
        "interfaces": interfaces,
    }
    return {
        "source": {
            **now_data,
            "status": "up",
            "hostname": hostname,
            "serial": serial,
            "model": model,
            "last_error": "",
            "failure_count": 0,
        },
        "machine": machine,
        "adapters": adapters,
        "device": device,
        "counts": {
            "interfaces": len(interfaces),
            "hardware_stats": len(device["chassis_stats"]),
            "vfcs": len(device["vfcs"]),
            "optional_errors": len(optional_errors),
        },
    }


def _network_fetcher(config: SmcSourceConfig) -> JsonFetcher:
    async def fetch(path: str) -> Any:
        return await asyncio.to_thread(_fetch_json, f"{config.base_url}{path}", config.timeout_seconds)

    return fetch


def _fetch_json(url: str, timeout_seconds: float) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "NetWatch-Light/0.1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read()
    except HTTPError as exc:
        raise SmcDiscoveryError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SmcDiscoveryError(str(reason)) from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmcDiscoveryError("response was not valid JSON") from exc


def _network_interfaces(device_id: str, adapters: list[Any]) -> list[dict[str, Any]]:
    interfaces: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(adapters, start=1):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or f"Network adapter {index}").strip()
        ip = str(raw.get("ip") or "").strip()
        if name.lower().startswith("loopback") or ip.startswith("127."):
            continue
        mac_key = _mac_key(raw.get("mac"))
        suffix = mac_key or f"adapter-{index}"
        interface_id = f"{device_id}-nic-{suffix}"
        if interface_id in used_ids:
            interface_id = f"{interface_id}-{index}"
        used_ids.add(interface_id)
        formatted_mac = _format_mac(mac_key)
        details = [value for value in (ip, formatted_mac) if value]
        interfaces.append(
            {
                "id": interface_id,
                "name": name,
                "if_index": str(index),
                "if_alias": " / ".join(details),
                "if_descr": name,
                "if_phys_address": formatted_mac,
                "ip": ip,
                "netmask": str(raw.get("netmask") or "").strip(),
                "admin_status": "unknown",
                "oper_status": "unknown",
                "in_bps": None,
                "out_bps": None,
                "in_errors": 0,
                "out_errors": 0,
                "in_discards": 0,
                "out_discards": 0,
                "alerting_enabled": False,
                "inventory_source": "smc",
                "internal_management": name.strip().lower() == "disguisemgmt",
            }
        )
    return interfaces


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "unknown"


def _mac_key(value: Any) -> str:
    key = re.sub(r"[^a-f0-9]", "", str(value or "").lower())
    return key if len(key) == 12 else ""


def _format_mac(key: str) -> str:
    return ":".join(key[index : index + 2] for index in range(0, 12, 2)) if key else ""
