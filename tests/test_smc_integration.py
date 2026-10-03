from __future__ import annotations

import asyncio
import sqlite3

from netwatch_light import polling, snmp_live
from netwatch_light.smc import SmcSourceConfig, discover_smc
from netwatch_light.snmp_live import SnmpSeedConfig
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


def test_polling_policy_stays_automatic_at_thirty_seconds():
    state = NetWatchState()

    result = state.set_backend_polling(False, 900)
    polling_settings = result["snapshot"]["settings"]["polling"]

    assert polling_settings["backend_auto_poll"] is True
    assert polling_settings["backend_interval_seconds"] == 30


def test_deferred_persistence_collapses_repeated_writes():
    state = NetWatchState()
    writes = []

    class RecordingStore:
        def save_payload(self, payload):
            writes.append(payload)

    state.store = RecordingStore()
    with state.deferred_persistence():
        state.persist()
        state.persist()
        state.persist()

    assert len(writes) == 1


def test_poll_runtime_and_run_survive_unrelated_snapshot_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("NETWATCH_MASTER_KEY", "poll-runtime-test-key")
    db_url = f"sqlite:///{tmp_path / 'netwatch.db'}"
    state = NetWatchState(tmp_path / "state.json", db_url)
    state.persist()

    run_id = state.start_poll_run("scheduled poll", seed_count=3)
    state.update_external_poller_status("polling", pid=4321)

    unrelated_writer = NetWatchState(tmp_path / "state.json", db_url)
    unrelated_writer.events.insert(0, {"id": "ui-write", "time": "12:00:00", "text": "UI state saved"})
    unrelated_writer.persist()

    reloaded = NetWatchState(tmp_path / "state.json", db_url)
    active_run = next(run for run in reloaded.poll_runs if run["id"] == run_id)
    assert active_run["source"] == "scheduled poll"
    assert active_run["status"] == "running"
    assert reloaded.settings["polling"]["external_poller_status"] == "polling"
    assert reloaded.settings["polling"]["external_poller_pid"] == 4321

    reloaded.finish_poll_run(run_id, "succeeded", successes=3)
    final_state = NetWatchState(tmp_path / "state.json", db_url)
    finished_run = next(run for run in final_state.poll_runs if run["id"] == run_id)
    assert finished_run["status"] == "succeeded"
    assert finished_run["duration_ms"] is not None


def test_interrupted_poll_runs_are_closed_on_poller_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("NETWATCH_MASTER_KEY", "poll-recovery-test-key")
    db_url = f"sqlite:///{tmp_path / 'netwatch.db'}"
    state = NetWatchState(tmp_path / "state.json", db_url)
    state.persist()
    run_id = state.start_poll_run("scheduled metrics", seed_count=2)

    restarted = NetWatchState(tmp_path / "state.json", db_url, load_history=False)
    assert restarted.recover_interrupted_poll_runs() == 1

    final_state = NetWatchState(tmp_path / "state.json", db_url, load_history=False)
    recovered = next(run for run in final_state.poll_runs if run["id"] == run_id)
    assert recovered["status"] == "failed"
    assert recovered["finished_at"]
    assert recovered["error"] == "poller stopped before this cycle completed"


