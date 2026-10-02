from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from uuid import uuid4

from .metrics import (
    build_interface_metric_samples,
    load_metric_catalog,
    metric_catalog_names,
    metric_catalog_summary,
)
from .security import security_settings
from .storage import DatabaseStore

SNAPSHOT_VERSION = 2
EVENT_CAP = 20
POLL_RUN_CAP = 100
INTERFACE_HISTORY_SECONDS = 60 * 60
INTERFACE_HISTORY_CAP = 720
OBSERVED_MAC_LINK_MISSING_POLL_CAP = 3
TOPOLOGY_CONFIDENCE_MAX = 100
TOPOLOGY_CONFIDENCE_LABELS = (
    (90, "authoritative"),
    (75, "high"),
    (55, "medium"),
    (30, "low"),
    (0, "hint"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class NetWatchState:
    """State container for the light app, persisted through the configured store."""

    def __init__(self, persistence_path: Path | None = None, database_url: str | None = None) -> None:
        self.persistence_path = persistence_path
        self.store = DatabaseStore(database_url) if database_url else None
        self.storage_backend = "database" if self.store else "json"
        self.mode = "mock"
        self.live_failures = 0
        self.live_counters: dict[str, dict[str, float | int]] = {}
        self.interface_history: dict[str, list[dict[str, Any]]] = {}
        self.metric_catalog_details = load_metric_catalog()
        self.metric_catalog = metric_catalog_names(self.metric_catalog_details)
        self.poll_runs: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = [
            {
                "id": "core-01",
                "name": "fs-core-01",
                "ip": "10.10.0.2",
                "vendor": "FS",
                "model": "S5860-20SQ",
                "status": "up",
                "fingerprint": "uuid+sysObjectID+chassis-8c:1f:64:10:00:01",
                "alerting_enabled": True,
                "layout": {"x": 520, "y": 70, "locked": True, "source": "manual"},
                "interfaces": [
                    {
                        "id": "core-01-eth1-1",
                        "name": "eth1/1",
                        "if_alias": "uplink to agg-01",
                        "admin_status": "up",
                        "oper_status": "up",
                        "in_bps": 812_000_000,
                        "out_bps": 690_000_000,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 1,
                        "out_discards": 1,
                        "alerting_enabled": True,
                    },
                    {
                        "id": "core-01-eth1-2",
                        "name": "eth1/2",
                        "if_alias": "uplink to edge-02",
                        "admin_status": "up",
                        "oper_status": "up",
                        "in_bps": 226_000_000,
                        "out_bps": 241_000_000,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": True,
                    },
                    {
                        "id": "core-01-eth1-20",
                        "name": "eth1/20",
                        "if_alias": "reserved",
                        "admin_status": "down",
                        "oper_status": "down",
                        "in_bps": 0,
                        "out_bps": 0,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": False,
                    },
                ],
            },
            {
                "id": "agg-01",
                "name": "fs-agg-01",
                "ip": "10.10.0.11",
                "vendor": "FS",
                "model": "S5850-48T4Q",
                "status": "up",
                "fingerprint": "uuid+sysObjectID+chassis-8c:1f:64:10:00:11",
                "alerting_enabled": True,
                "layout": {"x": 80, "y": 245, "locked": True, "source": "manual"},
                "interfaces": [
                    {
                        "id": "agg-01-eth1-49",
                        "name": "eth1/49",
                        "if_alias": "core uplink",
                        "admin_status": "up",
                        "oper_status": "up",
                        "in_bps": 612_000_000,
                        "out_bps": 590_000_000,
                        "in_errors": 0,
                        "out_errors": 1,
                        "in_discards": 0,
                        "out_discards": 1,
                        "alerting_enabled": True,
                    },
                    {
                        "id": "agg-01-eth1-4",
                        "name": "eth1/4",
                        "if_alias": "edge-01",
                        "admin_status": "up",
                        "oper_status": "up",
                        "in_bps": 92_000_000,
                        "out_bps": 38_000_000,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": True,
                    },
                ],
            },
            {
                "id": "edge-01",
                "name": "fs-edge-01",
                "ip": "10.10.1.21",
                "vendor": "FS",
                "model": "S3410-24TS-P",
                "status": "warning",
                "fingerprint": "uuid+sysObjectID+chassis-8c:1f:64:10:01:21",
                "alerting_enabled": True,
                "layout": {"x": 390, "y": 395, "locked": False, "source": "manual"},
                "interfaces": [
                    {
                        "id": "edge-01-eth1-1",
                        "name": "eth1/1",
                        "if_alias": "agg uplink",
                        "admin_status": "up",
                        "oper_status": "up",
                        "in_bps": 120_000_000,
                        "out_bps": 145_000_000,
                        "in_errors": 1,
                        "out_errors": 0,
                        "in_discards": 2,
                        "out_discards": 4,
                        "alerting_enabled": True,
                    },
                    {
                        "id": "edge-01-eth1-24",
                        "name": "eth1/24",
                        "if_alias": "access floor 2",
                        "admin_status": "up",
                        "oper_status": "down",
                        "in_bps": 0,
                        "out_bps": 0,
                        "in_errors": 18,
                        "out_errors": 0,
                        "in_discards": 42,
                        "out_discards": 9,
                        "alerting_enabled": True,
                    },
                ],
            },
            {
                "id": "edge-02",
                "name": "fs-edge-02",
                "ip": "10.10.2.22",
                "vendor": "FS",
                "model": "S3410-24TS-P",
                "status": "up",
                "fingerprint": "uuid+sysObjectID+chassis-8c:1f:64:10:02:22",
                "alerting_enabled": True,
                "layout": {"x": 820, "y": 245, "locked": False, "source": "manual"},
                "interfaces": [
                    {
                        "id": "edge-02-eth1-1",
                        "name": "eth1/1",
                        "if_alias": "core uplink",
                        "admin_status": "up",
                        "oper_status": "up",
                        "in_bps": 140_000_000,
                        "out_bps": 109_000_000,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 1,
                        "out_discards": 0,
                        "alerting_enabled": True,
                    },
                    {
                        "id": "edge-02-eth1-13",
                        "name": "eth1/13",
                        "if_alias": "access lab",
                        "admin_status": "up",
                        "oper_status": "up",
                        "in_bps": 18_000_000,
                        "out_bps": 11_000_000,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": True,
                    },
                ],
            },
            {
                "id": "pending-01",
                "name": "fs-lab-pending",
                "ip": "10.10.9.31",
                "vendor": "FS",
                "model": "LLDP candidate",
                "status": "pending",
                "fingerprint": "pending-lldp-one-sided",
                "alerting_enabled": False,
                "layout": {"x": 820, "y": 425, "locked": False, "source": "auto"},
                "interfaces": [
                    {
                        "id": "pending-01-eth1-1",
                        "name": "eth1/1",
                        "if_alias": "candidate uplink",
                        "admin_status": "unknown",
                        "oper_status": "unknown",
                        "in_bps": None,
                        "out_bps": None,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": False,
                    }
                ],
            },
        ]
        self.links: list[dict[str, Any]] = [
            {
                "id": "link-core-agg",
                "from": "core-01",
                "to": "agg-01",
                "status": "confirmed",
                "evidence": "LLDP both sides",
            },
            {
                "id": "link-core-edge-02",
                "from": "core-01",
                "to": "edge-02",
                "status": "confirmed",
                "evidence": "LLDP both sides",
            },
            {
                "id": "link-agg-edge-01",
                "from": "agg-01",
                "to": "edge-01",
                "status": "confirmed",
                "evidence": "LLDP both sides",
            },
            {
                "id": "link-edge-02-pending",
                "from": "edge-02",
                "to": "pending-01",
                "status": "pending",
                "evidence": "LLDP one side",
            },
        ]
        self.alerts: list[dict[str, Any]] = [
            {
                "id": "alert-01",
                "device_id": "edge-01",
                "interface_id": "edge-01-eth1-24",
                "title": "eth1/24 oper down",
                "detail": "admin up + oper down for 3 polling cycles",
                "severity": "critical",
                "state": "active",
                "created_at": now_iso(),
            },
            {
                "id": "alert-02",
                "device_id": "edge-01",
                "interface_id": "edge-01-eth1-24",
                "title": "eth1/24 discard rate",
                "detail": "discard rate above global threshold",
                "severity": "warning",
                "state": "active",
                "created_at": now_iso(),
            },
            {
                "id": "alert-03",
                "device_id": "agg-01",
                "interface_id": "agg-01-eth1-49",
                "title": "eth1/49 transient errors",
                "detail": "acknowledged during maintenance window",
                "severity": "warning",
                "state": "acknowledged",
                "created_at": now_iso(),
            },
        ]
        self.events: list[dict[str, Any]] = [
            {"id": str(uuid4()), "time": now_clock(), "text": "Light API started with FS-like SNMP fixtures"},
            {"id": str(uuid4()), "time": now_clock(), "text": "LLDP topology loaded: 3 confirmed links, 1 pending link"},
            {"id": str(uuid4()), "time": now_clock(), "text": "Static threshold alert active on edge-01 eth1/24"},
        ]
        self.settings = {
            "polling": {
                "status_seconds": 30,
                "traffic_seconds": 60,
                "inventory_minutes": 15,
                "global_concurrency": 50,
                "per_device_concurrency": 2,
                "getbulk": "adaptive, starts at 25",
                "backend_auto_poll": False,
                "backend_interval_seconds": 30,
                "backend_status": "stopped",
                "external_poller_status": "unknown",
            },
            "thresholds": {
                "interface_error_counter": 1,
                "interface_discard_counter": 1,
            },
            "metrics": metric_catalog_summary(self.metric_catalog_details),
            "security": security_settings(self.store.credential_cipher if self.store else None),
        }
        self.seeds: list[dict[str, Any]] = []
        self.seed_credentials: list[dict[str, Any]] = []
        self.smc_sources: list[dict[str, Any]] = []
        self.mac_labels: dict[str, dict[str, Any]] = {}
        self.device_labels: dict[str, dict[str, Any]] = {}
        self._load()

    def _payload(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "mode": self.mode,
            "live_failures": self.live_failures,
            "devices": self.devices,
            "links": self.links,
            "alerts": self.alerts,
            "events": self.events[:EVENT_CAP],
            "metric_catalog": self.metric_catalog,
            "metric_catalog_details": self.metric_catalog_details,
            "poll_runs": self.poll_runs[:POLL_RUN_CAP],
            "settings": self.settings,
            "seeds": self.seeds,
            "seed_credentials": self.seed_credentials,
            "smc_sources": self.smc_sources,
            "interface_history": self.interface_history,
            "mac_labels": self.mac_labels,
            "device_labels": self.device_labels,
        }

    def _load(self) -> None:
        data: dict[str, Any] | None = None
        loaded_from = ""
        if self.store:
            data = self.store.load_payload()
            if data is not None:
                loaded_from = "database"
        if data is None:
            if self.persistence_path is None or not self.persistence_path.exists():
                return
            try:
                loaded = json.loads(self.persistence_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(loaded, dict):
                return
            data = loaded
            loaded_from = "json"
        if not isinstance(data, dict):
            return
        self.mode = data.get("mode", self.mode)
        self.live_failures = int(data.get("live_failures", self.live_failures) or 0)
        self.devices = data.get("devices", self.devices)
        self.links = data.get("links", self.links)
        self.alerts = data.get("alerts", self.alerts)
        self.events = data.get("events", self.events)[:EVENT_CAP]
        self.metric_catalog_details = load_metric_catalog()
        self.metric_catalog = metric_catalog_names(self.metric_catalog_details)
        self.poll_runs = [
            run
            for run in data.get("poll_runs", self.poll_runs)
            if isinstance(run, dict) and run.get("id")
        ][:POLL_RUN_CAP]
        loaded_settings = data.get("settings", {})
        if isinstance(loaded_settings, dict):
            self.settings.update(loaded_settings)
            self.settings.setdefault("polling", {})
            self.settings["polling"].setdefault("backend_auto_poll", False)
            self.settings["polling"].setdefault("backend_interval_seconds", 30)
            self.settings["polling"].setdefault("backend_status", "stopped")
            self.settings["polling"].setdefault("external_poller_status", "unknown")
            self.settings.setdefault("thresholds", {})
            self.settings["thresholds"].setdefault("interface_error_counter", 1)
            self.settings["thresholds"].setdefault("interface_discard_counter", 1)
            self.settings["metrics"] = metric_catalog_summary(self.metric_catalog_details)
            self.settings["security"] = security_settings(self.store.credential_cipher if self.store else None)
        self.seeds = data.get("seeds", self.seeds)
        self.seed_credentials = data.get("seed_credentials", self.seed_credentials)
        self.smc_sources = [
            source
            for source in data.get("smc_sources", self.smc_sources)
            if isinstance(source, dict) and source.get("key")
        ]
        loaded_history = data.get("interface_history", {})
        if isinstance(loaded_history, dict):
            self.interface_history = loaded_history
        loaded_mac_labels = data.get("mac_labels", {})
        if isinstance(loaded_mac_labels, dict):
            self.mac_labels = {
                key: label
                for raw_key, label in loaded_mac_labels.items()
                if (key := self._mac_label_key(raw_key)) and isinstance(label, dict)
            }
            self._apply_mac_labels()
        loaded_device_labels = data.get("device_labels", {})
        if isinstance(loaded_device_labels, dict):
            self.device_labels = {
                key: label
                for raw_key, label in loaded_device_labels.items()
                if (key := self._device_label_key(raw_key)) and isinstance(label, dict)
            }
            self._apply_device_labels()
        changed = self._deduplicate_observed_topology()
        changed = self._annotate_topology_links() or changed
        if changed:
            self._propagate_endpoint_port_traffic()
            self._sync_alerts_from_devices()
            self.persist()
        elif self.store and loaded_from == "json":
            self.persist()

    def reload(self) -> None:
        self._load()

    def persist(self) -> None:
        if self.store:
            self.store.save_payload(self._payload())
            return
        if self.persistence_path is None:
            return
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.persistence_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self._payload(), indent=2), encoding="utf-8")
        tmp_path.replace(self.persistence_path)

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "devices": deepcopy(self.devices),
            "links": deepcopy(self.links),
            "alerts": deepcopy(self.alerts),
            "events": deepcopy(self.events),
            "metric_catalog": deepcopy(self.metric_catalog),
            "metric_catalog_details": deepcopy(self.metric_catalog_details),
            "poll_runs": deepcopy(self.poll_runs),
            "settings": deepcopy(self.settings),
            "seeds": deepcopy(self.seeds),
            "smc_sources": deepcopy(self.smc_sources),
            "mac_labels": deepcopy(self.mac_labels),
            "device_labels": deepcopy(self.device_labels),
            "operational_summary": self.operational_summary(),
            "storage": {"backend": self.storage_backend},
        }

    def operational_summary(self) -> dict[str, Any]:
        polling = self.settings.setdefault("polling", {})
        poll_interval = self._poll_interval_seconds()
        last_run = deepcopy(self.poll_runs[0]) if self.poll_runs else None
        last_seen_ts = _nullable_float(polling.get("external_poller_last_seen_ts"))
        poller_age = round(time.time() - last_seen_ts) if last_seen_ts else None
        poller_alive = self.is_external_poller_alive(max_age_seconds=max(45, poll_interval * 2))
        last_status = str(last_run.get("status") if last_run else polling.get("last_poll_run_status") or "unknown")
        poll_health_status = "ok" if poller_alive and last_status in {"succeeded", "running", "unknown"} else "warning"
        if not poller_alive or last_status == "failed":
            poll_health_status = "down"
        elif last_status == "partial":
            poll_health_status = "warning"

        latest_data = self._latest_data_summary(poll_interval)
        topology = self._topology_evidence_summary()
        problems = self._operational_problems(poller_alive, last_run, latest_data, topology)
        return {
            "poll_health": {
                "status": poll_health_status,
                "poller_alive": poller_alive,
                "poller_status": polling.get("external_poller_status") or "unknown",
                "poller_age_seconds": poller_age,
                "auto_poll": bool(polling.get("backend_auto_poll")),
                "interval_seconds": poll_interval,
                "last_run": last_run,
            },
            "topology_evidence": topology,
            "latest_data": latest_data,
            "problems": problems,
        }

    def _poll_interval_seconds(self) -> int:
        polling = self.settings.setdefault("polling", {})
        try:
            return max(5, min(3600, int(polling.get("backend_interval_seconds") or 30)))
        except (TypeError, ValueError):
            return 30

    def _latest_data_summary(self, poll_interval: int) -> dict[str, Any]:
        now = time.time()
        latest_ts = 0.0
        sample_count = 0
        interfaces_total = 0
        interfaces_with_samples = 0
        interfaces_with_traffic = 0
        stale_threshold = max(120, poll_interval * 3)
        stale_interfaces = 0
        for device in self.devices:
            for interface in device.get("interfaces", []):
                interfaces_total += 1
                key = self._interface_history_key(str(device.get("id") or ""), str(interface.get("id") or ""))
                samples = self.interface_history.get(key) or []
                if not samples:
                    continue
                interfaces_with_samples += 1
                sample_count += len(samples)
                latest = max((_nullable_float(sample.get("ts")) or 0 for sample in samples), default=0)
                latest_ts = max(latest_ts, latest)
                if now - latest > stale_threshold:
                    stale_interfaces += 1
                if interface.get("in_bps") is not None or interface.get("out_bps") is not None:
                    interfaces_with_traffic += 1
        newest_age = round(now - latest_ts) if latest_ts else None
        if latest_ts <= 0:
            status = "unknown"
        elif newest_age is not None and newest_age <= stale_threshold:
            status = "fresh"
        else:
            status = "stale"
        return {
            "status": status,
            "sample_count": sample_count,
            "latest_sample_ts": latest_ts or None,
            "latest_sample_age_seconds": newest_age,
            "stale_threshold_seconds": stale_threshold,
            "interfaces_total": interfaces_total,
            "interfaces_with_samples": interfaces_with_samples,
            "interfaces_with_traffic": interfaces_with_traffic,
            "stale_interfaces": stale_interfaces,
        }

    def _topology_evidence_summary(self) -> dict[str, Any]:
        confidence = Counter(str(link.get("confidence_label") or "unknown") for link in self.links)
        directness = Counter(str(link.get("directness") or "unknown") for link in self.links)
        sources: Counter[str] = Counter()
        low_confidence_links = []
        for link in self.links:
            for source in link.get("evidence_sources", []) or []:
                if isinstance(source, dict):
                    sources[str(source.get("source") or "unknown")] += 1
            if int(link.get("confidence") or 0) < 55:
                low_confidence_links.append(link)
        authoritative = confidence.get("authoritative", 0) + confidence.get("high", 0)
        total = len(self.links)
        score = round((authoritative / total) * 100) if total else 0
        return {
            "status": "strong" if score >= 70 else "mixed" if total else "empty",
            "score": score,
            "total_links": total,
            "confirmed_links": sum(1 for link in self.links if link.get("status") == "confirmed"),
            "pending_links": sum(1 for link in self.links if link.get("status") == "pending"),
            "low_confidence_links": len(low_confidence_links),
            "confidence": dict(confidence),
            "directness": dict(directness),
            "sources": dict(sources),
        }

    def _operational_problems(
        self,
        poller_alive: bool,
        last_run: dict[str, Any] | None,
        latest_data: dict[str, Any],
        topology: dict[str, Any],
    ) -> list[dict[str, Any]]:
        problems: list[dict[str, Any]] = []
        active_alerts = [alert for alert in self.alerts if alert.get("state") == "active"]
        if active_alerts:
            problems.append(
                {
                    "severity": "critical",
                    "title": f"{len(active_alerts)} active alert(s)",
                    "detail": str(active_alerts[0].get("title") or "Alert queue needs attention"),
                }
            )
        failed_seeds = [seed for seed in self.seeds if seed.get("status") not in {"up", "unknown"} or seed.get("last_error")]
        if failed_seeds:
            problems.append(
                {
                    "severity": "warning",
                    "title": f"{len(failed_seeds)} seed issue(s)",
                    "detail": str(failed_seeds[0].get("last_error") or failed_seeds[0].get("host") or "Seed status changed"),
                }
            )
        if not poller_alive and self.mode == "live":
            problems.append(
                {
                    "severity": "critical",
                    "title": "Poller offline",
                    "detail": "The external poller heartbeat is stale or missing.",
                }
            )
        if last_run and last_run.get("status") in {"failed", "partial"}:
            problems.append(
                {
                    "severity": "critical" if last_run.get("status") == "failed" else "warning",
                    "title": f"Last poll {last_run.get('status')}",
                    "detail": f"{last_run.get('successes', 0)} ok / {last_run.get('failures', 0)} failed",
                }
            )
        if latest_data.get("status") == "stale":
            problems.append(
                {
                    "severity": "warning",
                    "title": "Stale interface data",
                    "detail": f"{latest_data.get('stale_interfaces', 0)} interface(s) exceed freshness threshold.",
                }
            )
        if topology.get("low_confidence_links"):
            problems.append(
                {
                    "severity": "info",
                    "title": f"{topology['low_confidence_links']} low-confidence link(s)",
                    "detail": "Some topology edges are inferred from weak evidence.",
                }
            )
        return problems[:8]

    def get_device_history(self, device_id: str) -> dict[str, Any] | None:
        device = self._device(device_id)
        if device is None:
            return None
        cutoff = time.time() - INTERFACE_HISTORY_SECONDS
        interfaces = []
        for interface in device.get("interfaces", []):
            history_key = self._interface_history_key(device["id"], interface["id"])
            samples = [
                sample
                for sample in self.interface_history.get(history_key, [])
                if float(sample.get("ts", 0) or 0) >= cutoff
            ]
            if not samples:
                samples = [
                    {
                        "ts": time.time(),
                        "in_bps": interface.get("in_bps"),
                        "out_bps": interface.get("out_bps"),
                    }
                ]
            interfaces.append(
                {
                    "id": interface.get("id"),
                    "name": interface.get("name") or interface.get("if_descr") or "Interface",
                    "if_alias": interface.get("if_alias") or interface.get("if_descr") or "",
                    "if_high_speed": interface.get("if_high_speed"),
                    "admin_status": interface.get("admin_status"),
                    "oper_status": interface.get("oper_status"),
                    "samples": deepcopy(samples),
                }
            )
        return {
            "device": {
                "id": device["id"],
                "name": device.get("name") or device["id"],
                "ip": device.get("ip") or "unknown",
                "vendor": device.get("vendor") or "unknown",
                "model": device.get("model") or "unknown",
            },
            "window_seconds": INTERFACE_HISTORY_SECONDS,
            "interfaces": interfaces,
        }

    def add_event(self, text: str) -> dict[str, Any]:
        event = {"id": str(uuid4()), "time": now_clock(), "text": text}
        self.events.insert(0, event)
        self.events = self.events[:EVENT_CAP]
        self.persist()
        return event

    def run_poll(self) -> dict[str, Any]:
        if self.mode == "live":
            event = self.add_event("Live poll skipped: no saved SNMP seed credentials configured")
            return {"event": event, "snapshot": self.snapshot()}
        edge = self._device("edge-01")
        if edge:
            for interface in edge["interfaces"]:
                if interface["id"] == "edge-01-eth1-24":
                    interface["in_discards"] += 3
                    interface["in_errors"] += 1
        event = self.add_event("Manual SNMP poll completed: status, traffic and discard rates refreshed")
        return {"event": event, "snapshot": self.snapshot()}

    def run_discovery(self) -> dict[str, Any]:
        if self.mode == "live":
            event = self.add_event("Live LLDP discovery requested: run Add seed to refresh neighbor candidates")
            return {"event": event, "snapshot": self.snapshot()}
        pending = next((link for link in self.links if link["status"] == "pending"), None)
        if pending:
            text = "LLDP discovery kept fs-lab-pending as pending: one-sided evidence only"
        else:
            self.links.append(
                {
                    "id": "link-edge-02-pending",
                    "from": "edge-02",
                    "to": "pending-01",
                    "status": "pending",
                    "evidence": "LLDP one side",
                }
            )
            text = "LLDP discovery added a pending candidate link from edge-02"
        event = self.add_event(text)
        return {"event": event, "snapshot": self.snapshot()}

    def update_alert(self, alert_id: str, action: str) -> dict[str, Any] | None:
        alert = next((item for item in self.alerts if item["id"] == alert_id), None)
        if alert is None:
            return None
        if action == "ack" and alert["state"] == "active":
            alert["state"] = "acknowledged"
            event = self.add_event(f"Alert acknowledged: {alert['title']}")
            return {"alert": deepcopy(alert), "event": event, "snapshot": self.snapshot()}
        if action == "resolve" and alert["state"] != "resolved":
            alert["state"] = "resolved"
            event = self.add_event(f"Alert resolved: {alert['title']}")
            return {"alert": deepcopy(alert), "event": event, "snapshot": self.snapshot()}
        event = self.add_event(f"Alert unchanged: {alert['title']}")
        return {"alert": deepcopy(alert), "event": event, "snapshot": self.snapshot()}

    def clear_live_inventory(self) -> dict[str, Any]:
        self.mode = "live"
        self.live_failures = 0
        self.live_counters = {}
        self.interface_history = {}
        self.devices = []
        self.links = []
        self.alerts = []
        self.seeds = []
        self.seed_credentials = []
        self.smc_sources = []
        self.events = []
        event = self.add_event("Live mode enabled: mock devices cleared, waiting for SNMP seed")
        return {"event": event, "snapshot": self.snapshot()}

    def update_mac_label(self, mac: str, name: str, description: str = "") -> dict[str, Any]:
        key = self._mac_label_key(mac)
        if not key:
            raise ValueError("A valid MAC address is required")
        label_name = str(name or "").strip()
        label_description = str(description or "").strip()
        if not label_name and not label_description:
            raise ValueError("Asset name or description is required")
        self.mac_labels[key] = {
            "mac": self._format_mac_key(key),
            "name": label_name,
            "description": label_description,
            "updated_at": now_iso(),
        }
        updated = self._apply_mac_labels()
        display = label_name or label_description
        event = self.add_event(f"MAC label saved: {self._format_mac_key(key)} -> {display}")
        return {"label": deepcopy(self.mac_labels[key]), "updated": updated, "event": event, "snapshot": self.snapshot()}

    def update_device_label(self, device_id: str, name: str, description: str = "") -> dict[str, Any]:
        key = self._device_label_key(device_id)
        if not key:
            raise ValueError("A valid device id is required")
        device = self._device(key)
        if device is None:
            raise ValueError("Device not found")
        if device.get("device_type") == "switch":
            raise ValueError("SNMP-managed switches keep their SNMP name")
        label_name = str(name or "").strip()
        label_description = str(description or "").strip()
        if not label_name and not label_description:
            raise ValueError("Asset name or description is required")
        self.device_labels[key] = {
            "device_id": key,
            "name": label_name,
            "description": label_description,
            "updated_at": now_iso(),
        }
        updated = self._apply_device_labels()
        self._apply_mac_labels()
        display = label_name or label_description
        event = self.add_event(f"Device label saved: {key} -> {display}")
        return {"label": deepcopy(self.device_labels[key]), "updated": updated, "event": event, "snapshot": self.snapshot()}

    def set_backend_polling(self, enabled: bool, interval_seconds: int) -> dict[str, Any]:
        polling = self.settings.setdefault("polling", {})
        polling["backend_auto_poll"] = enabled
        polling["backend_interval_seconds"] = interval_seconds
        polling["backend_status"] = "enabled" if enabled else "stopped"
        event = self.add_event(
            f"Backend auto poll {'enabled' if enabled else 'disabled'}: {interval_seconds}s interval"
        )
        return {"event": event, "snapshot": self.snapshot()}

    def start_poll_run(self, source: str, seed_count: int = 0) -> str:
        run_id = str(uuid4())
        started_ts = time.time()
        run = {
            "id": run_id,
            "source": str(source or "poll"),
            "status": "running",
            "started_at": now_iso(),
            "started_at_ts": started_ts,
            "finished_at": "",
            "duration_ms": None,
            "seed_count": max(0, int(seed_count or 0)),
            "successes": 0,
            "failures": 0,
            "error": "",
        }
        self.poll_runs.insert(0, run)
        self.poll_runs = self.poll_runs[:POLL_RUN_CAP]
        polling = self.settings.setdefault("polling", {})
        polling["current_poll_run_id"] = run_id
        polling["last_poll_run_id"] = run_id
        polling["backend_status"] = "polling"
        self.persist()
        return run_id

    def finish_poll_run(
        self,
        run_id: str,
        status: str,
        successes: int = 0,
        failures: int = 0,
        error: str = "",
    ) -> None:
        if not run_id:
            return
        run = next((item for item in self.poll_runs if item.get("id") == run_id), None)
        if run is None:
            run = {
                "id": run_id,
                "source": "poll",
                "started_at": "",
                "started_at_ts": time.time(),
                "seed_count": int(successes or 0) + int(failures or 0),
            }
            self.poll_runs.insert(0, run)
        finished_ts = time.time()
        try:
            started_ts = float(run.get("started_at_ts") or finished_ts)
        except (TypeError, ValueError):
            started_ts = finished_ts
        duration_ms = round(max(0.0, finished_ts - started_ts) * 1000)
        run.update(
            {
                "status": str(status or "unknown"),
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
                "successes": max(0, int(successes or 0)),
                "failures": max(0, int(failures or 0)),
                "error": str(error or ""),
            }
        )
        self.poll_runs = self.poll_runs[:POLL_RUN_CAP]
        polling = self.settings.setdefault("polling", {})
        polling["current_poll_run_id"] = ""
        polling["last_poll_run_id"] = run_id
        polling["last_poll_run_status"] = run["status"]
        polling["last_poll_run_finished_at"] = run["finished_at"]
        polling["last_poll_duration_ms"] = duration_ms
        self.persist()

    def request_manual_poll(self, source: str = "manual poll") -> dict[str, Any]:
        polling = self.settings.setdefault("polling", {})
        request_id = str(uuid4())
        polling["manual_poll_request_id"] = request_id
        polling["manual_poll_requested_at"] = now_iso()
        polling["manual_poll_source"] = source
        polling["backend_status"] = "queued"
        event = self.add_event(f"Poll request queued for external poller: {source}")
        return {"event": event, "snapshot": self.snapshot(), "queued": True, "request_id": request_id}

    def pending_manual_poll_request(self) -> dict[str, str] | None:
        polling = self.settings.setdefault("polling", {})
        request_id = str(polling.get("manual_poll_request_id") or "")
        if not request_id:
            return None
        if request_id == str(polling.get("manual_poll_completed_id") or ""):
            return None
        return {
            "id": request_id,
            "source": str(polling.get("manual_poll_source") or "manual poll"),
            "requested_at": str(polling.get("manual_poll_requested_at") or ""),
        }

    def complete_manual_poll_request(self, request_id: str, result: dict[str, Any] | None = None) -> None:
        polling = self.settings.setdefault("polling", {})
        if request_id:
            polling["manual_poll_completed_id"] = request_id
            polling["manual_poll_completed_at"] = now_iso()
        if result:
            polling["last_manual_poll_successes"] = result.get("successes")
            polling["last_manual_poll_failures"] = result.get("failures")
        polling["backend_status"] = "running" if polling.get("backend_auto_poll") else "idle"
        self.persist()

    def update_external_poller_status(self, status: str, pid: int | None = None) -> None:
        polling = self.settings.setdefault("polling", {})
        polling["external_poller_status"] = status
        polling["external_poller_last_seen"] = now_iso()
        polling["external_poller_last_seen_ts"] = time.time()
        polling["external_poller_pid"] = pid if pid is not None else os.getpid()
        if status == "polling":
            polling["backend_status"] = "polling"
        elif status == "running":
            polling["backend_status"] = "running" if polling.get("backend_auto_poll") else "idle"
        elif status == "stopped":
            polling["backend_status"] = "stopped"
        self.persist()

    def is_external_poller_alive(self, max_age_seconds: int = 45) -> bool:
        polling = self.settings.setdefault("polling", {})
        last_seen = polling.get("external_poller_last_seen_ts")
        try:
            age = time.time() - float(last_seen)
        except (TypeError, ValueError):
            return False
        return age <= max_age_seconds and str(polling.get("external_poller_status") or "") in {"running", "polling"}

    def register_live_seed(self, seed_metadata: dict[str, Any]) -> None:
        key = seed_metadata["key"]
        existing = next((seed for seed in self.seeds if seed.get("key") == key), None)
        if existing:
            existing.update(seed_metadata)
        else:
            self.seeds.append(seed_metadata)
        self.persist()

    def register_seed_credentials(self, credential: dict[str, Any]) -> None:
        key = credential["key"]
        existing = next((item for item in self.seed_credentials if item.get("key") == key), None)
        if existing:
            existing.update(credential)
        else:
            self.seed_credentials.append(credential)
        self.persist()

    def register_smc_source(self, source: dict[str, Any]) -> None:
        key = str(source.get("key") or "")
        if not key:
            raise ValueError("SMC source key is required")
        existing = next((item for item in self.smc_sources if item.get("key") == key), None)
        if existing:
            existing.update(source)
        else:
            self.smc_sources.append(source)
        self.persist()

    def import_smc_discovery(self, discovery: dict[str, Any], source_key: str) -> dict[str, Any]:
        self.mode = "live"
        device = deepcopy(discovery["device"])
        device["smc_source_key"] = source_key
        device["last_seen"] = now_iso()
        self._apply_device_label_to_device(device)
        self._apply_mac_label_to_device(device)
        devices_by_id = {item["id"]: deepcopy(item) for item in self.devices}
        self._merge_device(devices_by_id, device)
        self.devices = list(devices_by_id.values())
        self._deduplicate_observed_topology()
        self._propagate_endpoint_port_traffic()
        self._record_interface_history(self.devices)
        self._sync_alerts_from_devices()
        self._annotate_topology_links()
        counts = discovery.get("counts", {})
        event = self.add_event(
            "SMC host imported: "
            f"{device.get('name') or device['id']}, {counts.get('interfaces', 0)} interfaces, "
            f"{counts.get('hardware_stats', 0)} hardware stats, {counts.get('vfcs', 0)} VFCs"
        )
        return {"event": event, "snapshot": self.snapshot()}

    def mark_smc_poll_failed(self, source_key: str, reason: str) -> dict[str, Any]:
        source = next((item for item in self.smc_sources if item.get("key") == source_key), None)
        failures = int(source.get("failure_count") or 0) + 1 if source else 1
        if source:
            source["failure_count"] = failures
            source["status"] = "down" if failures >= 3 else "unknown"
            source["last_error"] = reason
        for device in self.devices:
            if device.get("smc_source_key") != source_key:
                continue
            device["status"] = "down" if failures >= 3 else "unknown"
            device["smc_last_error"] = reason
        self._sync_alerts_from_devices()
        event = self.add_event(f"SMC poll failed ({failures}/3) for {source_key}: {reason}")
        return {"event": event, "snapshot": self.snapshot()}

    def update_device_layouts(self, layouts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        updated = 0
        for device in self.devices:
            layout = layouts.get(device["id"])
            if not isinstance(layout, dict):
                continue
            try:
                x = float(layout["x"])
                y = float(layout["y"])
            except (KeyError, TypeError, ValueError):
                continue
            device["layout"] = {
                "x": x,
                "y": y,
                "locked": bool(layout.get("locked", False)),
                "source": "saved-map",
            }
            updated += 1
        if updated:
            self.persist()
        event = self.add_event(f"Topology layout saved for {updated} device(s)")
        return {"event": event, "updated": updated, "snapshot": self.snapshot()}

    def import_live_discovery(self, discovery: dict[str, Any], seed_key: str | None = None) -> dict[str, Any]:
        self.mode = "live"
        self.live_failures = 0
        device = deepcopy(discovery["device"])
        if seed_key:
            device["seed_key"] = seed_key
        device["last_seen"] = now_iso()
        self._calculate_live_rates(device)
        self._apply_device_label_to_device(device)
        self._apply_mac_label_to_device(device)
        devices_by_id = {
            item["id"]: deepcopy(item)
            for item in self.devices
            if not self._should_replace_auto_discovery_item(item, seed_key)
        }
        candidate_remap: dict[str, str] = {}
        candidate_interface_remap: dict[str, str] = {}
        pending_match_id = self._find_pending_device_id(device, devices_by_id)
        if pending_match_id and pending_match_id != device["id"]:
            pending = devices_by_id.pop(pending_match_id)
            candidate_remap[pending_match_id] = device["id"]
            if pending.get("layout") and not pending.get("layout", {}).get("source") == "manual":
                device["layout"] = pending["layout"]
        self._merge_device(devices_by_id, device)
        skipped_candidate_ids: set[str] = set()
        for candidate in deepcopy(discovery["candidates"]):
            if seed_key:
                candidate["seed_key"] = seed_key
            candidate["last_seen"] = now_iso()
            self._apply_device_label_to_device(candidate)
            self._apply_mac_label_to_device(candidate)
            persisted_management_link = self._persisted_management_port_link(
                device["id"], local_port=candidate.get("observed_local_port")
            )
            if (
                str(candidate.get("observed_source") or "").upper() == "PORT-DESCRIPTION"
                and persisted_management_link
                and persisted_management_link.get("to") in devices_by_id
            ):
                candidate_remap[candidate["id"]] = str(persisted_management_link["to"])
                continue
            infrastructure_match_ids = (
                set()
                if candidate.get("device_type") == "segment"
                else self._segment_known_infrastructure_ids(candidate, devices_by_id)
            )
            if len(infrastructure_match_ids) == 1:
                match_id = next(iter(infrastructure_match_ids))
                candidate_remap[candidate["id"]] = match_id
                interface_id = self._matched_infrastructure_interface_id(candidate, devices_by_id.get(match_id))
                if interface_id:
                    candidate_interface_remap[candidate["id"]] = interface_id
                continue
            if len(infrastructure_match_ids) > 1:
                skipped_candidate_ids.add(candidate["id"])
                continue
            match_id = self._find_existing_device_id(candidate, devices_by_id)
            if match_id and match_id != candidate["id"] and devices_by_id[match_id].get("status") != "pending":
                candidate_remap[candidate["id"]] = match_id
                interface_id = self._matched_infrastructure_interface_id(candidate, devices_by_id.get(match_id))
                if interface_id:
                    candidate_interface_remap[candidate["id"]] = interface_id
                continue
            self._merge_device(devices_by_id, candidate)
        self.devices = list(devices_by_id.values())
        live_links = [
            link
            for link in discovery["links"]
            if link.get("from") not in skipped_candidate_ids and link.get("to") not in skipped_candidate_ids
        ]
        self._merge_live_links(
            live_links,
            candidate_remap,
            seed_key,
            candidate_interface_remap=candidate_interface_remap,
        )
        self._deduplicate_observed_topology()
        self._prune_unlinked_observed_endpoints(seed_key)
        self._propagate_endpoint_port_traffic()
        self._record_interface_history(self.devices)
        self._sync_alerts_from_devices()
        self._annotate_topology_links()
        counts = discovery["counts"]
        event = self.add_event(
            "Live seed imported: "
            f"{counts['interfaces']} interfaces, {counts['lldp_candidates']} LLDP candidates, "
            f"{counts.get('mac_endpoints', 0)} MAC endpoints, "
            f"{counts.get('described_endpoints', 0)} described endpoints"
        )
        return {"event": event, "snapshot": self.snapshot()}

    def mark_live_poll_failed(self, reason: str, seed_key: str | None = None) -> dict[str, Any]:
        self.live_failures += 1
        status = "down" if self.live_failures >= 3 else "unknown"
        for device in self.devices:
            if device.get("status") != "pending" and (seed_key is None or device.get("seed_key") == seed_key):
                device["status"] = status
                for interface in device.get("interfaces", []):
                    if interface.get("admin_status") == "up":
                        interface["oper_status"] = "unknown"
                        interface["in_bps"] = None
                        interface["out_bps"] = None
        for seed in self.seeds:
            if seed_key is None or seed.get("key") == seed_key:
                seed["status"] = status
                seed["last_error"] = reason
        self._propagate_endpoint_port_traffic()
        self._sync_alerts_from_devices()
        event = self.add_event(f"Live poll failed ({self.live_failures}/3): {reason}")
        return {"event": event, "snapshot": self.snapshot()}

    def _calculate_live_rates(self, device: dict[str, Any]) -> None:
        now = time.monotonic()
        for interface in device.get("interfaces", []):
            key = interface["id"]
            previous = self.live_counters.get(key)
            in_octets = interface.get("in_octets")
            out_octets = interface.get("out_octets")
            if previous and isinstance(in_octets, int) and isinstance(out_octets, int):
                elapsed = max(0.001, now - float(previous["time"]))
                previous_in = int(previous.get("in_octets", 0))
                previous_out = int(previous.get("out_octets", 0))
                interface["in_bps"] = round(((in_octets - previous_in) * 8) / elapsed) if in_octets >= previous_in else None
                interface["out_bps"] = round(((out_octets - previous_out) * 8) / elapsed) if out_octets >= previous_out else None
            self.live_counters[key] = {
                "time": now,
                "in_octets": int(in_octets or 0),
                "out_octets": int(out_octets or 0),
            }

    def _propagate_endpoint_port_traffic(self) -> None:
        devices_by_id = {device["id"]: device for device in self.devices}
        interfaces_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for device in self.devices:
            for interface in device.get("interfaces", []):
                interfaces_by_id[interface["id"]] = (device, interface)

        endpoint_links_by_interface: dict[str, int] = {}
        endpoint_links: list[dict[str, Any]] = []
        for link in self.links:
            endpoint = devices_by_id.get(link.get("to"))
            from_interface = link.get("from_interface")
            if (
                not endpoint
                or endpoint.get("device_type") not in {"endpoint", "media-server"}
                or not from_interface
            ):
                continue
            endpoint_links.append(link)
            endpoint_links_by_interface[from_interface] = endpoint_links_by_interface.get(from_interface, 0) + 1

        for device in self.devices:
            if device.get("device_type") not in {"endpoint", "media-server"}:
                continue
            device.pop("observed_traffic", None)
            for interface in device.get("interfaces", []):
                interface["in_bps"] = None
                interface["out_bps"] = None
                interface["if_high_speed"] = None
                interface.pop("traffic_source", None)
                interface.pop("traffic_note", None)

        for link in endpoint_links:
            endpoint = devices_by_id.get(link["to"])
            source = interfaces_by_id.get(link.get("from_interface"))
            if not endpoint or not source:
                continue
            switch, switch_interface = source
            endpoint_interfaces = endpoint.get("interfaces") or []
            if not endpoint_interfaces:
                continue

            shared_count = endpoint_links_by_interface.get(link["from_interface"], 0)
            traffic_note = "shared switch port" if shared_count > 1 else "single endpoint switch port"
            traffic = {
                "source": "switch-port",
                "switch_device_id": switch["id"],
                "switch_name": switch.get("name") or switch["id"],
                "switch_interface_id": switch_interface["id"],
                "switch_port": switch_interface.get("name") or link.get("local_port") or "unknown",
                "switch_port_alias": switch_interface.get("if_alias") or "",
                "switch_in_bps": switch_interface.get("in_bps"),
                "switch_out_bps": switch_interface.get("out_bps"),
                "endpoint_in_bps": switch_interface.get("out_bps"),
                "endpoint_out_bps": switch_interface.get("in_bps"),
                "if_high_speed": switch_interface.get("if_high_speed"),
                "shared_port": shared_count > 1,
                "shared_endpoint_count": shared_count,
                "note": traffic_note,
            }
            if endpoint.get("device_type") == "endpoint":
                endpoint["observed_traffic"] = traffic
                endpoint_interface = endpoint_interfaces[0]
            else:
                endpoint_interface = next(
                    (
                        interface
                        for interface in endpoint_interfaces
                        if interface.get("id") == link.get("to_interface")
                    ),
                    None,
                )
                if endpoint_interface is None:
                    continue
                endpoint_interface["traffic_note"] = "SMC adapter matched to switch port"
            endpoint_interface["in_bps"] = traffic["endpoint_in_bps"]
            endpoint_interface["out_bps"] = traffic["endpoint_out_bps"]
            endpoint_interface["if_high_speed"] = traffic["if_high_speed"]
            endpoint_interface["admin_status"] = switch_interface.get("admin_status", endpoint_interface.get("admin_status", "unknown"))
            endpoint_interface["oper_status"] = switch_interface.get("oper_status", endpoint_interface.get("oper_status", "observed"))
            endpoint_interface["in_errors"] = switch_interface.get("out_errors") or 0
            endpoint_interface["out_errors"] = switch_interface.get("in_errors") or 0
            endpoint_interface["in_discards"] = switch_interface.get("out_discards") or 0
            endpoint_interface["out_discards"] = switch_interface.get("in_discards") or 0
            endpoint_interface["traffic_source"] = "switch-port"
            endpoint_interface["switch_device_id"] = switch["id"]
            endpoint_interface["switch_interface_id"] = switch_interface["id"]
            endpoint_interface["switch_port"] = switch_interface.get("name") or link.get("local_port") or "unknown"
            endpoint_interface["traffic_note"] = traffic_note

    def _interface_history_key(self, device_id: str, interface_id: str) -> str:
        return f"{device_id}::{interface_id}"

    def _record_interface_history(self, devices: list[dict[str, Any]]) -> None:
        now = time.time()
        cutoff = now - INTERFACE_HISTORY_SECONDS
        active_keys: set[str] = set()
        for device in devices:
            device_id = device.get("id")
            if not device_id:
                continue
            for interface in device.get("interfaces", []):
                interface_id = interface.get("id")
                if not interface_id:
                    continue
                key = self._interface_history_key(device_id, interface_id)
                active_keys.add(key)
                samples = self.interface_history.setdefault(key, [])
                metric_samples = build_interface_metric_samples(
                    self.metric_catalog_details,
                    device,
                    interface,
                    now,
                )
                traffic_missing = interface.get("in_bps") is None and interface.get("out_bps") is None
                samples.append(
                    {
                        "ts": now,
                        "in_bps": interface.get("in_bps"),
                        "out_bps": interface.get("out_bps"),
                        "quality": "missing" if traffic_missing else "ok",
                        "metrics": metric_samples,
                    }
                )
                self.interface_history[key] = [
                    sample
                    for sample in samples
                    if float(sample.get("ts", 0) or 0) >= cutoff
                ][-INTERFACE_HISTORY_CAP:]

        for key in list(self.interface_history):
            samples = [
                sample
                for sample in self.interface_history[key]
                if float(sample.get("ts", 0) or 0) >= cutoff
            ][-INTERFACE_HISTORY_CAP:]
            if samples or key in active_keys:
                self.interface_history[key] = samples
            else:
                self.interface_history.pop(key, None)

    def _merge_device(self, devices_by_id: dict[str, dict[str, Any]], device: dict[str, Any]) -> None:
        existing = devices_by_id.get(device["id"])
        if existing:
            if existing.get("layout"):
                device["layout"] = existing["layout"]
            device["alerting_enabled"] = existing.get("alerting_enabled", device.get("alerting_enabled", True))
            if existing.get("management_mac") and not device.get("management_mac"):
                device["management_mac"] = existing["management_mac"]
                device["management_mac_source"] = existing.get("management_mac_source") or "persisted"
        devices_by_id[device["id"]] = device

    def _is_infrastructure_device(self, device: dict[str, Any]) -> bool:
        if device.get("device_type") in {"endpoint", "segment"}:
            return False
        if device.get("lldp_sys_name") or device.get("lldp_mgmt_ip"):
            return True
        if device.get("seed_key") or str(device.get("id", "")).startswith("live-"):
            return True
        return bool(device.get("interfaces"))

    def _infrastructure_mac_index(self, devices_by_id: dict[str, dict[str, Any]]) -> dict[str, str]:
        index: dict[str, str] = {}
        for device in devices_by_id.values():
            if not self._is_infrastructure_device(device):
                continue
            for value in (
                device.get("mac"),
                device.get("observed_mac"),
                device.get("management_mac"),
                device.get("fingerprint"),
                device.get("chassis_id"),
            ):
                key = self._mac_label_key(value)
                if key:
                    index.setdefault(key, device["id"])
            for interface in device.get("interfaces", []):
                key = self._mac_label_key(interface.get("if_phys_address"))
                if key:
                    index.setdefault(key, device["id"])
        return index

    def _segment_known_infrastructure_ids(
        self, candidate: dict[str, Any], devices_by_id: dict[str, dict[str, Any]]
    ) -> set[str]:
        if candidate.get("device_type") != "segment":
            return set()
        observed_keys = {
            key
            for mac in candidate.get("observed_macs", [])
            if (key := self._mac_label_key(mac))
        }
        if not observed_keys:
            return set()
        mac_index = self._infrastructure_mac_index(devices_by_id)
        matches: set[str] = set()
        for key in sorted(observed_keys):
            match_id = mac_index.get(key)
            if match_id:
                matches.add(match_id)
        return matches

    def _prune_unlinked_observed_endpoints(self, seed_key: str | None) -> None:
        if not seed_key:
            return
        linked_ids = {link.get("from") for link in self.links} | {link.get("to") for link in self.links}
        self.devices = [
            device
            for device in self.devices
            if not (
                device.get("seed_key") == seed_key
                and device.get("device_type") in {"endpoint", "segment"}
                and device.get("status") == "observed"
                and device.get("id") not in linked_ids
            )
        ]

    def _annotate_topology_links(self) -> bool:
        changed = False
        for link in self.links:
            changed = self._annotate_topology_link(link) or changed
        return changed

    def _annotate_topology_link(self, link: dict[str, Any]) -> bool:
        before = {
            "evidence_sources": deepcopy(link.get("evidence_sources")),
            "confidence": link.get("confidence"),
            "confidence_label": link.get("confidence_label"),
            "directness": link.get("directness"),
            "line_style": link.get("line_style"),
            "topology_decision": link.get("topology_decision"),
        }
        sources = self._link_evidence_sources(link)
        confidence = self._link_confidence(link, sources)
        link["evidence_sources"] = sources
        link["confidence"] = confidence
        link["confidence_label"] = self._confidence_label(confidence)
        link["directness"] = self._link_directness(link, sources, confidence)
        link["line_style"] = "solid" if confidence >= 75 else "dashed"
        link.setdefault("topology_decision", self._default_topology_decision(link))
        after = {
            "evidence_sources": link.get("evidence_sources"),
            "confidence": link.get("confidence"),
            "confidence_label": link.get("confidence_label"),
            "directness": link.get("directness"),
            "line_style": link.get("line_style"),
            "topology_decision": link.get("topology_decision"),
        }
        return before != after

    def _link_evidence_sources(self, link: dict[str, Any]) -> list[dict[str, Any]]:
        raw_sources = link.get("evidence_sources")
        if isinstance(raw_sources, list) and raw_sources:
            sources = [
                self._normalize_evidence_source(source, link)
                for source in raw_sources
                if isinstance(source, dict)
            ]
            if sources:
                return sources

        text = str(link.get("evidence") or "").strip()
        lowered = text.lower()
        observed_at = str(link.get("last_seen") or now_iso())
        if "lldp both sides" in lowered:
            return [
                {
                    "source": "lldp",
                    "direction": "both",
                    "weight": 100,
                    "detail": "LLDP observed from both devices",
                    "observed_at": observed_at,
                }
            ]
        if "lldp one side + snmp seed match" in lowered:
            return [
                {
                    "source": "lldp",
                    "direction": "one",
                    "weight": 85,
                    "detail": "LLDP one side matched to imported SNMP seed",
                    "observed_at": observed_at,
                }
            ]
        if "lldp one side + smc adapter match" in lowered:
            return [
                {
                    "source": "lldp",
                    "direction": "one",
                    "weight": 85,
                    "detail": "LLDP observed from the switch",
                    "observed_at": observed_at,
                },
                {
                    "source": "smc",
                    "direction": "identity",
                    "weight": 92,
                    "detail": "LLDP chassis MAC matched an SMC network adapter",
                    "observed_at": observed_at,
                },
            ]
        if lowered.startswith("lldp") or "lldp" in lowered:
            return [
                {
                    "source": "lldp",
                    "direction": "one",
                    "weight": 68,
                    "detail": text or "LLDP one side from seed",
                    "observed_at": observed_at,
                }
            ]
        if "q-bridge" in lowered:
            return [
                {
                    "source": "fdb",
                    "direction": "observed",
                    "weight": 52,
                    "detail": text or "MAC table Q-BRIDGE-MIB",
                    "observed_at": observed_at,
                }
            ]
        if "bridge-mib" in lowered or "mac table" in lowered:
            return [
                {
                    "source": "fdb",
                    "direction": "observed",
                    "weight": 44,
                    "detail": text or "MAC table BRIDGE-MIB",
                    "observed_at": observed_at,
                }
            ]
        if "shared mac segment" in lowered or "shared" in lowered:
            return [
                {
                    "source": "fdb-segment",
                    "direction": "observed",
                    "weight": 35,
                    "detail": text or "Shared MAC segment",
                    "observed_at": observed_at,
                }
            ]
        if "port description" in lowered:
            return [
                {
                    "source": "port-description",
                    "direction": "described",
                    "weight": 25,
                    "detail": text or "Port description",
                    "observed_at": observed_at,
                }
            ]
        return [
            {
                "source": "unknown",
                "direction": "unknown",
                "weight": 15,
                "detail": text or "No structured evidence",
                "observed_at": observed_at,
            }
        ]

    def _normalize_evidence_source(self, source: dict[str, Any], link: dict[str, Any]) -> dict[str, Any]:
        try:
            weight = int(source.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0
        return {
            "source": str(source.get("source") or "unknown"),
            "direction": str(source.get("direction") or ""),
            "weight": max(0, min(TOPOLOGY_CONFIDENCE_MAX, weight)),
            "detail": str(source.get("detail") or link.get("evidence") or ""),
            "observed_at": str(source.get("observed_at") or link.get("last_seen") or now_iso()),
        }

    def _link_confidence(self, link: dict[str, Any], sources: list[dict[str, Any]] | None = None) -> int:
        evidence_sources = sources or self._link_evidence_sources(link)
        score = max((int(source.get("weight") or 0) for source in evidence_sources), default=0)
        if link.get("status") == "confirmed":
            score = max(score, 80)
        if link.get("status") == "pending":
            score = min(score, 68)
        missing_polls = int(link.get("missing_polls") or 0)
        if missing_polls:
            score -= min(45, missing_polls * 15)
        if link.get("stale"):
            score -= 15
        return max(0, min(TOPOLOGY_CONFIDENCE_MAX, score))

    def _confidence_label(self, confidence: int) -> str:
        for minimum, label in TOPOLOGY_CONFIDENCE_LABELS:
            if confidence >= minimum:
                return label
        return "unknown"

    def _link_directness(self, link: dict[str, Any], sources: list[dict[str, Any]], confidence: int) -> str:
        source_names = {str(source.get("source") or "") for source in sources}
        directions = {str(source.get("direction") or "") for source in sources}
        if "arp" in source_names and "management" in directions:
            return "management"
        if "lldp" in source_names and "both" in directions:
            return "direct"
        if "lldp" in source_names and confidence >= 80:
            return "probable-direct"
        if "lldp" in source_names:
            return "candidate"
        if "fdb" in source_names:
            return "observed-fdb"
        if "fdb-segment" in source_names:
            return "shared-segment"
        if "port-description" in source_names:
            return "described-port"
        return "unknown"

    def _default_topology_decision(self, link: dict[str, Any]) -> str:
        confidence = int(link.get("confidence") or 0)
        directness = str(link.get("directness") or "unknown")
        if directness == "direct":
            return "Displayed as direct link: LLDP evidence is authoritative."
        if directness == "probable-direct":
            return "Displayed as probable direct link: one-sided LLDP matched an imported managed device."
        if directness == "candidate":
            return "Displayed as candidate link: one-sided LLDP has no reciprocal confirmation yet."
        if directness == "management":
            return "Displayed as management link: ARP identifies the seed management interface observed in the MAC table."
        if directness == "observed-fdb":
            return f"Displayed as observed link: MAC table evidence with {confidence}% confidence."
        if directness == "shared-segment":
            return "Displayed as shared segment: multiple MAC addresses were learned on one switch port."
        if directness == "described-port":
            return "Displayed as descriptive hint: port description exists but no stronger L2 evidence was found."
        return "Displayed with unknown evidence quality."

    def _deduplicate_observed_topology(self) -> bool:
        changed = self._clear_seen_observed_link_stale_flags()
        changed = self._deduplicate_resolved_pending_devices() or changed
        devices_by_id = {device.get("id"): device for device in self.devices if device.get("id")}
        interface_index = self._interface_index_by_id(devices_by_id)
        infrastructure_interfaces, infrastructure_ports = self._infrastructure_link_ports(devices_by_id, interface_index)
        changed = self._prune_infrastructure_port_observations(
            devices_by_id,
            interface_index,
            infrastructure_interfaces,
            infrastructure_ports,
        ) or changed
        changed = self._keep_best_observed_mac_links(
            devices_by_id,
            interface_index,
            infrastructure_interfaces,
            infrastructure_ports,
        ) or changed
        if changed:
            linked_ids = {link.get("from") for link in self.links} | {link.get("to") for link in self.links}
            self.devices = [
                device
                for device in self.devices
                if device.get("device_type") not in {"endpoint", "segment"} or device.get("id") in linked_ids
            ]
        return changed

    def _clear_seen_observed_link_stale_flags(self) -> bool:
        changed = False
        for link in self.links:
            if link.get("stale") and int(link.get("missing_polls") or 0) <= 0:
                link["stale"] = False
                changed = True
        return changed

    def _deduplicate_resolved_pending_devices(self) -> bool:
        devices_by_id = {device.get("id"): device for device in self.devices if device.get("id")}
        active_infrastructure = [
            device
            for device in devices_by_id.values()
            if device.get("status") != "pending" and self._is_infrastructure_device(device)
        ]
        remap: dict[str, str] = {}
        remap_to_interface: dict[str, str] = {}
        for candidate in devices_by_id.values():
            candidate_id = str(candidate.get("id") or "")
            if not candidate_id:
                continue
            if candidate.get("status") == "pending":
                match_id = self._resolved_pending_match_id(candidate, active_infrastructure)
            elif candidate.get("status") == "observed" and candidate.get("device_type") == "endpoint":
                match_id = self._resolved_observed_endpoint_match_id(candidate, active_infrastructure)
            else:
                continue
            if match_id:
                interface_id = self._matched_infrastructure_interface_id(candidate, devices_by_id.get(match_id))
                if interface_id:
                    remap_to_interface[candidate_id] = interface_id
            if match_id and match_id != candidate_id:
                remap[candidate_id] = match_id

        if not remap:
            return False

        for old_id, new_id in remap.items():
            resolved = devices_by_id.get(old_id)
            active = devices_by_id.get(new_id)
            if resolved and active and resolved.get("layout") and active.get("layout", {}).get("source") != "manual":
                active["layout"] = resolved["layout"]

        self.devices = [device for device in self.devices if str(device.get("id") or "") not in remap]
        merged_links: dict[str, dict[str, Any]] = {}
        for raw_link in self.links:
            link = deepcopy(raw_link)
            old_from = str(link.get("from") or "")
            old_to = str(link.get("to") or "")
            link["from"] = remap.get(old_from, link.get("from"))
            link["to"] = remap.get(old_to, link.get("to"))
            if old_to in remap_to_interface:
                link["to_interface"] = remap_to_interface[old_to]
                if str(remap_to_interface[old_to]).endswith("-management"):
                    resolved = devices_by_id.get(old_to, {})
                    management_mac = self._format_mac_key(
                        self._mac_label_key(
                            resolved.get("management_mac")
                            or resolved.get("mac")
                            or resolved.get("chassis_id")
                            or resolved.get("fingerprint")
                        )
                    )
                    if management_mac:
                        link["management_mac"] = management_mac
                        observed_source = str(resolved.get("observed_source") or "MAC table")
                        if "mac table" not in str(link.get("evidence") or "").lower():
                            link["evidence"] = (
                                f"MAC table {observed_source}; {link.get('evidence') or 'endpoint observation'}"
                            )
            if link.get("from") == link.get("to"):
                continue
            self._classify_management_link(link, devices_by_id)
            link["id"] = self._stable_link_id(link)
            merged_links[link["id"]] = {**merged_links.get(link["id"], {}), **link}
        self.links = list(merged_links.values())
        self._confirm_reciprocal_links()
        return True

    def _resolved_pending_match_id(
        self, pending: dict[str, Any], active_infrastructure: list[dict[str, Any]]
    ) -> str | None:
        pending_ips = {
            str(value).strip()
            for value in (pending.get("ip"), pending.get("lldp_mgmt_ip"), pending.get("observed_ip"))
            if value and str(value).strip() != "unknown"
        }
        pending_names = {
            str(value).strip().lower()
            for value in (pending.get("name"), pending.get("lldp_sys_name"))
            if value and not str(value).strip().lower().startswith("lldp neighbor")
        }
        pending_identifiers = {
            self._normalize_identifier(value)
            for value in (
                pending.get("fingerprint"),
                pending.get("chassis_id"),
                pending.get("mac"),
                pending.get("observed_mac"),
                pending.get("management_mac"),
            )
        }
        pending_identifiers.discard("")

        for device in active_infrastructure:
            if device.get("device_type") == "media-server":
                device_identifiers = self._infrastructure_device_mac_keys(device)
                if pending_identifiers.intersection(device_identifiers):
                    return str(device["id"])
                continue
            device_ips = {
                str(value).strip()
                for value in (device.get("ip"), device.get("lldp_mgmt_ip"), device.get("observed_ip"))
                if value and str(value).strip() != "unknown"
            }
            if pending_ips.intersection(device_ips):
                return str(device["id"])

            device_names = {
                str(value).strip().lower()
                for value in (device.get("name"), device.get("lldp_sys_name"))
                if value
            }
            if pending_names.intersection(device_names):
                return str(device["id"])

            device_identifiers = {
                self._normalize_identifier(value)
                for value in (
                    device.get("fingerprint"),
                    device.get("chassis_id"),
                    device.get("mac"),
                    device.get("observed_mac"),
                    device.get("management_mac"),
                )
            }
            device_identifiers.update(
                self._normalize_identifier(interface.get("if_phys_address"))
                for interface in device.get("interfaces", []) or []
                if interface.get("if_phys_address")
            )
            device_identifiers.discard("")
            if pending_identifiers.intersection(device_identifiers):
                return str(device["id"])

        return None

    def _resolved_observed_endpoint_match_id(
        self, endpoint: dict[str, Any], active_infrastructure: list[dict[str, Any]]
    ) -> str | None:
        endpoint_ips = {
            str(value).strip()
            for value in (endpoint.get("ip"), endpoint.get("observed_ip"))
            if value and str(value).strip() != "unknown"
        }
        endpoint_macs = set(self._device_mac_label_keys(endpoint))
        observed_macs = {self._mac_label_key(value) for value in endpoint.get("observed_macs", []) or []}
        observed_macs.discard("")
        if len(observed_macs) > 1 and not self._mac_label_key(endpoint.get("mac")):
            endpoint_macs.difference_update(observed_macs)

        if not endpoint_ips and not endpoint_macs:
            return None

        matches: set[str] = set()
        for device in active_infrastructure:
            if device.get("id") == endpoint.get("id"):
                continue
            device_macs = self._infrastructure_device_mac_keys(device)
            if endpoint_macs.intersection(device_macs):
                matches.add(str(device["id"]))
                continue
            if device.get("device_type") == "media-server":
                continue
            device_ips = {
                str(value).strip()
                for value in (device.get("ip"), device.get("lldp_mgmt_ip"), device.get("observed_ip"))
                if value and str(value).strip() != "unknown"
            }
            if endpoint_ips.intersection(device_ips):
                matches.add(str(device["id"]))

        if len(matches) == 1:
            return next(iter(matches))
        return None

    def _matched_infrastructure_interface_id(
        self, endpoint: dict[str, Any], device: dict[str, Any] | None
    ) -> str:
        if not device:
            return ""
        endpoint_macs = set(self._device_mac_label_keys(endpoint))
        management_key = self._mac_label_key(device.get("management_mac"))
        if management_key and management_key in endpoint_macs:
            return f"{device.get('id')}-management"
        for interface in device.get("interfaces", []) or []:
            interface_key = self._mac_label_key(interface.get("if_phys_address"))
            if interface_key and interface_key in endpoint_macs:
                return str(interface.get("id") or "")
        return ""

    def _infrastructure_device_mac_keys(self, device: dict[str, Any]) -> set[str]:
        keys = set(self._device_mac_label_keys(device))
        for interface in device.get("interfaces", []) or []:
            key = self._mac_label_key(interface.get("if_phys_address"))
            if key:
                keys.add(key)
        return keys

    def _interface_index_by_id(
        self, devices_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
        interface_index: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for device in devices_by_id.values():
            for interface in device.get("interfaces", []) or []:
                interface_id = interface.get("id")
                if interface_id:
                    interface_index[interface_id] = (device, interface)
        return interface_index

    def _infrastructure_link_ports(
        self,
        devices_by_id: dict[str, dict[str, Any]],
        interface_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[set[str], set[tuple[str, str]]]:
        interface_ids: set[str] = set()
        port_names: set[tuple[str, str]] = set()
        for link in self.links:
            if link.get("status") == "observed":
                continue
            from_device = devices_by_id.get(link.get("from"))
            to_device = devices_by_id.get(link.get("to"))
            if not from_device or not to_device:
                continue
            if not self._is_infrastructure_device(from_device) or not self._is_infrastructure_device(to_device):
                continue
            self._add_infrastructure_link_port(from_device, link.get("from_interface"), link.get("local_port"), interface_ids, port_names, interface_index)
            self._add_infrastructure_link_port(to_device, link.get("to_interface"), link.get("remote_port"), interface_ids, port_names, interface_index)
        return interface_ids, port_names

    def _add_infrastructure_link_port(
        self,
        device: dict[str, Any],
        interface_id: Any,
        port_name: Any,
        interface_ids: set[str],
        port_names: set[tuple[str, str]],
        interface_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    ) -> None:
        device_id = str(device.get("id") or "")
        if not device_id:
            return
        if interface_id and interface_id in interface_index:
            interface_ids.add(str(interface_id))
            _, interface = interface_index[str(interface_id)]
            for value in (interface.get("name"), interface.get("if_alias"), interface.get("if_descr")):
                key = self._port_key(value)
                if key:
                    port_names.add((device_id, key))
        for value in (port_name,):
            key = self._port_key(value)
            if key:
                port_names.add((device_id, key))

    def _prune_infrastructure_port_observations(
        self,
        devices_by_id: dict[str, dict[str, Any]],
        interface_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
        infrastructure_interfaces: set[str],
        infrastructure_ports: set[tuple[str, str]],
    ) -> bool:
        kept_links: list[dict[str, Any]] = []
        changed = False
        for link in self.links:
            target = devices_by_id.get(link.get("to"))
            if (
                link.get("status") == "observed"
                and target
                and self._is_infrastructure_device(target)
                and "shared mac segment" in str(link.get("evidence") or "").lower()
            ):
                changed = True
                continue
            if (
                link.get("status") == "observed"
                and target
                and target.get("device_type") in {"endpoint", "segment"}
                and self._is_infrastructure_observation(link, interface_index, infrastructure_interfaces, infrastructure_ports)
            ):
                changed = True
                continue
            kept_links.append(link)
        if changed:
            self.links = kept_links
        return changed

    def _keep_best_observed_mac_links(
        self,
        devices_by_id: dict[str, dict[str, Any]],
        interface_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
        infrastructure_interfaces: set[str],
        infrastructure_ports: set[tuple[str, str]],
    ) -> bool:
        observed_links_by_mac: dict[str, list[dict[str, Any]]] = {}
        observed_link_count_by_interface: dict[str, int] = {}
        for link in self.links:
            target = devices_by_id.get(link.get("to"))
            if link.get("status") != "observed" or not target:
                continue
            if target.get("device_type") == "endpoint":
                mac_key = self._observed_link_mac_key(target, link)
                group_key = f"endpoint:{mac_key}" if mac_key else ""
            elif self._is_infrastructure_device(target):
                mac_key = self._observed_infrastructure_link_mac_key(target, link)
                group_key = f"infrastructure:{target.get('id')}:{mac_key}" if mac_key else ""
            else:
                group_key = ""
            if not group_key:
                continue
            observed_links_by_mac.setdefault(group_key, []).append(link)
            from_interface = str(link.get("from_interface") or "")
            if from_interface:
                observed_link_count_by_interface[from_interface] = observed_link_count_by_interface.get(from_interface, 0) + 1

        links_to_remove: set[str] = set()
        endpoint_updates: dict[str, dict[str, Any]] = {}
        for links in observed_links_by_mac.values():
            if len(links) <= 1:
                continue
            best = max(
                links,
                key=lambda link: self._observed_link_score(
                    link,
                    interface_index,
                    infrastructure_interfaces,
                    infrastructure_ports,
                    observed_link_count_by_interface,
                ),
            )
            for link in links:
                if link is not best:
                    links_to_remove.add(str(link.get("id") or ""))
            target = devices_by_id.get(best.get("to"))
            if target and target.get("device_type") == "endpoint":
                endpoint_updates[str(best.get("to") or "")] = best
            best["candidate_observation_count"] = len(links)
            best["suppressed_observation_count"] = len(links) - 1
            best["topology_decision"] = self._observed_link_selection_reason(best, len(links))

        if not links_to_remove:
            return False

        self.links = [link for link in self.links if str(link.get("id") or "") not in links_to_remove]
        for endpoint_id, link in endpoint_updates.items():
            endpoint = devices_by_id.get(endpoint_id)
            source = interface_index.get(str(link.get("from_interface") or ""))
            if endpoint and source:
                _, source_interface = source
                self._update_endpoint_observation(endpoint, link, source_interface)
        return True

    def _observed_infrastructure_link_mac_key(self, target: dict[str, Any], link: dict[str, Any]) -> str:
        remote_key = self._mac_label_key(link.get("management_mac") or link.get("remote_port"))
        if not remote_key:
            return ""
        if remote_key not in self._infrastructure_device_mac_keys(target):
            return ""
        return remote_key

    def _observed_link_score(
        self,
        link: dict[str, Any],
        interface_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
        infrastructure_interfaces: set[str],
        infrastructure_ports: set[tuple[str, str]],
        observed_link_count_by_interface: dict[str, int],
    ) -> tuple[int, str]:
        score = self._link_confidence(link)
        from_interface_id = str(link.get("from_interface") or "")
        if self._is_infrastructure_observation(link, interface_index, infrastructure_interfaces, infrastructure_ports):
            score -= 1000
        missing_polls = int(link.get("missing_polls") or 0)
        if missing_polls > 0:
            score -= missing_polls * 60
        if from_interface_id and observed_link_count_by_interface.get(from_interface_id, 0) > 1:
            score -= 140
        source = interface_index.get(from_interface_id)
        if source:
            _, interface = source
            if interface.get("oper_status") == "up":
                score += 30
            if interface.get("if_alias"):
                score += 10
            tagged_vlans = interface.get("vlan_tagged") or []
            untagged_vlans = interface.get("vlan_untagged") or []
            vlan_count = len(tagged_vlans) + len(untagged_vlans)
            if vlan_count == 1:
                score += 24
            if vlan_count > 2:
                score -= 220
            if len(tagged_vlans) > 1:
                score -= 80
            label = " ".join(
                str(value or "").lower()
                for value in (interface.get("name"), interface.get("if_alias"), interface.get("if_descr"), link.get("local_port"))
            )
            if any(token in label for token in ("trunk", "uplink", "lwl", "lag", "port-channel", "etherchannel")):
                score -= 180
            if "qnap" in label or "nas" in label:
                score += 60
        return score, str(link.get("id") or "")

    def _observed_link_selection_reason(self, link: dict[str, Any], candidate_count: int) -> str:
        confidence = self._link_confidence(link)
        port = link.get("local_port") or "unknown port"
        evidence = link.get("evidence") or "observed evidence"
        return (
            f"Selected from {candidate_count} competing observation(s): {evidence} on {port}, "
            f"confidence {confidence}."
        )

    def _is_infrastructure_observation(
        self,
        link: dict[str, Any],
        interface_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
        infrastructure_interfaces: set[str],
        infrastructure_ports: set[tuple[str, str]],
    ) -> bool:
        from_interface_id = str(link.get("from_interface") or "")
        if from_interface_id and from_interface_id in infrastructure_interfaces:
            return True
        source = interface_index.get(from_interface_id)
        device_id = str(link.get("from") or "")
        port_keys = [self._port_key(link.get("local_port"))]
        if source:
            _, interface = source
            port_keys.extend(
                self._port_key(value)
                for value in (interface.get("name"), interface.get("if_alias"), interface.get("if_descr"))
            )
        return any(key and (device_id, key) in infrastructure_ports for key in port_keys)

    def _observed_link_mac_key(self, endpoint: dict[str, Any], link: dict[str, Any]) -> str:
        for value in (
            endpoint.get("mac"),
            endpoint.get("observed_mac"),
            endpoint.get("asset_mac"),
            endpoint.get("fingerprint"),
            endpoint.get("chassis_id"),
            link.get("remote_port"),
        ):
            key = self._mac_label_key(value)
            if key:
                return key
        return ""

    def _update_endpoint_observation(
        self, endpoint: dict[str, Any], link: dict[str, Any], source_interface: dict[str, Any]
    ) -> None:
        port_name = source_interface.get("name") or link.get("local_port") or ""
        port_alias = source_interface.get("if_alias") or ""
        endpoint["observed_local_port"] = port_name
        endpoint["observed_local_port_alias"] = port_alias
        endpoint["observed_source"] = link.get("evidence") or endpoint.get("observed_source") or "MAC table"
        if port_alias and endpoint.get("name_source") not in {"mac-label", "device-label"}:
            endpoint["name"] = port_alias
        for interface in endpoint.get("interfaces", []) or []:
            interface["if_alias"] = f"seen on {port_name}" if port_name else interface.get("if_alias", "")

    def _port_key(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"^(tengigabitethernet|tgigabitethernet|tgigaethernet|tgi|te)", "tg", text)
        text = re.sub(r"^(gigaethernet|gigabitethernet|gi|ge)", "g", text)
        text = re.sub(r"^(fastethernet|fa)", "fa", text)
        text = re.sub(r"\b(tengigabitethernet|tgigabitethernet|tgigaethernet|tgi|te)\b", "tg", text)
        text = re.sub(r"\b(gigaethernet|gigabitethernet|gi|ge)\b", "g", text)
        text = re.sub(r"\b(fastethernet|fa)\b", "fa", text)
        text = re.sub(r"[^a-z0-9]+", "", text)
        return text

    def _should_replace_auto_discovery_item(self, item: dict[str, Any], seed_key: str | None) -> bool:
        if not seed_key or item.get("seed_key") != seed_key:
            return False
        if item.get("status") not in {"pending", "observed"}:
            return False
        return item.get("layout", {}).get("source") not in {"manual", "saved-map"}

    def _find_existing_device_id(
        self, candidate: dict[str, Any], devices_by_id: dict[str, dict[str, Any]]
    ) -> str | None:
        if candidate["id"] in devices_by_id:
            return candidate["id"]
        candidate_name = (candidate.get("name") or "").strip().lower()
        candidate_fp = self._normalize_identifier(candidate.get("fingerprint"))
        candidate_chassis = self._normalize_identifier(candidate.get("chassis_id"))
        candidate_lldp_name = (candidate.get("lldp_sys_name") or "").strip().lower()
        candidate_mac = self._normalize_identifier(candidate.get("mac") or candidate.get("observed_mac"))
        if candidate.get("device_type") == "endpoint" and candidate_mac:
            for device_id, device in devices_by_id.items():
                if device.get("status") == "pending":
                    continue
                device_fp = self._normalize_identifier(device.get("fingerprint"))
                device_chassis = self._normalize_identifier(device.get("chassis_id"))
                if self._mac_label_key(candidate_mac) in self._infrastructure_device_mac_keys(device):
                    return device_id
                if candidate_fp and device_fp and candidate_fp == device_fp:
                    return device_id
                if candidate_chassis and device_chassis and candidate_chassis == device_chassis:
                    return device_id
            return None
        for device_id, device in devices_by_id.items():
            if device.get("status") == "pending":
                continue
            device_name = (device.get("name") or "").strip().lower()
            device_fp = self._normalize_identifier(device.get("fingerprint"))
            device_chassis = self._normalize_identifier(device.get("chassis_id"))
            interface_ids = {
                self._normalize_identifier(interface.get("if_phys_address"))
                for interface in device.get("interfaces", [])
                if interface.get("if_phys_address")
            }
            device_mac = self._normalize_identifier(device.get("mac") or device.get("observed_mac"))
            if candidate_lldp_name and candidate_lldp_name == device_name:
                return device_id
            if candidate_name and candidate_name == device_name:
                return device_id
            if candidate_fp and device_fp and candidate_fp == device_fp:
                return device_id
            if candidate_chassis and device_chassis and candidate_chassis == device_chassis:
                return device_id
            if candidate_fp and candidate_fp in interface_ids:
                return device_id
            if candidate_chassis and candidate_chassis in interface_ids:
                return device_id
            if candidate_mac and (
                candidate_mac == device_mac
                or candidate_mac in interface_ids
                or self._mac_label_key(candidate_mac) in self._infrastructure_device_mac_keys(device)
            ):
                return device_id
        return None

    def _find_pending_device_id(
        self, device: dict[str, Any], devices_by_id: dict[str, dict[str, Any]]
    ) -> str | None:
        device_name = (device.get("name") or "").strip().lower()
        device_ip = str(device.get("ip") or "").strip()
        device_fp = self._normalize_identifier(device.get("fingerprint"))
        device_chassis = self._normalize_identifier(device.get("chassis_id"))
        device_management_mac = self._normalize_identifier(device.get("management_mac"))
        device_identifiers = {device_fp, device_chassis, device_management_mac}
        device_identifiers.update(
            self._normalize_identifier(interface.get("if_phys_address"))
            for interface in device.get("interfaces", [])
            if interface.get("if_phys_address")
        )
        device_identifiers.discard("")

        pending_devices = [item for item in devices_by_id.values() if item.get("status") == "pending"]
        unique_ip_matches = [
            item["id"]
            for item in pending_devices
            if device_ip and device_ip != "unknown" and str(item.get("ip") or "") == device_ip
        ]

        for pending in pending_devices:
            pending_names = {
                (pending.get("name") or "").strip().lower(),
                (pending.get("lldp_sys_name") or "").strip().lower(),
            }
            pending_names.discard("")
            if device_name and not device_name.startswith("lldp neighbor") and device_name in pending_names:
                return pending["id"]

            pending_identifiers = {
                self._normalize_identifier(pending.get("fingerprint")),
                self._normalize_identifier(pending.get("chassis_id")),
            }
            pending_identifiers.discard("")
            if device_identifiers.intersection(pending_identifiers):
                return pending["id"]

        if len(unique_ip_matches) == 1:
            return unique_ip_matches[0]
        return None

    def _normalize_identifier(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return "".join(char for char in text if char.isalnum())

    def _mac_label_key(self, value: Any) -> str:
        normalized = self._normalize_identifier(value)
        if len(normalized) == 12 and all(char in "0123456789abcdef" for char in normalized):
            return normalized
        return ""

    def _format_mac_key(self, key: str) -> str:
        return ":".join(key[index : index + 2] for index in range(0, 12, 2))

    def _device_label_key(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 200:
            return ""
        return text

    def _apply_device_label_to_device(self, device: dict[str, Any]) -> bool:
        if device.get("device_type") == "switch":
            return False
        key = self._device_label_key(device.get("id"))
        label = self.device_labels.get(key)
        if not label:
            return False
        name = str(label.get("name") or "").strip()
        description = str(label.get("description") or "").strip()
        if name:
            device["name"] = name
            device["name_source"] = "device-label"
        if description:
            device["description"] = description
        device["device_label"] = deepcopy(label)
        return True

    def _apply_device_labels(self) -> int:
        updated = 0
        for device in self.devices:
            if self._apply_device_label_to_device(device):
                updated += 1
        return updated

    def _device_mac_label_keys(self, device: dict[str, Any]) -> list[str]:
        keys: list[str] = []

        def add(value: Any) -> None:
            key = self._mac_label_key(value)
            if key and key not in keys:
                keys.append(key)

        for value in (
            device.get("asset_mac"),
            device.get("mac"),
            device.get("observed_mac"),
            device.get("management_mac"),
            device.get("chassis_id"),
            device.get("fingerprint"),
        ):
            add(value)
        for value in device.get("observed_macs", []) or []:
            add(value)
        return keys

    def _device_mac_label_key(self, device: dict[str, Any]) -> str:
        keys = self._device_mac_label_keys(device)
        return keys[0] if keys else ""

    def _apply_mac_label_to_device(self, device: dict[str, Any]) -> bool:
        key = ""
        label = None
        for candidate_key in self._device_mac_label_keys(device):
            candidate_label = self.mac_labels.get(candidate_key)
            if candidate_label:
                key = candidate_key
                label = candidate_label
                break
        if not label:
            return False
        name = str(label.get("name") or "").strip()
        description = str(label.get("description") or "").strip()
        if name:
            device["name"] = name
            device["name_source"] = "mac-label"
        if description:
            device["description"] = description
        device["asset_mac"] = self._format_mac_key(key)
        device["asset_label"] = deepcopy(label)
        return True

    def _apply_mac_labels(self) -> int:
        updated = 0
        for device in self.devices:
            if self._apply_mac_label_to_device(device):
                updated += 1
        return updated

    def _merge_live_links(
        self,
        links: list[dict[str, Any]],
        candidate_remap: dict[str, str],
        seed_key: str | None,
        candidate_interface_remap: dict[str, str] | None = None,
    ) -> None:
        candidate_interface_remap = candidate_interface_remap or {}
        merged: dict[str, dict[str, Any]] = {}
        devices_by_id = {device.get("id"): device for device in self.devices if device.get("id")}
        for existing_link in self.links:
            if seed_key and existing_link.get("seed_key") == seed_key:
                if not self._is_sticky_observed_infrastructure_mac_link(existing_link, devices_by_id):
                    continue
                missing_polls = int(existing_link.get("missing_polls") or 0) + 1
                if missing_polls > OBSERVED_MAC_LINK_MISSING_POLL_CAP:
                    continue
                link = deepcopy(existing_link)
                link["missing_polls"] = missing_polls
                link["stale"] = True
            else:
                link = deepcopy(existing_link)
            link["from"] = candidate_remap.get(link["from"], link["from"])
            link["to"] = candidate_remap.get(link["to"], link["to"])
            if link.get("from") == link.get("to"):
                continue
            self._classify_management_link(link, devices_by_id)
            link["id"] = self._stable_link_id(link)
            merged[link["id"]] = link
        for raw_link in links:
            link = deepcopy(raw_link)
            old_to = str(link.get("to") or "")
            link["from"] = candidate_remap.get(link["from"], link["from"])
            link["to"] = candidate_remap.get(link["to"], link["to"])
            if old_to in candidate_interface_remap:
                link["to_interface"] = candidate_interface_remap[old_to]
            if link.get("from") == link.get("to"):
                continue
            persisted_management_link = self._persisted_management_port_link(
                str(link.get("from") or ""),
                from_interface=link.get("from_interface"),
                local_port=link.get("local_port"),
            )
            if persisted_management_link and link.get("to") == persisted_management_link.get("to"):
                link["management_mac"] = persisted_management_link.get("management_mac")
                link["to_interface"] = persisted_management_link.get("to_interface")
                link["evidence"] = (
                    f"MAC table persisted management binding; {link.get('evidence') or 'current port observation'}"
                )
            if seed_key:
                link["seed_key"] = seed_key
            link["missing_polls"] = 0
            link["stale"] = False
            link["last_seen"] = now_iso()
            self._classify_management_link(link, devices_by_id)
            link["id"] = self._stable_link_id(link)
            existing = merged.get(link["id"], {})
            merged[link["id"]] = {**existing, **link}
        self.links = list(merged.values())
        self._confirm_reciprocal_links()

    def _persisted_management_port_link(
        self,
        from_device_id: str,
        from_interface: Any = None,
        local_port: Any = None,
    ) -> dict[str, Any] | None:
        interface_id = str(from_interface or "").strip()
        port_name = str(local_port or "").strip().lower()
        if not from_device_id or (not interface_id and not port_name):
            return None
        for link in self.links:
            if link.get("link_type") != "management" or link.get("from") != from_device_id:
                continue
            if interface_id and str(link.get("from_interface") or "").strip() == interface_id:
                return link
            if port_name and str(link.get("local_port") or "").strip().lower() == port_name:
                return link
        return None

    def _classify_management_link(
        self, link: dict[str, Any], devices_by_id: dict[str, dict[str, Any]]
    ) -> bool:
        if "mac table" not in str(link.get("evidence") or "").lower():
            return False
        target = devices_by_id.get(link.get("to"))
        if not target or not self._is_infrastructure_device(target):
            return False
        remote_key = self._mac_label_key(link.get("management_mac") or link.get("remote_port"))
        management_key = self._mac_label_key(target.get("management_mac"))
        if not remote_key or remote_key != management_key:
            return False
        if link.get("link_type") == "management":
            return False

        original_evidence = str(link.get("evidence") or "MAC table")
        observed_at = str(link.get("last_seen") or now_iso())
        link["link_type"] = "management"
        link["management_mac"] = self._format_mac_key(management_key)
        link["status"] = "observed"
        link["to_interface"] = f"{target.get('id')}-management"
        link["remote_port"] = f"management {target.get('ip') or self._format_mac_key(management_key)}"
        link["evidence"] = f"Management ARP + {original_evidence}"
        link["evidence_sources"] = [
            {
                "source": "arp",
                "direction": "management",
                "weight": 78,
                "detail": (
                    f"Seed management IP {target.get('ip') or 'unknown'} resolves to "
                    f"{target.get('management_mac')}"
                ),
                "observed_at": observed_at,
            },
            {
                "source": "fdb",
                "direction": "observed",
                "weight": 52 if "q-bridge" in original_evidence.lower() else 44,
                "detail": original_evidence,
                "observed_at": observed_at,
            },
        ]
        link["topology_decision"] = (
            "Displayed as management link: the FDB MAC matches the ARP identity of an imported seed."
        )
        return True

    def _is_sticky_observed_infrastructure_mac_link(
        self, link: dict[str, Any], devices_by_id: dict[str, dict[str, Any]]
    ) -> bool:
        if link.get("status") != "observed":
            return False
        if "mac table" not in str(link.get("evidence") or "").lower():
            return False
        target = devices_by_id.get(link.get("to"))
        if not target or not self._is_infrastructure_device(target):
            return False
        return bool(self._observed_infrastructure_link_mac_key(target, link))

    def _stable_link_id(self, link: dict[str, Any]) -> str:
        from_if = str(link.get("from_interface") or link.get("local_port") or "any").replace("/", "-")
        to_if = str(link.get("to_interface") or link.get("remote_port") or "any").replace("/", "-")
        return f"link-{link['from']}-{link['to']}-{from_if}-{to_if}"

    def _confirm_reciprocal_links(self) -> None:
        devices_by_id = {device.get("id"): device for device in self.devices if device.get("id")}
        for link in self.links:
            from_device = devices_by_id.get(link.get("from"))
            to_device = devices_by_id.get(link.get("to"))
            if (
                link.get("status") == "pending"
                and str(link.get("evidence") or "").lower().startswith("lldp")
                and from_device
                and to_device
                and from_device.get("status") != "pending"
                and to_device.get("status") != "pending"
                and self._is_infrastructure_device(from_device)
                and self._is_infrastructure_device(to_device)
            ):
                link["status"] = "confirmed"
                if from_device.get("smc_source_key") or to_device.get("smc_source_key"):
                    link["evidence"] = "LLDP one side + SMC adapter match"
                    link.pop("evidence_sources", None)
                    link["evidence_sources"] = self._link_evidence_sources(link)
                    link["topology_decision"] = (
                        "Displayed as direct managed-host link: LLDP chassis MAC matched an SMC network adapter."
                    )
                else:
                    link["evidence"] = "LLDP one side + SNMP seed match"
                    link.pop("evidence_sources", None)
            for other in self.links:
                if link is other:
                    continue
                if link.get("from") == other.get("to") and link.get("to") == other.get("from"):
                    link["status"] = "confirmed"
                    other["status"] = "confirmed"
                    link["evidence"] = "LLDP both sides"
                    other["evidence"] = "LLDP both sides"

    def _alerts_from_live_devices(self, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        thresholds = self.settings.get("thresholds", {})
        error_threshold = int(thresholds.get("interface_error_counter", 1) or 1)
        discard_threshold = int(thresholds.get("interface_discard_counter", 1) or 1)
        for device in devices:
            if not device.get("alerting_enabled"):
                continue
            if device.get("status") == "down":
                source_name = "SMC" if device.get("smc_source_key") else "SNMP"
                alerts.append(
                    {
                        "id": f"live-alert-device-{device['id']}",
                        "device_id": device["id"],
                        "interface_id": None,
                        "title": f"{device['name']} unreachable",
                        "detail": f"{source_name} poll failed repeatedly and the device was marked down",
                        "severity": "critical",
                        "state": "active",
                        "created_at": now_iso(),
                    }
                )
            for interface in device.get("interfaces", []):
                if interface.get("admin_status") == "up" and interface.get("oper_status") not in {"up", "unknown"}:
                    alerts.append(
                        {
                            "id": f"live-alert-{interface['id']}",
                            "device_id": device["id"],
                            "interface_id": interface["id"],
                            "title": f"{interface['name']} oper {interface['oper_status']}",
                            "detail": "live SNMP: admin up + oper not up",
                            "severity": "critical",
                            "state": "active",
                            "created_at": now_iso(),
                        }
                    )
                errors = int(interface.get("in_errors") or 0) + int(interface.get("out_errors") or 0)
                discards = int(interface.get("in_discards") or 0) + int(interface.get("out_discards") or 0)
                if errors >= error_threshold:
                    alerts.append(
                        {
                            "id": f"live-alert-errors-{interface['id']}",
                            "device_id": device["id"],
                            "interface_id": interface["id"],
                            "title": f"{interface['name']} error counters",
                            "detail": f"live SNMP: {errors} cumulative input/output errors",
                            "severity": "warning",
                            "state": "active",
                            "created_at": now_iso(),
                        }
                    )
                if discards >= discard_threshold:
                    alerts.append(
                        {
                            "id": f"live-alert-discards-{interface['id']}",
                            "device_id": device["id"],
                            "interface_id": interface["id"],
                            "title": f"{interface['name']} discard counters",
                            "detail": f"live SNMP: {discards} cumulative input/output discards",
                            "severity": "warning",
                            "state": "active",
                            "created_at": now_iso(),
                        }
                    )
        return alerts

    def _sync_alerts_from_devices(self) -> None:
        generated = self._alerts_from_live_devices(self.devices)
        generated_by_id = {alert["id"]: alert for alert in generated}
        existing_by_id = {alert["id"]: alert for alert in self.alerts}
        next_alerts: list[dict[str, Any]] = []

        for alert_id, alert in generated_by_id.items():
            existing = existing_by_id.get(alert_id)
            if existing:
                alert["created_at"] = existing.get("created_at", alert["created_at"])
                if existing.get("state") == "acknowledged":
                    alert["state"] = "acknowledged"
            next_alerts.append(alert)

        for alert in self.alerts:
            alert_id = alert["id"]
            if not alert_id.startswith("live-alert-") or alert_id in generated_by_id:
                continue
            if alert.get("state") != "resolved":
                resolved = deepcopy(alert)
                resolved["state"] = "resolved"
                resolved["detail"] = f"{resolved.get('detail', '')} (auto-resolved: condition no longer present)".strip()
                next_alerts.append(resolved)

        self.alerts = next_alerts

    def _device(self, device_id: str) -> dict[str, Any] | None:
        return next((device for device in self.devices if device["id"] == device_id), None)
