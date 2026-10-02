from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.sql import func

from .security import CredentialCipher, sanitize_seed_credential


metadata = MetaData()


app_state = Table(
    "app_state",
    metadata,
    Column("key", String(80), primary_key=True),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

devices = Table(
    "devices",
    metadata,
    Column("id", String(200), primary_key=True),
    Column("name", String(255), nullable=False, index=True),
    Column("ip", String(80), nullable=False, default="unknown"),
    Column("vendor", String(120), nullable=False, default="unknown"),
    Column("model", Text, nullable=False, default=""),
    Column("status", String(40), nullable=False, index=True),
    Column("device_type", String(40), nullable=False, default="switch", index=True),
    Column("fingerprint", Text, nullable=False, default=""),
    Column("alerting_enabled", Boolean, nullable=False, default=True),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

interfaces = Table(
    "interfaces",
    metadata,
    Column("id", String(240), primary_key=True),
    Column("device_id", String(200), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("if_index", String(80), nullable=False, default=""),
    Column("if_alias", Text, nullable=False, default=""),
    Column("admin_status", String(40), nullable=False, default="unknown"),
    Column("oper_status", String(40), nullable=False, default="unknown"),
    Column("in_bps", Float, nullable=True),
    Column("out_bps", Float, nullable=True),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

links = Table(
    "links",
    metadata,
    Column("id", String(260), primary_key=True),
    Column("from_device_id", String(200), nullable=False, index=True),
    Column("to_device_id", String(200), nullable=False, index=True),
    Column("from_interface_id", String(240), nullable=True, index=True),
    Column("to_interface_id", String(240), nullable=True, index=True),
    Column("status", String(40), nullable=False, index=True),
    Column("evidence", Text, nullable=False, default=""),
    Column("confidence", Integer, nullable=True, index=True),
    Column("directness", String(40), nullable=True, index=True),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

topology_evidence = Table(
    "topology_evidence",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("link_id", String(260), ForeignKey("links.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("source", String(80), nullable=False, index=True),
    Column("direction", String(40), nullable=False, default=""),
    Column("weight", Integer, nullable=False, default=0),
    Column("detail", Text, nullable=False, default=""),
    Column("observed_at", String(80), nullable=False, default=""),
    Column("payload", JSON, nullable=False),
)

seeds = Table(
    "seeds",
    metadata,
    Column("key", String(260), primary_key=True),
    Column("host", String(255), nullable=False, index=True),
    Column("port", Integer, nullable=False, default=161),
    Column("version", String(20), nullable=False, default="2c"),
    Column("sys_name", String(255), nullable=False, default=""),
    Column("sys_object_id", Text, nullable=False, default=""),
    Column("status", String(40), nullable=False, index=True),
    Column("last_error", Text, nullable=False, default=""),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

smc_sources = Table(
    "smc_sources",
    metadata,
    Column("key", String(260), primary_key=True),
    Column("host", String(255), nullable=False, index=True),
    Column("port", Integer, nullable=False, default=80),
    Column("scheme", String(20), nullable=False, default="http"),
    Column("hostname", String(255), nullable=False, default=""),
    Column("serial", String(120), nullable=False, default=""),
    Column("status", String(40), nullable=False, index=True),
    Column("last_error", Text, nullable=False, default=""),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

seed_credentials = Table(
    "seed_credentials",
    metadata,
    Column("key", String(260), primary_key=True),
    Column("host", String(255), nullable=False, index=True),
    Column("port", Integer, nullable=False, default=161),
    Column("version", String(20), nullable=False, default="2c"),
    Column("encrypted", Boolean, nullable=False, default=False, index=True),
    Column("key_id", String(80), nullable=False, default="", index=True),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

alerts = Table(
    "alerts",
    metadata,
    Column("id", String(260), primary_key=True),
    Column("device_id", String(200), nullable=True, index=True),
    Column("interface_id", String(240), nullable=True, index=True),
    Column("state", String(40), nullable=False, index=True),
    Column("severity", String(40), nullable=False, index=True),
    Column("title", Text, nullable=False, default=""),
    Column("created_at", String(80), nullable=False, default=""),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

events = Table(
    "events",
    metadata,
    Column("id", String(260), primary_key=True),
    Column("ts", String(80), nullable=False, index=True),
    Column("text", Text, nullable=False, default=""),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

mac_labels = Table(
    "mac_labels",
    metadata,
    Column("mac", String(80), primary_key=True),
    Column("name", String(160), nullable=False, default=""),
    Column("description", Text, nullable=False, default=""),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

device_labels = Table(
    "device_labels",
    metadata,
    Column("device_id", String(200), primary_key=True),
    Column("name", String(160), nullable=False, default=""),
    Column("description", Text, nullable=False, default=""),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

topology_layouts = Table(
    "topology_layouts",
    metadata,
    Column("device_id", String(200), primary_key=True),
    Column("x", Float, nullable=False),
    Column("y", Float, nullable=False),
    Column("locked", Boolean, nullable=False, default=False),
    Column("source", String(80), nullable=False, default="saved-map"),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

interface_samples = Table(
    "interface_samples",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("device_id", String(200), nullable=False, index=True),
    Column("interface_id", String(240), nullable=False, index=True),
    Column("ts", Float, nullable=False, index=True),
    Column("in_bps", Float, nullable=True),
    Column("out_bps", Float, nullable=True),
)

metric_samples = Table(
    "metric_samples",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("metric_name", String(160), nullable=False, index=True),
    Column("target_type", String(40), nullable=False, index=True),
    Column("target_id", String(260), nullable=False, index=True),
    Column("device_id", String(200), nullable=True, index=True),
    Column("interface_id", String(240), nullable=True, index=True),
    Column("ts", Float, nullable=False, index=True),
    Column("value_float", Float, nullable=True),
    Column("value_text", Text, nullable=True),
    Column("quality", String(40), nullable=False, default="ok", index=True),
    Column("labels", JSON, nullable=False),
    Column("payload", JSON, nullable=False),
)

poll_runs = Table(
    "poll_runs",
    metadata,
    Column("id", String(260), primary_key=True),
    Column("source", String(160), nullable=False, default="poll", index=True),
    Column("status", String(40), nullable=False, default="unknown", index=True),
    Column("started_at", String(80), nullable=False, default="", index=True),
    Column("finished_at", String(80), nullable=False, default=""),
    Column("duration_ms", Integer, nullable=True),
    Column("seed_count", Integer, nullable=False, default=0),
    Column("successes", Integer, nullable=False, default=0),
    Column("failures", Integer, nullable=False, default=0),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def default_database_url(root_dir: Path) -> str:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        return configured
    sqlite_path = root_dir / "data" / "netwatch.db"
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{sqlite_path.as_posix()}"


def create_storage_engine(database_url: str) -> Engine:
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, future=True, connect_args=connect_args)


class DatabaseStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = create_storage_engine(database_url)
        self.credential_cipher = CredentialCipher(database_url)
        metadata.create_all(self.engine)

    def load_payload(self) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(app_state.c.payload).where(app_state.c.key == "snapshot")).first()
            credential_rows = conn.execute(select(seed_credentials)).mappings().all()
        if row is None:
            return None
        payload = row[0]
        if not isinstance(payload, dict):
            return None
        payload = dict(payload)
        if credential_rows:
            payload["seed_credentials"] = self._load_seed_credentials(credential_rows)
        else:
            payload["seed_credentials"] = [
                credential
                for credential in payload.get("seed_credentials", [])
                if isinstance(credential, dict) and credential.get("key")
            ]
        return payload

    def save_payload(self, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            self._replace_full_payload(conn, payload, now)

    def _replace_full_payload(self, conn: Any, payload: dict[str, Any], now: datetime) -> None:
        storage_payload = self._sanitized_payload(payload)
        conn.execute(delete(app_state))
        conn.execute(app_state.insert().values(key="snapshot", payload=storage_payload, updated_at=now))

        for table in (
            poll_runs,
            metric_samples,
            interface_samples,
            topology_layouts,
            device_labels,
            mac_labels,
            events,
            alerts,
            seed_credentials,
            smc_sources,
            seeds,
            topology_evidence,
            links,
            interfaces,
            devices,
        ):
            conn.execute(delete(table))

        device_rows = []
        interface_rows = []
        layout_rows = []
        for device in payload.get("devices", []):
            if not isinstance(device, dict) or not device.get("id"):
                continue
            device_rows.append(
                {
                    "id": str(device["id"]),
                    "name": str(device.get("name") or device["id"]),
                    "ip": str(device.get("ip") or "unknown"),
                    "vendor": str(device.get("vendor") or "unknown"),
                    "model": str(device.get("model") or ""),
                    "status": str(device.get("status") or "unknown"),
                    "device_type": str(device.get("device_type") or "switch"),
                    "fingerprint": str(device.get("fingerprint") or ""),
                    "alerting_enabled": bool(device.get("alerting_enabled", True)),
                    "payload": device,
                    "updated_at": now,
                }
            )
            layout = device.get("layout")
            if isinstance(layout, dict) and "x" in layout and "y" in layout:
                layout_rows.append(
                    {
                        "device_id": str(device["id"]),
                        "x": float(layout.get("x") or 0),
                        "y": float(layout.get("y") or 0),
                        "locked": bool(layout.get("locked", False)),
                        "source": str(layout.get("source") or "auto"),
                        "payload": layout,
                        "updated_at": now,
                    }
                )
            for interface in device.get("interfaces", []):
                if not isinstance(interface, dict) or not interface.get("id"):
                    continue
                interface_rows.append(
                    {
                        "id": str(interface["id"]),
                        "device_id": str(device["id"]),
                        "name": str(interface.get("name") or interface["id"]),
                        "if_index": str(interface.get("if_index") or ""),
                        "if_alias": str(interface.get("if_alias") or ""),
                        "admin_status": str(interface.get("admin_status") or "unknown"),
                        "oper_status": str(interface.get("oper_status") or "unknown"),
                        "in_bps": _nullable_float(interface.get("in_bps")),
                        "out_bps": _nullable_float(interface.get("out_bps")),
                        "payload": interface,
                        "updated_at": now,
                    }
                )

        if device_rows:
            conn.execute(devices.insert(), device_rows)
        if interface_rows:
            conn.execute(interfaces.insert(), interface_rows)
        if layout_rows:
            conn.execute(topology_layouts.insert(), layout_rows)

        link_rows = []
        for link in payload.get("links", []):
            if not isinstance(link, dict) or not link.get("id"):
                continue
            link_rows.append(
                {
                    "id": str(link["id"]),
                    "from_device_id": str(link.get("from") or ""),
                    "to_device_id": str(link.get("to") or ""),
                    "from_interface_id": _nullable_string(link.get("from_interface")),
                    "to_interface_id": _nullable_string(link.get("to_interface")),
                    "status": str(link.get("status") or "unknown"),
                    "evidence": str(link.get("evidence") or ""),
                    "confidence": _nullable_int(link.get("confidence")),
                    "directness": _nullable_string(link.get("directness")),
                    "payload": link,
                    "updated_at": now,
                }
            )
        if link_rows:
            conn.execute(links.insert(), link_rows)

        evidence_rows = []
        for link in payload.get("links", []):
            if not isinstance(link, dict) or not link.get("id"):
                continue
            for evidence in link.get("evidence_sources", []) or []:
                if not isinstance(evidence, dict):
                    continue
                evidence_rows.append(
                    {
                        "link_id": str(link["id"]),
                        "source": str(evidence.get("source") or "unknown"),
                        "direction": str(evidence.get("direction") or ""),
                        "weight": int(evidence.get("weight") or 0),
                        "detail": str(evidence.get("detail") or ""),
                        "observed_at": str(evidence.get("observed_at") or link.get("last_seen") or ""),
                        "payload": evidence,
                    }
                )
        if evidence_rows:
            conn.execute(topology_evidence.insert(), evidence_rows)

        seed_rows = []
        for seed in payload.get("seeds", []):
            if not isinstance(seed, dict) or not seed.get("key"):
                continue
            seed_rows.append(
                {
                    "key": str(seed["key"]),
                    "host": str(seed.get("host") or ""),
                    "port": int(seed.get("port") or 161),
                    "version": str(seed.get("version") or "2c"),
                    "sys_name": str(seed.get("sys_name") or ""),
                    "sys_object_id": str(seed.get("sys_object_id") or ""),
                    "status": str(seed.get("status") or "unknown"),
                    "last_error": str(seed.get("last_error") or ""),
                    "payload": seed,
                    "updated_at": now,
                }
            )
        if seed_rows:
            conn.execute(seeds.insert(), seed_rows)

        smc_source_rows = []
        for source in payload.get("smc_sources", []):
            if not isinstance(source, dict) or not source.get("key"):
                continue
            smc_source_rows.append(
                {
                    "key": str(source["key"]),
                    "host": str(source.get("host") or ""),
                    "port": int(source.get("port") or 80),
                    "scheme": str(source.get("scheme") or "http"),
                    "hostname": str(source.get("hostname") or ""),
                    "serial": str(source.get("serial") or ""),
                    "status": str(source.get("status") or "unknown"),
                    "last_error": str(source.get("last_error") or ""),
                    "payload": source,
                    "updated_at": now,
                }
            )
        if smc_source_rows:
            conn.execute(smc_sources.insert(), smc_source_rows)

        credential_rows = []
        for credential in payload.get("seed_credentials", []):
            if not isinstance(credential, dict) or not credential.get("key"):
                continue
            encrypted_payload = self.credential_cipher.encrypt_record(credential)
            credential_rows.append(
                {
                    "key": str(credential["key"]),
                    "host": str(credential.get("host") or ""),
                    "port": int(credential.get("port") or 161),
                    "version": str(credential.get("version") or "2c"),
                    "encrypted": True,
                    "key_id": self.credential_cipher.info.key_id,
                    "payload": encrypted_payload,
                    "updated_at": now,
                }
            )
        if credential_rows:
            conn.execute(seed_credentials.insert(), credential_rows)

        alert_rows = []
        for alert in payload.get("alerts", []):
            if not isinstance(alert, dict) or not alert.get("id"):
                continue
            alert_rows.append(
                {
                    "id": str(alert["id"]),
                    "device_id": _nullable_string(alert.get("device_id")),
                    "interface_id": _nullable_string(alert.get("interface_id")),
                    "state": str(alert.get("state") or "active"),
                    "severity": str(alert.get("severity") or "warning"),
                    "title": str(alert.get("title") or ""),
                    "created_at": str(alert.get("created_at") or ""),
                    "payload": alert,
                    "updated_at": now,
                }
            )
        if alert_rows:
            conn.execute(alerts.insert(), alert_rows)

        event_rows = []
        for index, event in enumerate(payload.get("events", [])):
            if not isinstance(event, dict):
                continue
            ts = str(event.get("ts") or event.get("created_at") or "")
            event_rows.append(
                {
                    "id": str(event.get("id") or f"{index}-{ts}-{event.get('text', '')}")[:260],
                    "ts": ts,
                    "text": str(event.get("text") or event.get("message") or ""),
                    "payload": event,
                    "updated_at": now,
                }
            )
        if event_rows:
            conn.execute(events.insert(), event_rows)

        mac_label_rows = []
        for mac, label in payload.get("mac_labels", {}).items():
            if not isinstance(label, dict):
                continue
            mac_label_rows.append(
                {
                    "mac": str(mac),
                    "name": str(label.get("name") or ""),
                    "description": str(label.get("description") or ""),
                    "payload": label,
                    "updated_at": now,
                }
            )
        if mac_label_rows:
            conn.execute(mac_labels.insert(), mac_label_rows)

        device_label_rows = []
        for device_id, label in payload.get("device_labels", {}).items():
            if not isinstance(label, dict):
                continue
            device_label_rows.append(
                {
                    "device_id": str(device_id),
                    "name": str(label.get("name") or ""),
                    "description": str(label.get("description") or ""),
                    "payload": label,
                    "updated_at": now,
                }
            )
        if device_label_rows:
            conn.execute(device_labels.insert(), device_label_rows)

        poll_run_rows = []
        for run in payload.get("poll_runs", []):
            if not isinstance(run, dict) or not run.get("id"):
                continue
            poll_run_rows.append(
                {
                    "id": str(run["id"]),
                    "source": str(run.get("source") or "poll"),
                    "status": str(run.get("status") or "unknown"),
                    "started_at": str(run.get("started_at") or ""),
                    "finished_at": str(run.get("finished_at") or ""),
                    "duration_ms": _nullable_int(run.get("duration_ms")),
                    "seed_count": int(run.get("seed_count") or 0),
                    "successes": int(run.get("successes") or 0),
                    "failures": int(run.get("failures") or 0),
                    "payload": run,
                    "updated_at": now,
                }
            )
        if poll_run_rows:
            conn.execute(poll_runs.insert(), poll_run_rows)

        sample_rows = []
        metric_sample_rows = []
        for key, samples in payload.get("interface_history", {}).items():
            if not isinstance(samples, list):
                continue
            device_id, interface_id = _split_history_key(key)
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                ts = _nullable_float(sample.get("ts"))
                if ts is None:
                    continue
                sample_rows.append(
                    {
                        "device_id": device_id,
                        "interface_id": interface_id,
                        "ts": ts,
                        "in_bps": _nullable_float(sample.get("in_bps")),
                        "out_bps": _nullable_float(sample.get("out_bps")),
                    }
                )
                for metric in sample.get("metrics", []) or []:
                    if not isinstance(metric, dict) or not metric.get("metric"):
                        continue
                    labels = metric.get("labels") if isinstance(metric.get("labels"), dict) else {}
                    value_float = _nullable_float(metric.get("value_float"))
                    value_text = _nullable_string(metric.get("value_text"))
                    if value_float is None and value_text is None:
                        continue
                    metric_sample_rows.append(
                        {
                            "metric_name": str(metric["metric"]),
                            "target_type": str(metric.get("target_type") or "unknown"),
                            "target_id": str(metric.get("target_id") or labels.get("interface_id") or interface_id),
                            "device_id": str(labels.get("device_id") or device_id or ""),
                            "interface_id": str(labels.get("interface_id") or interface_id or ""),
                            "ts": ts,
                            "value_float": value_float,
                            "value_text": value_text,
                            "quality": str(metric.get("quality") or "ok"),
                            "labels": labels,
                            "payload": metric,
                        }
                    )
        if sample_rows:
            conn.execute(interface_samples.insert(), sample_rows)
        if metric_sample_rows:
            conn.execute(metric_samples.insert(), metric_sample_rows)

    def _sanitized_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(payload)
        sanitized["seed_credentials"] = [
            sanitize_seed_credential(credential)
            for credential in payload.get("seed_credentials", [])
            if isinstance(credential, dict) and credential.get("key")
        ]
        return sanitized

    def _load_seed_credentials(self, credential_rows: list[Any]) -> list[dict[str, Any]]:
        credentials: list[dict[str, Any]] = []
        for row in credential_rows:
            payload = row.get("payload") if hasattr(row, "get") else row["payload"]
            if not isinstance(payload, dict):
                continue
            record = self.credential_cipher.decrypt_record(payload)
            record.setdefault("key", row.get("key") if hasattr(row, "get") else row["key"])
            record.setdefault("host", row.get("host") if hasattr(row, "get") else row["host"])
            record.setdefault("port", row.get("port") if hasattr(row, "get") else row["port"])
            record.setdefault("version", row.get("version") if hasattr(row, "get") else row["version"])
            credentials.append(record)
        return credentials


def _nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nullable_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _split_history_key(key: str) -> tuple[str, str]:
    if "::" not in key:
        return "", key
    device_id, interface_id = key.split("::", 1)
    return device_id, interface_id