def test_poll_cycle_discovers_multiple_sources_concurrently(monkeypatch):
    state = NetWatchState()
    state.smc_sources = []
    active = 0
    peak = 0

    async def fake_discover(config, labeled_macs=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"system": {"sys_name": config.host}, "counts": {}}

    monkeypatch.setattr(polling, "discover_seed", fake_discover)
    monkeypatch.setattr(state, "reload", lambda: None)
    monkeypatch.setattr(state, "start_poll_run", lambda source, seed_count=0: "run-1")
    monkeypatch.setattr(state, "finish_poll_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(state, "register_live_seed", lambda metadata: None)
    monkeypatch.setattr(
        state,
        "import_live_discovery",
        lambda discovery, key, **kwargs: {"event": {"id": key}, "snapshot": {}},
    )
    monkeypatch.setattr(state, "add_event", lambda text: {"text": text})
    monkeypatch.setattr(state, "snapshot", lambda: {})
    configs = {
        f"10.0.0.{index}:161": SnmpSeedConfig(f"10.0.0.{index}", community="test")
        for index in range(1, 4)
    }

    result = asyncio.run(polling.poll_live_seeds(state, "test", configs))

    assert peak > 1
    assert result["successes"] == 3


def test_fast_snmp_discovery_uses_hc_counters_with_32_bit_fallback(monkeypatch):
    async def fake_walk_tables(config, tables, concurrency=4):
        return {
            "if_admin_status": {"1": 1, "2": 1},
            "if_oper_status": {"1": 1, "2": 2},
            "if_in_octets": {"1": 100, "2": 200},
            "if_out_octets": {"1": 300, "2": 400},
            "if_hc_in_octets": {"1": 1_000},
            "if_hc_out_octets": {"1": 3_000},
            "if_in_discards": {},
            "if_in_errors": {},
            "if_out_discards": {},
            "if_out_errors": {},
        }

    monkeypatch.setattr(snmp_live, "_walk_many_bounded", fake_walk_tables)

    metrics = asyncio.run(snmp_live.discover_seed_metrics(SnmpSeedConfig("10.0.0.1", community="test")))
    interfaces = {interface["if_index"]: interface for interface in metrics["interfaces"]}

    assert interfaces["1"]["in_octets"] == 1_000
    assert interfaces["1"]["out_octets"] == 3_000
    assert interfaces["2"]["in_octets"] == 200
    assert interfaces["2"]["out_octets"] == 400
    assert interfaces["2"]["oper_status"] == "down"


def test_fast_metrics_update_existing_inventory_without_replacing_it():
    state = NetWatchState()
    state.devices = [
        {
            "id": "switch-1",
            "name": "Switch 1",
            "status": "unknown",
            "device_type": "switch",
            "seed_key": "10.0.0.1:161",
            "interfaces": [
                {
                    "id": "switch-1-if-1",
                    "if_index": "1",
                    "name": "Ethernet 1",
                    "if_alias": "uplink",
                    "admin_status": "up",
                    "oper_status": "unknown",
                    "in_octets": 100,
                    "out_octets": 200,
                    "in_bps": None,
                    "out_bps": None,
                }
            ],
        }
    ]
    state.seeds = [{
        "key": "10.0.0.1:161",
        "status": "unknown",
        "last_error": "timeout",
        "last_counts": {"lldp_candidates": 2, "interfaces": 1},
    }]
    state.links = []
    first = {
        "interfaces": [
            {
                "if_index": "1",
                "admin_status": "up",
                "oper_status": "up",
                "in_octets": 200,
                "out_octets": 300,
                "in_errors": 0,
                "out_errors": 0,
                "in_discards": 0,
                "out_discards": 0,
            }
        ],
        "counts": {"interfaces": 1},
    }
    second = {
        **first,
        "interfaces": [{**first["interfaces"][0], "in_octets": 400, "out_octets": 600}],
    }

    state.import_live_metrics(first, "10.0.0.1:161")
    state.record_interface_history()
    state.import_live_metrics(second, "10.0.0.1:161")
    state.record_interface_history()

    interface = state.devices[0]["interfaces"][0]
    assert state.devices[0]["status"] == "up"
    assert interface["name"] == "Ethernet 1"
    assert interface["if_alias"] == "uplink"
    assert interface["oper_status"] == "up"
    assert interface["in_bps"] is not None
    assert interface["out_bps"] is not None
    assert state.seeds[0]["last_error"] == ""
    assert state.seeds[0]["last_counts"]["lldp_candidates"] == 2
    assert len(state.interface_history["switch-1::switch-1-if-1"]) == 2


def test_database_history_is_incremental_and_not_embedded_in_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("NETWATCH_MASTER_KEY", "history-storage-test-key")
    db_path = tmp_path / "netwatch.db"
    state = NetWatchState(tmp_path / "state.json", f"sqlite:///{db_path}")
    state.devices = [
        {
            "id": "switch-1",
            "name": "Switch 1",
            "status": "up",
            "device_type": "switch",
            "interfaces": [
                {
                    "id": "switch-1-if-1",
                    "if_index": "1",
                    "name": "Ethernet 1",
                    "admin_status": "up",
                    "oper_status": "up",
                    "in_bps": 100,
                    "out_bps": 200,
                }
            ],
        }
    ]
    state.interface_history = {}

    state.record_interface_history()
    state.persist()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from interface_samples").fetchone()[0] == 1
        assert conn.execute(
            "select json_type(payload, '$.interface_history') from app_state where key = 'snapshot'"
        ).fetchone()[0] is None

    reloaded = NetWatchState(tmp_path / "state.json", f"sqlite:///{db_path}")
    assert len(reloaded.interface_history["switch-1::switch-1-if-1"]) == 1
