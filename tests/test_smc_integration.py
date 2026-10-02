from __future__ import annotations

import asyncio
import sqlite3

from netwatch_light.smc import SmcSourceConfig, discover_smc
from netwatch_light.state import NetWatchState


SMC_RESPONSES = {
    "/api/localmachine": {"hostname": "vx2-26545", "serial": "26545", "type": "vx 2 (De)"},
    "/api/networkadapters": [
        {
            "name": "B - Steuernetz 10Gbit",
            "mac": "C4-00-AD-D7-26-F6",
            "ip": "192.168.0.145",
            "netmask": "255.255.255.0",
        },
        {
            "name": "C - PosiStageNet 10Gbit",
            "mac": "C4-00-AD-D7-26-F7",
            "ip": "10.101.50.155",
            "netmask": "255.255.0.0",
        },
        {"name": "Loopback Pseudo-Interface", "mac": "", "ip": "127.0.0.1"},
    ],
    "/api/chassis/stats": {"System Temp": 32, "CPU0 Temp": 43},
    "/api/chassis/power/status": {"System Power": "on"},
    "/api/vfcs": [{"name": "SDI", "slot": 1}, {"name": "HD2", "slot": 2}],
    "/api/session": {"role": "Actor"},
    "/api/smc": {"hardwareversion": 2, "apiversion": "1.0.0"},
}


async def _fetch(path: str):
    return SMC_RESPONSES[path]


def _smc_discovery():
    return asyncio.run(discover_smc(SmcSourceConfig("192.168.0.156"), fetch_json=_fetch))


def test_smc_discovery_builds_one_chassis_with_physical_nics():
    discovery = _smc_discovery()

    assert discovery["source"]["key"] == "http://192.168.0.156"
    assert discovery["device"]["id"] == "smc-26545"
    assert discovery["device"]["device_type"] == "media-server"
    assert discovery["device"]["status"] == "up"
    assert discovery["counts"]["interfaces"] == 2
    assert {interface["if_phys_address"] for interface in discovery["device"]["interfaces"]} == {
        "c4:00:ad:d7:26:f6",
        "c4:00:ad:d7:26:f7",
    }


def test_smc_import_reconciles_lldp_candidates_to_distinct_nics_and_propagates_traffic():
    state = NetWatchState()
    switch = {
        "id": "switch-vx2",
        "name": "S3900_VX2_Case_A",
        "ip": "192.168.100.155",
        "status": "up",
        "device_type": "switch",
        "seed_key": "192.168.100.155:161",
        "interfaces": [
            {
                "id": "switch-vx2-if-2",
                "name": "GigaEthernet0/2",
                "admin_status": "up",
                "oper_status": "up",
                "in_bps": 1200,
                "out_bps": 3400,
                "if_high_speed": 1000,
            },
            {
                "id": "switch-vx2-if-22",
                "name": "GigaEthernet0/22",
                "admin_status": "up",
                "oper_status": "up",
                "in_bps": 5600,
                "out_bps": 7800,
                "if_high_speed": 1000,
            },
        ],
    }
    pending = [
        {
            "id": "pending-f6",
            "name": "LLDP neighbor c4:00:ad:d7:26:f6",
            "status": "pending",
            "device_type": "unknown",
            "chassis_id": "c4:00:ad:d7:26:f6",
            "fingerprint": "c4:00:ad:d7:26:f6",
            "interfaces": [],
        },
        {
            "id": "pending-f7",
            "name": "LLDP neighbor c4:00:ad:d7:26:f7",
            "status": "pending",
            "device_type": "unknown",
            "chassis_id": "c4:00:ad:d7:26:f7",
            "fingerprint": "c4:00:ad:d7:26:f7",
            "interfaces": [],
        },
    ]
    state.devices = [switch, *pending]
    state.links = [
        {
            "id": "link-f6",
            "from": switch["id"],
            "to": "pending-f6",
            "from_interface": "switch-vx2-if-2",
            "local_port": "GigaEthernet0/2",
            "status": "pending",
            "evidence": "LLDP one side",
        },
        {
            "id": "link-f7",
            "from": switch["id"],
            "to": "pending-f7",
            "from_interface": "switch-vx2-if-22",
            "local_port": "GigaEthernet0/22",
            "status": "pending",
            "evidence": "LLDP one side",
        },
    ]

    discovery = _smc_discovery()
    state.register_smc_source(discovery["source"])
    state.import_smc_discovery(discovery, discovery["source"]["key"])

    assert {device["id"] for device in state.devices} == {"switch-vx2", "smc-26545"}
    assert len(state.links) == 2
    assert {link["to"] for link in state.links} == {"smc-26545"}
    assert len({link["to_interface"] for link in state.links}) == 2
    assert all(link["status"] == "confirmed" for link in state.links)
    assert all(link["evidence"] == "LLDP one side + SMC adapter match" for link in state.links)
    assert all(link["line_style"] == "solid" for link in state.links)

    media_server = next(device for device in state.devices if device["id"] == "smc-26545")
    nics = {interface["if_phys_address"]: interface for interface in media_server["interfaces"]}
    assert nics["c4:00:ad:d7:26:f6"]["in_bps"] == 3400
    assert nics["c4:00:ad:d7:26:f6"]["out_bps"] == 1200
    assert nics["c4:00:ad:d7:26:f7"]["in_bps"] == 7800
    assert nics["c4:00:ad:d7:26:f7"]["traffic_source"] == "switch-port"


def test_smc_source_and_chassis_persist_in_database(tmp_path, monkeypatch):
    monkeypatch.setenv("NETWATCH_MASTER_KEY", "smc-test-master-key")
    db_path = tmp_path / "netwatch.db"
    db_url = f"sqlite:///{db_path}"
    state = NetWatchState(tmp_path / "state.json", db_url)
    discovery = _smc_discovery()

    state.register_smc_source(discovery["source"])
    state.import_smc_discovery(discovery, discovery["source"]["key"])

    reloaded = NetWatchState(tmp_path / "state.json", db_url)
    assert reloaded.smc_sources[0]["host"] == "192.168.0.156"
    assert next(device for device in reloaded.devices if device.get("device_type") == "media-server")["serial"] == "26545"

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from smc_sources").fetchone()[0] == 1


def test_smc_controller_ip_does_not_absorb_an_unrelated_endpoint():
    state = NetWatchState()
    media_server = _smc_discovery()["device"]
    endpoint = {
        "id": "endpoint-unrelated",
        "name": "NTP appliance",
        "ip": "192.168.0.156",
        "observed_ip": "192.168.0.156",
        "mac": "e4:5f:01:b9:da:b8",
        "fingerprint": "mac+e4:5f:01:b9:da:b8",
        "status": "observed",
        "device_type": "endpoint",
        "interfaces": [],
    }
    state.devices = [media_server, endpoint]
    state.links = []

    assert state._deduplicate_observed_topology() is False
    assert {device["id"] for device in state.devices} == {"smc-26545", "endpoint-unrelated"}
