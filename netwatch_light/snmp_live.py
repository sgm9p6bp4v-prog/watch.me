from __future__ import annotations

import asyncio
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from uuid import uuid5, NAMESPACE_DNS

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    bulk_walk_cmd,
    get_cmd,
    usmAesCfb128Protocol,
    usmDESPrivProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
)


SYSTEM_OIDS = {
    "sys_descr": "1.3.6.1.2.1.1.1.0",
    "sys_object_id": "1.3.6.1.2.1.1.2.0",
    "sys_name": "1.3.6.1.2.1.1.5.0",
}

TABLE_OIDS = {
    "if_descr": "1.3.6.1.2.1.2.2.1.2",
    "if_phys_address": "1.3.6.1.2.1.2.2.1.6",
    "if_admin_status": "1.3.6.1.2.1.2.2.1.7",
    "if_oper_status": "1.3.6.1.2.1.2.2.1.8",
    "if_in_discards": "1.3.6.1.2.1.2.2.1.13",
    "if_in_errors": "1.3.6.1.2.1.2.2.1.14",
    "if_out_discards": "1.3.6.1.2.1.2.2.1.19",
    "if_out_errors": "1.3.6.1.2.1.2.2.1.20",
    "if_name": "1.3.6.1.2.1.31.1.1.1.1",
    "if_hc_in_octets": "1.3.6.1.2.1.31.1.1.1.6",
    "if_hc_out_octets": "1.3.6.1.2.1.31.1.1.1.10",
    "if_high_speed": "1.3.6.1.2.1.31.1.1.1.15",
    "if_alias": "1.3.6.1.2.1.31.1.1.1.18",
}

FAST_TABLE_OIDS = {
    "if_admin_status": "1.3.6.1.2.1.2.2.1.7",
    "if_oper_status": "1.3.6.1.2.1.2.2.1.8",
    "if_in_octets": "1.3.6.1.2.1.2.2.1.10",
    "if_in_discards": "1.3.6.1.2.1.2.2.1.13",
    "if_in_errors": "1.3.6.1.2.1.2.2.1.14",
    "if_out_octets": "1.3.6.1.2.1.2.2.1.16",
    "if_out_discards": "1.3.6.1.2.1.2.2.1.19",
    "if_out_errors": "1.3.6.1.2.1.2.2.1.20",
    "if_hc_in_octets": "1.3.6.1.2.1.31.1.1.1.6",
    "if_hc_out_octets": "1.3.6.1.2.1.31.1.1.1.10",
}

LLDP_OIDS = {
    "remote_chassis_id_subtype": "1.0.8802.1.1.2.1.4.1.1.4",
    "remote_chassis_id": "1.0.8802.1.1.2.1.4.1.1.5",
    "remote_port_id_subtype": "1.0.8802.1.1.2.1.4.1.1.6",
    "remote_port_id": "1.0.8802.1.1.2.1.4.1.1.7",
    "remote_port_desc": "1.0.8802.1.1.2.1.4.1.1.8",
    "remote_sys_name": "1.0.8802.1.1.2.1.4.1.1.9",
    "remote_sys_desc": "1.0.8802.1.1.2.1.4.1.1.10",
}

LLDP_REMOTE_MGMT_OIDS = {
    "remote_mgmt_if_subtype": "1.0.8802.1.1.2.1.4.2.1.3",
}

LLDP_LOCAL_OIDS = {
    "local_port_id": "1.0.8802.1.1.2.1.3.7.1.3",
    "local_port_desc": "1.0.8802.1.1.2.1.3.7.1.4",
}

FS_ENTERPRISE_OID = "1.3.6.1.4.1.52642"
FS_LLDP_OID = f"{FS_ENTERPRISE_OID}.127"
FS_LLDP_OIDS = {
    name: oid.replace("1.0.8802.1.1.2", FS_LLDP_OID)
    for name, oid in LLDP_OIDS.items()
}
FS_LLDP_LOCAL_OIDS = {
    name: oid.replace("1.0.8802.1.1.2", FS_LLDP_OID)
    for name, oid in LLDP_LOCAL_OIDS.items()
}
FS_LLDP_REMOTE_MGMT_OIDS = {
    # FS stores the address as an OctetString value indexed like the remote row.
    "remote_mgmt_address": f"{FS_LLDP_OID}.1.4.2.1.2",
}

BRIDGE_OIDS = {
    "base_port_if_index": "1.3.6.1.2.1.17.1.4.1.2",
    "fdb_port": "1.3.6.1.2.1.17.4.3.1.2",
    "fdb_status": "1.3.6.1.2.1.17.4.3.1.3",
}

Q_BRIDGE_OIDS = {
    "q_fdb_port": "1.3.6.1.2.1.17.7.1.2.2.1.2",
    "q_fdb_status": "1.3.6.1.2.1.17.7.1.2.2.1.3",
    "q_pvid": "1.3.6.1.2.1.17.7.1.4.5.1.1",
    "q_vlan_static_name": "1.3.6.1.2.1.17.7.1.4.3.1.1",
    "q_vlan_static_egress_ports": "1.3.6.1.2.1.17.7.1.4.3.1.2",
    "q_vlan_static_untagged_ports": "1.3.6.1.2.1.17.7.1.4.3.1.4",
}

ARP_OIDS = {
    "ip_net_to_media_phys_address": "1.3.6.1.2.1.4.22.1.2",
    "ip_net_to_media_net_address": "1.3.6.1.2.1.4.22.1.3",
}

ADMIN_STATUS = {1: "up", 2: "down", 3: "testing"}
OPER_STATUS = {
    1: "up",
    2: "down",
    3: "testing",
    4: "unknown",
    5: "dormant",
    6: "notPresent",
    7: "lowerLayerDown",
}
MAX_ENDPOINT_MACS_PER_PORT = 8
MULTI_MAC_SEGMENT_THRESHOLD = 1
NEIGHBOR_MAC_PATTERN = re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{1,2}:){5}[0-9a-fA-F]{1,2}(?![0-9a-fA-F])")


@dataclass
class SnmpSeedConfig:
    host: str
    port: int = 161
    version: str = "2c"
    community: str = ""
    username: str = ""
    auth_key: str = ""
    priv_key: str = ""
    auth_protocol: str = "SHA"
    priv_protocol: str = "AES"


def _is_printable(text: str) -> bool:
    if not text:
        return False
    printable = sum(1 for char in text if char.isprintable())
    return printable / len(text) > 0.85


def _text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "asOctets"):
        raw = bytes(value.asOctets())
        if not raw:
            return ""
        try:
            decoded = raw.decode("utf-8", errors="ignore").strip("\x00").strip()
            if _is_printable(decoded):
                return decoded
        except UnicodeDecodeError:
            pass
        return ":".join(f"{byte:02x}" for byte in raw)
    if hasattr(value, "prettyPrint"):
        return value.prettyPrint()
    return str(value)


def _octets_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "asOctets"):
        raw = bytes(value.asOctets())
        if not raw:
            return ""
        return ":".join(f"{byte:02x}" for byte in raw)
    return _text(value)


def _normalize_mac(value: Any) -> str:
    text = _octets_text(value) if hasattr(value, "asOctets") else str(value or "")
    text = text.strip().lower().replace("-", ":").replace(".", "")
    if ":" in text:
        parts = [part.zfill(2) for part in text.split(":") if part]
        if len(parts) == 6 and all(len(part) == 2 for part in parts):
            return ":".join(parts)
    compact = "".join(char for char in text if char in "0123456789abcdef")
    if len(compact) == 12:
        return ":".join(compact[index : index + 2] for index in range(0, 12, 2))
    return ""


def _neighbor_mac_from_text(value: str) -> str:
    for candidate in NEIGHBOR_MAC_PATTERN.findall(value or ""):
        mac = _normalize_mac(candidate)
        if _is_usable_endpoint_mac(mac):
            return mac
    return ""


async def _resolve_management_mac(host: str) -> str:
    try:
        address = ip_address(host)
    except ValueError:
        return ""
    if address.version != 4:
        return ""

    if sys.platform == "darwin":
        commands = [("arp", "-n", host)]
    elif sys.platform.startswith("linux"):
        commands = [("ip", "neigh", "show", host), ("arp", "-n", host)]
    elif sys.platform.startswith("win"):
        commands = [("arp", "-a", host)]
    else:
        commands = [("arp", "-n", host)]

    for command in commands:
        executable = shutil.which(command[0])
        if not executable:
            fallback = Path("/usr/sbin") / command[0]
            if not fallback.exists():
                continue
            executable = str(fallback)
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *command[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=1.5)
        except (OSError, asyncio.TimeoutError):
            continue
        mac = _neighbor_mac_from_text(stdout.decode("utf-8", errors="ignore"))
        if process.returncode == 0 and mac:
            return mac
    return ""


def _mac_from_suffix(index: str) -> str:
    parts = index.split(".")
    if len(parts) < 6:
        return ""
    octets = [_int(part, -1) for part in parts[-6:]]
    if any(octet < 0 or octet > 255 for octet in octets):
        return ""
    return ":".join(f"{octet:02x}" for octet in octets)


def _is_usable_endpoint_mac(mac: str) -> bool:
    normalized = _normalize_mac(mac)
    if not normalized:
        return False
    octets = [int(part, 16) for part in normalized.split(":")]
    return not (
        all(octet == 0 for octet in octets)
        or all(octet == 255 for octet in octets)
        or bool(octets[0] & 1)
    )


def _lldp_id_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "asOctets"):
        raw = bytes(value.asOctets())
        if not raw:
            return ""
        try:
            decoded = raw.decode("utf-8").strip("\x00").strip()
        except UnicodeDecodeError:
            decoded = ""
        if decoded and all(32 <= ord(char) <= 126 for char in decoded):
            return decoded
        return ":".join(f"{byte:02x}" for byte in raw)
    return _text(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _suffix(oid: Any, base: str) -> str | None:
    base_parts = tuple(int(part) for part in base.split("."))
    if hasattr(oid, "asTuple"):
        oid_parts = tuple(int(part) for part in oid.asTuple())
    else:
        oid_text = _text(oid)
        oid_parts = tuple(int(part) for part in oid_text.strip(".").split(".") if part.isdigit())
    if oid_parts == base_parts:
        return ""
    if oid_parts[: len(base_parts)] != base_parts:
        return None
    return ".".join(str(part) for part in oid_parts[len(base_parts) :])


def _auth_data(config: SnmpSeedConfig) -> CommunityData | UsmUserData:
    if config.version == "3":
        auth_protocol = {
            "NONE": usmNoAuthProtocol,
            "MD5": usmHMACMD5AuthProtocol,
            "SHA": usmHMACSHAAuthProtocol,
        }.get(config.auth_protocol.upper(), usmHMACSHAAuthProtocol)
        priv_protocol = {
            "NONE": usmNoPrivProtocol,
            "DES": usmDESPrivProtocol,
            "AES": usmAesCfb128Protocol,
        }.get(config.priv_protocol.upper(), usmAesCfb128Protocol)
        return UsmUserData(
            config.username,
            authKey=config.auth_key or None,
            privKey=config.priv_key or None,
            authProtocol=auth_protocol,
            privProtocol=priv_protocol,
        )
    return CommunityData(config.community, mpModel=1)


async def _target(config: SnmpSeedConfig) -> UdpTransportTarget:
    return await UdpTransportTarget.create((config.host, config.port), timeout=2, retries=1)


async def _get_system(config: SnmpSeedConfig) -> dict[str, str]:
    error_indication, error_status, error_index, var_binds = await get_cmd(
        SnmpEngine(),
        _auth_data(config),
        await _target(config),
        ContextData(),
        *(ObjectType(ObjectIdentity(oid)) for oid in SYSTEM_OIDS.values()),
    )
    if error_indication:
        raise RuntimeError(str(error_indication))
    if error_status:
        problem = var_binds[int(error_index) - 1][0] if error_index else "unknown OID"
        raise RuntimeError(f"{error_status.prettyPrint()} at {problem}")
    return {key: _text(var_binds[index][1]) for index, key in enumerate(SYSTEM_OIDS)}


async def _walk_table(
    config: SnmpSeedConfig,
    base_oid: str,
    max_rows: int | None = None,
    snmp_engine: SnmpEngine | None = None,
    transport_target: UdpTransportTarget | None = None,
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    async for error_indication, error_status, error_index, var_binds in bulk_walk_cmd(
        snmp_engine or SnmpEngine(),
        _auth_data(config),
        transport_target or await _target(config),
        ContextData(),
        0,
        25,
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=False,
    ):
        if error_indication:
            raise RuntimeError(str(error_indication))
        if error_status:
            problem = var_binds[int(error_index) - 1][0] if error_index else "unknown OID"
            raise RuntimeError(f"{error_status.prettyPrint()} at {problem}")
        for oid, value in var_binds:
            index = _suffix(oid, base_oid)
            if index is not None:
                rows[index] = value
                if max_rows is not None and len(rows) >= max_rows:
                    return rows
    return rows


async def _walk_many(config: SnmpSeedConfig, tables: dict[str, str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for name, oid in tables.items():
        results[name] = await _walk_table(config, oid)
    return results


async def _walk_many_bounded(
    config: SnmpSeedConfig,
    tables: dict[str, str],
    concurrency: int = 4,
) -> dict[str, dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    snmp_engine = SnmpEngine()
    transport_target = await _target(config)

    async def walk_item(name: str, oid: str) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            return name, await _walk_table(
                config,
                oid,
                snmp_engine=snmp_engine,
                transport_target=transport_target,
            )

    pairs = await asyncio.gather(*(walk_item(name, oid) for name, oid in tables.items()))
    return dict(pairs)


async def _walk_optional_table(config: SnmpSeedConfig, oid: str, max_rows: int = 768) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(_walk_table(config, oid, max_rows=max_rows), timeout=8)
    except (RuntimeError, TimeoutError):
        return {}


async def _walk_many_optional(config: SnmpSeedConfig, tables: dict[str, str]) -> dict[str, dict[str, Any]]:
    async def walk_item(name: str, oid: str) -> tuple[str, dict[str, Any]]:
        try:
            return name, await _walk_optional_table(config, oid)
        except RuntimeError:
            return name, {}

    pairs = await asyncio.gather(*(walk_item(name, oid) for name, oid in tables.items()))
    return dict(pairs)


def _status(table: dict[str, Any], index: str, mapping: dict[int, str]) -> str:
    return mapping.get(_int(table.get(index)), "unknown")


def _device_id(host: str, sys_name: str, sys_object_id: str) -> str:
    seed = f"{host}|{sys_name}|{sys_object_id}"
    return f"live-{uuid5(NAMESPACE_DNS, seed).hex[:12]}"


def _candidate_id(seed_device_id: str, index: str, name: str) -> str:
    seed = f"{seed_device_id}|{index}|{name}"
    return f"candidate-{uuid5(NAMESPACE_DNS, seed).hex[:12]}"


def _endpoint_id(seed_device_id: str, mac: str) -> str:
    return f"endpoint-{uuid5(NAMESPACE_DNS, f'{seed_device_id}|mac|{mac}').hex[:12]}"


def _shared_segment_id(seed_device_id: str, if_index: str) -> str:
    return f"segment-{uuid5(NAMESPACE_DNS, f'{seed_device_id}|shared-mac-port|{if_index}').hex[:12]}"


def _lldp_local_port_num(index: str) -> str:
    parts = index.split(".")
    if len(parts) >= 3:
        return parts[1]
    if len(parts) == 2:
        return parts[1]
    return index


def _lldp_remote_index(mgmt_index: str) -> str:
    parts = mgmt_index.split(".")
    if len(parts) < 6:
        return ""
    return ".".join(parts[:3])


def _lldp_mgmt_ip_from_index(mgmt_index: str) -> str:
    parts = mgmt_index.split(".")
    if len(parts) < 9:
        return ""
    subtype = parts[3]
    length = _int(parts[4])
    address = parts[5 : 5 + length]
    if subtype != "1" or length != 4 or len(address) != 4:
        return ""
    octets = [_int(part, -1) for part in address]
    if any(octet < 0 or octet > 255 for octet in octets) or all(octet == 0 for octet in octets):
        return ""
    return ".".join(str(octet) for octet in octets)


def _lldp_management_addresses(rows: dict[str, Any]) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for index in rows:
        remote_index = _lldp_remote_index(index)
        ip = _lldp_mgmt_ip_from_index(index)
        if remote_index and ip:
            addresses.setdefault(remote_index, ip)
    return addresses


def _fs_lldp_management_addresses(rows: dict[str, Any]) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for remote_index, raw_address in rows.items():
        address = _text(raw_address).strip()
        octets = address.split(".")
        if len(octets) != 4:
            continue
        values = [_int(part, -1) for part in octets]
        if any(value < 0 or value > 255 for value in values) or all(value == 0 for value in values):
            continue
        addresses.setdefault(remote_index, ".".join(str(value) for value in values))
    return addresses


def _has_lldp_remote_rows(tables: dict[str, dict[str, Any]]) -> bool:
    return any(tables.get(name) for name in LLDP_OIDS)


def _select_lldp_remote_indexes(
    lldp: dict[str, dict[str, Any]],
    remote_mgmt_ips: dict[str, str],
    oid_profile: str,
    fdb_rows: list[dict[str, str]],
) -> tuple[list[str], list[str]]:
    indexes = sorted(
        set(lldp["remote_chassis_id"])
        | set(lldp["remote_port_id"])
        | set(lldp["remote_port_desc"])
        | set(lldp["remote_sys_name"])
        | set(lldp["remote_sys_desc"]),
        key=lambda item: [int(part) if part.isdigit() else part for part in item.split(".")],
    )
    if oid_profile != "fs-private":
        return indexes, []

    fdb_locations = {(row["mac"], row["if_index"]) for row in fdb_rows}
    accepted: list[str] = []
    suppressed: list[str] = []
    for index in indexes:
        has_identity = bool(
            _text(lldp["remote_sys_name"].get(index)).strip()
            or _text(lldp["remote_sys_desc"].get(index)).strip()
            or remote_mgmt_ips.get(index)
        )
        chassis_mac = _normalize_mac(_lldp_id_text(lldp["remote_chassis_id"].get(index)))
        local_port_num = _lldp_local_port_num(index)
        if has_identity or (chassis_mac and (chassis_mac, local_port_num) in fdb_locations):
            accepted.append(index)
        else:
            suppressed.append(index)
    return accepted, suppressed


def _arp_ip_by_mac(arp: dict[str, dict[str, Any]]) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for index, raw_mac in arp["ip_net_to_media_phys_address"].items():
        mac = _normalize_mac(raw_mac)
        parts = index.split(".")
        ip = ".".join(parts[-4:]) if len(parts) >= 5 else ""
        if mac and ip and ip != "0.0.0.0":
            addresses.setdefault(mac, ip)
    return addresses


def _fdb_rows(
    bridge: dict[str, dict[str, Any]],
    q_bridge: dict[str, dict[str, Any]],
    bridge_port_to_if_index: dict[str, str],
) -> list[dict[str, str]]:
    rows_by_key: dict[tuple[str, str], dict[str, str]] = {}

    for index, raw_port in bridge["fdb_port"].items():
        mac = _mac_from_suffix(index)
        bridge_port = str(_int(raw_port))
        status = _int(bridge["fdb_status"].get(index), 3)
        if status not in {3, 5} or not _is_usable_endpoint_mac(mac):
            continue
        if_index = bridge_port_to_if_index.get(bridge_port)
        if not if_index:
            continue
        rows_by_key[(mac, if_index)] = {
            "mac": mac,
            "if_index": if_index,
            "bridge_port": bridge_port,
            "vlan": "",
            "source_table": "BRIDGE-MIB",
        }

    for index, raw_port in q_bridge["q_fdb_port"].items():
        mac = _mac_from_suffix(index)
        bridge_port = str(_int(raw_port))
        status = _int(q_bridge["q_fdb_status"].get(index), 3)
        parts = index.split(".")
        vlan = parts[0] if len(parts) >= 7 else ""
        if status not in {3, 5} or not _is_usable_endpoint_mac(mac):
            continue
        if_index = bridge_port_to_if_index.get(bridge_port)
        if not if_index:
            continue
        rows_by_key.setdefault(
            (mac, if_index),
            {
                "mac": mac,
                "if_index": if_index,
                "bridge_port": bridge_port,
                "vlan": vlan,
                "source_table": "Q-BRIDGE-MIB",
            },
        )

    return list(rows_by_key.values())


def _portlist_bridge_ports(value: Any) -> list[str]:
    if value is None or not hasattr(value, "asOctets"):
        return []
    ports: list[str] = []
    for byte_index, byte in enumerate(bytes(value.asOctets())):
        for bit_index in range(8):
            if byte & (1 << (7 - bit_index)):
                ports.append(str(byte_index * 8 + bit_index + 1))
    return ports


def _sorted_vlan_ids(values: set[str]) -> list[str]:
    return sorted(values, key=lambda value: _int(value, 4096))


def _vlan_config_by_if_index(
    q_bridge: dict[str, dict[str, Any]],
    bridge_port_to_if_index: dict[str, str],
) -> dict[str, dict[str, Any]]:
    vlan_names = {
        str(_int(vlan_id)): _text(raw_name)
        for vlan_id, raw_name in q_bridge["q_vlan_static_name"].items()
        if _int(vlan_id) > 0
    }
    port_data: dict[str, dict[str, Any]] = {}

    def data_for(if_index: str) -> dict[str, Any]:
        return port_data.setdefault(
            if_index,
            {
                "vlan_pvid": "",
                "vlan_tagged": set(),
                "vlan_untagged": set(),
            },
        )

    for bridge_port, raw_pvid in q_bridge["q_pvid"].items():
        if_index = bridge_port_to_if_index.get(str(_int(bridge_port)))
        pvid = str(_int(raw_pvid))
        if if_index and pvid != "0":
            data_for(if_index)["vlan_pvid"] = pvid

    for vlan_id, raw_ports in q_bridge["q_vlan_static_egress_ports"].items():
        vlan = str(_int(vlan_id))
        if vlan == "0":
            continue
        for bridge_port in _portlist_bridge_ports(raw_ports):
            if_index = bridge_port_to_if_index.get(bridge_port)
            if if_index:
                data_for(if_index)["vlan_tagged"].add(vlan)

    for vlan_id, raw_ports in q_bridge["q_vlan_static_untagged_ports"].items():
        vlan = str(_int(vlan_id))
        if vlan == "0":
            continue
        for bridge_port in _portlist_bridge_ports(raw_ports):
            if_index = bridge_port_to_if_index.get(bridge_port)
            if if_index:
                item = data_for(if_index)
                item["vlan_untagged"].add(vlan)
                item["vlan_tagged"].discard(vlan)

    result: dict[str, dict[str, Any]] = {}
    for if_index, item in port_data.items():
        tagged = _sorted_vlan_ids(set(item["vlan_tagged"]))
        untagged = _sorted_vlan_ids(set(item["vlan_untagged"]))
        pvid = item["vlan_pvid"]
        all_vlans = set(tagged) | set(untagged)
        if pvid:
            all_vlans.add(pvid)
        memberships = []
        for vlan in _sorted_vlan_ids(all_vlans):
            if vlan == pvid and vlan in untagged:
                mode = "pvid untagged"
            elif vlan == pvid and vlan in tagged:
                mode = "pvid tagged"
            elif vlan == pvid:
                mode = "pvid"
            elif vlan in untagged:
                mode = "untagged"
            else:
                mode = "tagged"
            memberships.append(
                {
                    "vlan": vlan,
                    "name": vlan_names.get(vlan, ""),
                    "mode": mode,
                }
            )
        result[if_index] = {
            "vlan_pvid": pvid,
            "vlan_tagged": tagged,
            "vlan_untagged": untagged,
            "vlans": _sorted_vlan_ids(all_vlans),
            "vlan_memberships": memberships,
        }
    return result


def _vendor_from_mac(mac: str) -> str:
    oui = _normalize_mac(mac).replace(":", "")[:6]
    vendors = {
        "649d99": "FS",
        "c400ad": "unknown",
        "f4f19e": "unknown",
        "000ec6": "Siemens",
        "001b1b": "Siemens",
        "0021ba": "Siemens",
        "28e9a4": "Siemens",
    }
    return vendors.get(oui, "unknown")


async def discover_seed_metrics(config: SnmpSeedConfig) -> dict[str, Any]:
    tables = await _walk_many_bounded(config, FAST_TABLE_OIDS)
    indexes = sorted(
        set().union(*(set(table) for table in tables.values())),
        key=lambda item: [int(part) if part.isdigit() else part for part in item.split(".")],
    )
    interfaces = []
    for index in indexes:
        hc_in = tables["if_hc_in_octets"].get(index)
        hc_out = tables["if_hc_out_octets"].get(index)
        interfaces.append(
            {
                "if_index": index,
                "admin_status": _status(tables["if_admin_status"], index, ADMIN_STATUS),
                "oper_status": _status(tables["if_oper_status"], index, OPER_STATUS),
                "in_octets": _int(hc_in if hc_in is not None else tables["if_in_octets"].get(index)),
                "out_octets": _int(hc_out if hc_out is not None else tables["if_out_octets"].get(index)),
                "in_errors": _int(tables["if_in_errors"].get(index)),
                "out_errors": _int(tables["if_out_errors"].get(index)),
                "in_discards": _int(tables["if_in_discards"].get(index)),
                "out_discards": _int(tables["if_out_discards"].get(index)),
            }
        )
    return {
        "system": {},
        "interfaces": interfaces,
        "counts": {"interfaces": len(interfaces)},
    }


async def discover_seed(config: SnmpSeedConfig, labeled_macs: set[str] | None = None) -> dict[str, Any]:
    labeled_endpoint_macs = {_normalize_mac(mac) for mac in (labeled_macs or set())}
    labeled_endpoint_macs.discard("")
    system, tables, lldp, local_lldp, remote_mgmt, bridge, q_bridge, arp = await asyncio.gather(
        _get_system(config),
        _walk_many(config, TABLE_OIDS),
        _walk_many(config, LLDP_OIDS),
        _walk_many(config, LLDP_LOCAL_OIDS),
        _walk_many_optional(config, LLDP_REMOTE_MGMT_OIDS),
        _walk_many_optional(config, BRIDGE_OIDS),
        _walk_many_optional(config, Q_BRIDGE_OIDS),
        _walk_many_optional(config, ARP_OIDS),
    )

    sys_name = system.get("sys_name") or config.host
    sys_descr = system.get("sys_descr") or "SNMP device"
    sys_object_id = system.get("sys_object_id") or "unknown"
    management_mac = await _resolve_management_mac(config.host)
    lldp_oid_profile = "standard"
    if sys_object_id.startswith(FS_ENTERPRISE_OID) and not _has_lldp_remote_rows(lldp):
        fs_lldp, fs_local_lldp, fs_remote_mgmt = await asyncio.gather(
            _walk_many(config, FS_LLDP_OIDS),
            _walk_many(config, FS_LLDP_LOCAL_OIDS),
            _walk_many_optional(config, FS_LLDP_REMOTE_MGMT_OIDS),
        )
        if _has_lldp_remote_rows(fs_lldp) or any(fs_local_lldp.values()):
            lldp = fs_lldp
            local_lldp = fs_local_lldp
            remote_mgmt = fs_remote_mgmt
            lldp_oid_profile = "fs-private"
    device_id = _device_id(config.host, sys_name, sys_object_id)

    indexes = sorted(
        set(tables["if_descr"])
        | set(tables["if_name"])
        | set(tables["if_admin_status"])
        | set(tables["if_oper_status"]),
        key=lambda item: [int(part) if part.isdigit() else part for part in item.split(".")],
    )
    interfaces = []
    interface_by_index: dict[str, dict[str, Any]] = {}
    for index in indexes:
        name = _text(tables["if_name"].get(index)) or _text(tables["if_descr"].get(index)) or f"if{index}"
        interface = {
            "id": f"{device_id}-if-{index}",
            "name": name,
            "if_index": index,
            "if_alias": _text(tables["if_alias"].get(index)),
            "if_descr": _text(tables["if_descr"].get(index)),
            "if_phys_address": _octets_text(tables["if_phys_address"].get(index)),
            "if_high_speed": _int(tables["if_high_speed"].get(index)),
            "admin_status": _status(tables["if_admin_status"], index, ADMIN_STATUS),
            "oper_status": _status(tables["if_oper_status"], index, OPER_STATUS),
            "in_bps": None,
            "out_bps": None,
            "in_octets": _int(tables["if_hc_in_octets"].get(index)),
            "out_octets": _int(tables["if_hc_out_octets"].get(index)),
            "in_errors": _int(tables["if_in_errors"].get(index)),
            "out_errors": _int(tables["if_out_errors"].get(index)),
            "in_discards": _int(tables["if_in_discards"].get(index)),
            "out_discards": _int(tables["if_out_discards"].get(index)),
            "vlan_pvid": "",
            "vlan_tagged": [],
            "vlan_untagged": [],
            "vlans": [],
            "vlan_memberships": [],
            "alerting_enabled": True,
        }
        interfaces.append(interface)
        interface_by_index[index] = interface

    candidates = []
    candidate_links = []
    bridge_port_to_if_index = {
        str(_int(port)): str(_int(raw_if_index))
        for port, raw_if_index in bridge["base_port_if_index"].items()
        if _int(port) > 0 and _int(raw_if_index) > 0
    }
    endpoint_rows = _fdb_rows(bridge, q_bridge, bridge_port_to_if_index)
    if lldp_oid_profile == "fs-private":
        remote_mgmt_rows = remote_mgmt["remote_mgmt_address"]
        remote_mgmt_ips = _fs_lldp_management_addresses(remote_mgmt_rows)
    else:
        remote_mgmt_rows = remote_mgmt["remote_mgmt_if_subtype"]
        remote_mgmt_ips = _lldp_management_addresses(remote_mgmt_rows)
    remote_indexes, suppressed_remote_indexes = _select_lldp_remote_indexes(
        lldp,
        remote_mgmt_ips,
        lldp_oid_profile,
        endpoint_rows,
    )
    for index in remote_indexes:
        remote_chassis_id = _lldp_id_text(lldp["remote_chassis_id"].get(index))
        remote_sys_name = _text(lldp["remote_sys_name"].get(index)).strip()
        remote_sys_desc = _text(lldp["remote_sys_desc"].get(index)).strip()
        remote_mgmt_ip = remote_mgmt_ips.get(index, "")
        remote_name = (
            remote_sys_name
            or (remote_sys_desc.splitlines()[0][:80] if remote_sys_desc else "")
            or (f"LLDP neighbor {remote_chassis_id}" if remote_chassis_id else "")
            or f"LLDP neighbor {index}"
        )
        candidate_id = _candidate_id(device_id, index, remote_name)
        local_port_num = _lldp_local_port_num(index)
        local_interface = interface_by_index.get(local_port_num)
        local_port_id = _lldp_id_text(local_lldp["local_port_id"].get(local_port_num))
        local_port_desc = _text(local_lldp["local_port_desc"].get(local_port_num))
        local_port_name = (
            local_interface.get("name")
            if local_interface
            else local_port_id or local_port_desc or f"lldpLocalPort {local_port_num}"
        )
        remote_port_id = _lldp_id_text(lldp["remote_port_id"].get(index))
        remote_port_desc = _text(lldp["remote_port_desc"].get(index))
        remote_port_name = remote_port_id or remote_port_desc or "remote"
        candidates.append(
            {
                "id": candidate_id,
                "name": remote_name,
                "ip": remote_mgmt_ip or "unknown",
                "vendor": "unknown",
                "model": remote_sys_desc.splitlines()[0][:80] if remote_sys_desc else "LLDP candidate",
                "status": "pending",
                "fingerprint": remote_chassis_id or f"lldp-{index}",
                "chassis_id": remote_chassis_id,
                "lldp_sys_name": remote_sys_name,
                "lldp_sys_desc": remote_sys_desc,
                "lldp_mgmt_ip": remote_mgmt_ip,
                "lldp_index": index,
                "lldp_local_port_num": local_port_num,
                "lldp_local_port": local_port_name,
                "lldp_remote_port": remote_port_name,
                "alerting_enabled": False,
                "layout": {"x": 780, "y": 160 + len(candidates) * 110, "locked": False, "source": "auto"},
                "interfaces": [
                    {
                        "id": f"{candidate_id}-remote",
                        "name": remote_port_name,
                        "if_alias": remote_port_desc,
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
            }
        )
        candidate_links.append(
            {
                "id": f"live-link-{device_id}-{candidate_id}-{local_port_num}",
                "from": device_id,
                "to": candidate_id,
                "from_interface": local_interface["id"] if local_interface else None,
                "to_interface": f"{candidate_id}-remote",
                "local_port": local_port_name,
                "remote_port": remote_port_name,
                "status": "pending",
                "evidence": "LLDP one side from seed",
            }
        )

    lldp_local_if_indexes = {_lldp_local_port_num(index) for index in remote_indexes}
    known_macs = {
        _normalize_mac(interface.get("if_phys_address"))
        for interface in interfaces
        if interface.get("if_phys_address")
    }
    known_macs.update(_normalize_mac(candidate.get("chassis_id")) for candidate in candidates)
    known_macs.discard("")
    vlan_config_by_if_index = _vlan_config_by_if_index(q_bridge, bridge_port_to_if_index)
    for if_index, vlan_config in vlan_config_by_if_index.items():
        if if_index in interface_by_index:
            interface_by_index[if_index].update(vlan_config)
    ip_by_mac = _arp_ip_by_mac(arp)
    endpoint_rows.sort(key=lambda row: [int(part) if part.isdigit() else part for part in row["if_index"].split(".")] + [row["mac"]])
    fdb_macs_by_if_index: dict[str, set[str]] = {}
    fdb_rows_by_if_index: dict[str, list[dict[str, str]]] = {}
    for row in endpoint_rows:
        fdb_macs_by_if_index.setdefault(row["if_index"], set()).add(row["mac"])
        fdb_rows_by_if_index.setdefault(row["if_index"], []).append(row)

    endpoint_count = 0
    segment_count = 0
    seen_endpoint_macs: set[str] = set()
    occupied_endpoint_if_indexes: set[str] = set()
    shared_segment_if_indexes = {
        if_index
        for if_index, macs in fdb_macs_by_if_index.items()
        if len(macs) > MULTI_MAC_SEGMENT_THRESHOLD
    }
    for row in endpoint_rows:
        mac = row["mac"]
        if_index = row["if_index"]
        port_mac_count = len(fdb_macs_by_if_index.get(if_index, set()))
        is_labeled_endpoint = mac in labeled_endpoint_macs
        is_shared_segment_port = if_index in shared_segment_if_indexes
        if (
            mac in seen_endpoint_macs
            or mac in known_macs
            or if_index in lldp_local_if_indexes
            or (is_shared_segment_port and not is_labeled_endpoint)
        ):
            continue
        local_interface = interface_by_index.get(if_index)
        if not local_interface:
            continue
        endpoint_id = _endpoint_id(device_id, mac)
        ip = ip_by_mac.get(mac, "")
        vlan = row.get("vlan") or ""
        local_port_name = local_interface.get("name") or f"if{if_index}"
        local_port_alias = (local_interface.get("if_alias") or "").strip()
        vendor = _vendor_from_mac(mac)
        if local_port_alias:
            name = local_port_alias if port_mac_count == 1 else f"{local_port_alias} {mac}"
        else:
            name = f"{vendor} endpoint {mac}" if vendor != "unknown" else f"Endpoint {mac}"
        model_parts = ["MAC table endpoint"]
        if vlan:
            model_parts.append(f"VLAN/FDB {vlan}")
        candidates.append(
            {
                "id": endpoint_id,
                "name": name,
                "ip": ip or "unknown",
                "vendor": vendor,
                "model": " / ".join(model_parts),
                "status": "observed",
                "device_type": "endpoint",
                "fingerprint": mac,
                "chassis_id": mac,
                "mac": mac,
                "observed_ip": ip,
                "observed_vlan": vlan,
                "observed_source": row["source_table"],
                "observed_local_port": local_port_name,
                "observed_local_port_alias": local_port_alias,
                "alerting_enabled": False,
                "layout": {"x": 940, "y": 180 + endpoint_count * 95, "locked": False, "source": "auto"},
                "interfaces": [
                    {
                        "id": f"{endpoint_id}-mac",
                        "name": mac,
                        "if_alias": f"seen on {local_port_name}",
                        "admin_status": "unknown",
                        "oper_status": "observed",
                        "in_bps": None,
                        "out_bps": None,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": False,
                    }
                ],
            }
        )
        candidate_links.append(
            {
                "id": f"mac-link-{device_id}-{endpoint_id}-{if_index}",
                "from": device_id,
                "to": endpoint_id,
                "from_interface": local_interface["id"],
                "to_interface": f"{endpoint_id}-mac",
                "local_port": local_port_name,
                "remote_port": mac,
                "status": "observed",
                "evidence": f"MAC table {row['source_table']}",
            }
        )
        seen_endpoint_macs.add(mac)
        occupied_endpoint_if_indexes.add(if_index)
        endpoint_count += 1

    for if_index in sorted(shared_segment_if_indexes, key=lambda value: [int(part) if part.isdigit() else part for part in value.split(".")]):
        if if_index in lldp_local_if_indexes:
            continue
        local_interface = interface_by_index.get(if_index)
        if not local_interface:
            continue
        rows = [
            row
            for row in fdb_rows_by_if_index.get(if_index, [])
            if row["mac"] not in known_macs and row["mac"] not in labeled_endpoint_macs
        ]
        macs = sorted({row["mac"] for row in rows})
        if len(macs) <= MULTI_MAC_SEGMENT_THRESHOLD:
            continue
        local_port_name = local_interface.get("name") or f"if{if_index}"
        local_port_alias = (local_interface.get("if_alias") or "").strip()
        segment_id = _shared_segment_id(device_id, if_index)
        vlans = sorted({row.get("vlan") or "" for row in rows if row.get("vlan")}, key=lambda value: _int(value, 4096))
        source_tables = sorted({row.get("source_table") or "FDB" for row in rows})
        tagged_vlans = local_interface.get("vlan_tagged") or []
        segment_name = f"{local_port_alias or local_port_name} shared segment"
        model_parts = [f"{len(macs)} MACs on one switch port"]
        if vlans:
            model_parts.append(f"FDB VLANs {', '.join(vlans[:6])}{' +' + str(len(vlans) - 6) if len(vlans) > 6 else ''}")
        if len(tagged_vlans) > 1:
            model_parts.append(f"tagged trunk ({len(tagged_vlans)} VLANs)")
        candidates.append(
            {
                "id": segment_id,
                "name": segment_name,
                "ip": "unknown",
                "vendor": "shared-port",
                "model": " / ".join(model_parts),
                "status": "observed",
                "device_type": "segment",
                "fingerprint": f"shared-mac-port-{device_id}-{if_index}",
                "chassis_id": "",
                "mac": "",
                "observed_ip": "",
                "observed_vlan": ", ".join(vlans),
                "observed_source": " / ".join(source_tables),
                "observed_local_port": local_port_name,
                "observed_local_port_alias": local_port_alias,
                "observed_macs": macs,
                "observed_mac_count": len(macs),
                "observed_vlans": vlans,
                "alerting_enabled": False,
                "layout": {"x": 940, "y": 180 + (endpoint_count + segment_count) * 95, "locked": False, "source": "auto"},
                "interfaces": [
                    {
                        "id": f"{segment_id}-port",
                        "name": local_port_name,
                        "if_alias": f"{len(macs)} MACs observed on {local_port_name}",
                        "admin_status": "unknown",
                        "oper_status": "observed",
                        "in_bps": None,
                        "out_bps": None,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": False,
                    }
                ],
            }
        )
        candidate_links.append(
            {
                "id": f"segment-link-{device_id}-{segment_id}-{if_index}",
                "from": device_id,
                "to": segment_id,
                "from_interface": local_interface["id"],
                "to_interface": f"{segment_id}-port",
                "local_port": local_port_name,
                "remote_port": f"{len(macs)} MACs",
                "status": "observed",
                "evidence": f"Shared MAC segment from {'/'.join(source_tables)}",
            }
        )
        occupied_endpoint_if_indexes.add(if_index)
        segment_count += 1

    described_endpoint_count = 0
    for if_index, local_interface in interface_by_index.items():
        local_port_alias = (local_interface.get("if_alias") or "").strip()
        if (
            not local_port_alias
            or if_index in lldp_local_if_indexes
            or if_index in occupied_endpoint_if_indexes
            or local_interface.get("oper_status") != "up"
        ):
            continue
        local_port_name = local_interface.get("name") or f"if{if_index}"
        endpoint_id = f"endpoint-{uuid5(NAMESPACE_DNS, f'{device_id}|portdesc|{if_index}|{local_port_alias}').hex[:12]}"
        portdesc_macs = sorted(
            {
                row["mac"]
                for row in fdb_rows_by_if_index.get(if_index, [])
                if row["mac"] not in known_macs
            }
        )
        candidates.append(
            {
                "id": endpoint_id,
                "name": local_port_alias,
                "ip": "unknown",
                "vendor": "unknown",
                "model": "Port description endpoint",
                "status": "observed",
                "device_type": "endpoint",
                "fingerprint": portdesc_macs[0] if len(portdesc_macs) == 1 else f"portdesc-{device_id}-{if_index}",
                "chassis_id": portdesc_macs[0] if len(portdesc_macs) == 1 else "",
                "mac": portdesc_macs[0] if len(portdesc_macs) == 1 else "",
                "observed_ip": "",
                "observed_vlan": "",
                "observed_source": "PORT-DESCRIPTION",
                "observed_local_port": local_port_name,
                "observed_local_port_alias": local_port_alias,
                "observed_macs": portdesc_macs,
                "observed_mac_count": len(portdesc_macs),
                "alerting_enabled": False,
                "layout": {"x": 940, "y": 180 + (endpoint_count + described_endpoint_count) * 95, "locked": False, "source": "auto"},
                "interfaces": [
                    {
                        "id": f"{endpoint_id}-port",
                        "name": local_port_name,
                        "if_alias": f"described endpoint on {local_port_name}",
                        "admin_status": "unknown",
                        "oper_status": "observed",
                        "in_bps": None,
                        "out_bps": None,
                        "in_errors": 0,
                        "out_errors": 0,
                        "in_discards": 0,
                        "out_discards": 0,
                        "alerting_enabled": False,
                    }
                ],
            }
        )
        candidate_links.append(
            {
                "id": f"portdesc-link-{device_id}-{endpoint_id}-{if_index}",
                "from": device_id,
                "to": endpoint_id,
                "from_interface": local_interface["id"],
                "to_interface": f"{endpoint_id}-port",
                "local_port": local_port_name,
                "remote_port": local_port_alias,
                "status": "observed",
                "evidence": "Port description",
            }
        )
        described_endpoint_count += 1

    device = {
        "id": device_id,
        "name": sys_name,
        "ip": config.host,
        "vendor": "FS" if "FS" in sys_descr.upper() or "FIBERSTORE" in sys_descr.upper() else "unknown",
        "model": sys_descr.splitlines()[0][:80],
        "status": "up",
        "fingerprint": f"uuid+{sys_object_id}+{sys_name}",
        "management_mac": management_mac,
        "management_mac_source": "local-arp" if management_mac else "",
        "alerting_enabled": True,
        "layout": {"x": 240, "y": 180, "locked": True, "source": "manual"},
        "interfaces": interfaces,
    }
    return {
        "device": device,
        "candidates": candidates,
        "links": candidate_links,
        "system": system,
        "counts": {
            "interfaces": len(interfaces),
            "lldp_candidates": len(remote_indexes),
            "if_descr_rows": len(tables["if_descr"]),
            "if_name_rows": len(tables["if_name"]),
            "if_oper_status_rows": len(tables["if_oper_status"]),
            "lldp_remote_sys_name_rows": len(lldp["remote_sys_name"]),
            "lldp_remote_rows": len(remote_indexes),
            "lldp_remote_rows_raw": len(remote_indexes) + len(suppressed_remote_indexes),
            "lldp_remote_rows_suppressed": len(suppressed_remote_indexes),
            "lldp_oid_profile": lldp_oid_profile,
            "lldp_remote_mgmt_rows": len(remote_mgmt_rows),
            "lldp_remote_mgmt_ips": len(remote_mgmt_ips),
            "lldp_local_port_rows": len(local_lldp["local_port_id"]),
            "bridge_fdb_rows": len(bridge["fdb_port"]),
            "q_bridge_fdb_rows": len(q_bridge["q_fdb_port"]),
            "q_bridge_pvid_rows": len(q_bridge["q_pvid"]),
            "q_bridge_vlan_rows": len(q_bridge["q_vlan_static_name"]),
            "q_bridge_vlan_ports": len(vlan_config_by_if_index),
            "arp_rows": len(arp["ip_net_to_media_phys_address"]),
            "management_mac_resolved": bool(management_mac),
            "mac_endpoints": endpoint_count,
            "shared_segments": segment_count,
            "described_endpoints": described_endpoint_count,
            "mac_endpoint_ports_suppressed": sum(
                1 for macs in fdb_macs_by_if_index.values() if len(macs) > MAX_ENDPOINT_MACS_PER_PORT
            ),
            "multi_mac_ports_grouped": len(shared_segment_if_indexes),
        },
    }
