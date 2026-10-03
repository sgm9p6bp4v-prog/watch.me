from __future__ import annotations

import asyncio

import pytest
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject

from netwatch_light import snmp_live
from netwatch_light.state import NetWatchState
from copy import deepcopy


CONFIG = snmp_live.SnmpSeedConfig(host="192.0.2.1", community="test")
OID = snmp_live.BRIDGE_OIDS["fdb_port"]
ENDPOINT = "00:11:22:33:44:55"
INFRASTRUCTURE = "64:9d:99:08:74:48"


def _mock_walk(monkeypatch, walk):
    async def target(config):
        return object()

    monkeypatch.setattr(snmp_live, "_target", target)
    monkeypatch.setattr(snmp_live, "SnmpEngine", lambda: object())
    monkeypatch.setattr(snmp_live, "bulk_walk_cmd", walk)


@pytest.mark.parametrize("sentinel", [None, EndOfMibView(), NoSuchInstance(), NoSuchObject()])
def test_optional_empty_success_is_complete(monkeypatch, sentinel):
    async def walk(*args, **kwargs):
        if sentinel is not None:
            yield None, 0, 0, [(OID, sentinel)]

    _mock_walk(monkeypatch, walk)
    tables = asyncio.run(snmp_live._walk_many_optional(CONFIG, {"fdb_port": OID}))
    assert tables == {"fdb_port": {}}
    assert tables["fdb_port"].complete is True
    assert tables["fdb_port"].errors == []


@pytest.mark.parametrize("partial", [False, True])
def test_optional_snmp_failure_retains_partial_rows(monkeypatch, partial):
    async def walk(*args, **kwargs):
        if partial:
            yield None, 0, 0, [(f"{OID}.1", 7)]
        yield "No SNMP response received before timeout", 0, 0, []

    _mock_walk(monkeypatch, walk)
    rows = asyncio.run(snmp_live._walk_optional_table(CONFIG, OID))
    assert rows == ({"1": 7} if partial else {})
    assert rows.complete is False
    assert "timeout" in rows.errors[0]


def test_optional_deadline_retains_partial_rows(monkeypatch):
    async def walk(*args, **kwargs):
        yield None, 0, 0, [(f"{OID}.1", 7)]
        await asyncio.Event().wait()

    original_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, timeout=0.001)

    _mock_walk(monkeypatch, walk)
    monkeypatch.setattr(snmp_live.asyncio, "wait_for", short_wait_for)
    rows = asyncio.run(snmp_live._walk_optional_table(CONFIG, OID))
    assert rows == {"1": 7}
    assert rows.complete is False
    assert rows.errors == ["timeout"]


def test_optional_walk_cap_is_incomplete(monkeypatch):
    async def walk(*args, **kwargs):
        yield None, 0, 0, [(f"{OID}.{index}", 7) for index in range(1, 770)]

    _mock_walk(monkeypatch, walk)
    rows = asyncio.run(snmp_live._walk_optional_table(CONFIG, OID))
    assert len(rows) == 768
    assert rows.complete is False
    assert rows.errors == ["max_rows=768 reached"]


def test_optional_plain_dict_mock_at_cap_is_incomplete(monkeypatch):
    async def walk_table(config, oid, max_rows=None):
        return {str(index): 7 for index in range(max_rows)}

    monkeypatch.setattr(snmp_live, "_walk_table", walk_table)
    rows = asyncio.run(snmp_live._walk_optional_table(CONFIG, OID))
    assert len(rows) == 768
    assert rows.complete is False


def test_external_cancellation_is_not_optional_success(monkeypatch):
    started = asyncio.Event()

    async def walk(*args, **kwargs):
        yield None, 0, 0, [(f"{OID}.1", 7)]
        started.set()
        await asyncio.Event().wait()

    async def cancel_walk():
        task = asyncio.create_task(snmp_live._walk_optional_table(CONFIG, OID))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _mock_walk(monkeypatch, walk)
    asyncio.run(cancel_walk())


def test_required_walk_failure_still_raises(monkeypatch):
    async def walk(*args, **kwargs):
        yield None, 0, 0, [(f"{OID}.1", 7)]
        yield "timeout", 0, 0, []

    _mock_walk(monkeypatch, walk)
    with pytest.raises(RuntimeError, match="timeout"):
        asyncio.run(snmp_live._walk_many(CONFIG, {"fdb_port": OID}))


@pytest.fixture
def discovery_tables(monkeypatch):
    definitions = (
        snmp_live.TABLE_OIDS, snmp_live.LLDP_OIDS, snmp_live.LLDP_LOCAL_OIDS,
        snmp_live.LLDP_REMOTE_MGMT_OIDS, snmp_live.BRIDGE_OIDS,
        snmp_live.Q_BRIDGE_OIDS, snmp_live.ARP_OIDS,
        snmp_live.FS_LLDP_OIDS, snmp_live.FS_LLDP_LOCAL_OIDS,
        snmp_live.FS_LLDP_REMOTE_MGMT_OIDS,
    )
    tables = {id(group): {name: {} for name in group} for group in definitions}
    tables[id(snmp_live.TABLE_OIDS)]["if_name"] = {"1": "port1", "2": "port2"}
    system = {"sys_name": "seed", "sys_object_id": "1.3.6.1.4.1.999"}

    async def get_system(config):
        return system

    async def walk_many(config, group):
        return tables[id(group)]

    async def management_mac(host):
        return INFRASTRUCTURE

    monkeypatch.setattr(snmp_live, "_get_system", get_system)
    monkeypatch.setattr(snmp_live, "_walk_many", walk_many)
    monkeypatch.setattr(snmp_live, "_walk_many_optional", walk_many)
    monkeypatch.setattr(snmp_live, "_resolve_management_mac", management_mac)
    return tables, system


def _discover():
    return asyncio.run(snmp_live.discover_seed(CONFIG))


def _add_fdb(tables, macs, *, port="1"):
    bridge = tables[id(snmp_live.BRIDGE_OIDS)]
    bridge["base_port_if_index"][port] = int(port)
    for mac in macs:
        index = ".".join(str(int(octet, 16)) for octet in mac.split(":"))
        bridge["fdb_port"][index] = int(port)
        bridge["fdb_status"][index] = 3


def _failed(rows=None, reason="timeout"):
    return snmp_live._CollectionRows(rows, complete=False, errors=[reason])


def test_full_empty_discovery_has_complete_metadata(discovery_tables):
    assert _discover()["topology_collection"] == {
        "complete": True, "lldp_complete": True, "fdb_complete": True, "errors": [],
    }


@pytest.mark.parametrize("group", [snmp_live.TABLE_OIDS, snmp_live.LLDP_OIDS, snmp_live.LLDP_LOCAL_OIDS])
def test_full_required_collection_failure_still_raises(discovery_tables, monkeypatch, group):
    original = snmp_live._walk_many

    async def walk_many(config, definitions):
        if definitions is group:
            raise RuntimeError("required timeout")
        return await original(config, definitions)

    monkeypatch.setattr(snmp_live, "_walk_many", walk_many)
    with pytest.raises(RuntimeError, match="required timeout"):
        _discover()


@pytest.mark.parametrize("group,name", [
    (snmp_live.BRIDGE_OIDS, "base_port_if_index"),
    (snmp_live.BRIDGE_OIDS, "fdb_port"),
    (snmp_live.BRIDGE_OIDS, "fdb_status"),
    (snmp_live.Q_BRIDGE_OIDS, "q_fdb_port"),
    (snmp_live.Q_BRIDGE_OIDS, "q_fdb_status"),
])
def test_critical_fdb_failure_is_not_authoritative_empty(discovery_tables, group, name):
    tables, _ = discovery_tables
    tables[id(group)][name] = _failed()
    metadata = _discover()["topology_collection"]
    assert metadata["complete"] is False
    assert metadata["fdb_complete"] is False
    assert metadata["lldp_complete"] is True
    assert any(name in error for error in metadata["errors"])


def test_partial_fdb_observations_are_still_usable(discovery_tables):
    tables, _ = discovery_tables
    _add_fdb(tables, [ENDPOINT])
    bridge = tables[id(snmp_live.BRIDGE_OIDS)]
    bridge["fdb_port"] = _failed(bridge["fdb_port"], "max_rows=768 reached")
    result = _discover()
    assert result["topology_collection"]["fdb_complete"] is False
    assert result["counts"]["mac_endpoints"] == 1
    assert result["candidates"][0]["mac"] == ENDPOINT


@pytest.mark.parametrize("problem", ["missing_status", "missing_mapping", "unknown_interface"])
def test_inconsistent_fdb_rows_are_incomplete(discovery_tables, problem):
    tables, _ = discovery_tables
    _add_fdb(tables, [ENDPOINT])
    bridge = tables[id(snmp_live.BRIDGE_OIDS)]
    if problem == "missing_status":
        bridge["fdb_status"] = {}
    elif problem == "missing_mapping":
        bridge["base_port_if_index"] = {}
    else:
        bridge["base_port_if_index"] = {"1": 999}
    assert _discover()["topology_collection"]["fdb_complete"] is False


def test_unrelated_vlan_failures_do_not_gate_topology(discovery_tables):
    tables, _ = discovery_tables
    for name in snmp_live.Q_BRIDGE_OIDS:
        if name not in {"q_fdb_port", "q_fdb_status"}:
            tables[id(snmp_live.Q_BRIDGE_OIDS)][name] = _failed()
    assert _discover()["topology_collection"]["complete"] is True


def test_failed_arp_resolution_gates_observed_endpoints(discovery_tables):
    tables, _ = discovery_tables
    tables[id(snmp_live.ARP_OIDS)]["ip_net_to_media_phys_address"] = _failed()
    assert _discover()["topology_collection"]["fdb_complete"] is True
    _add_fdb(tables, [ENDPOINT])
    assert _discover()["topology_collection"]["fdb_complete"] is False


def test_failed_lldp_management_resolution_is_incomplete(discovery_tables):
    tables, _ = discovery_tables
    tables[id(snmp_live.LLDP_REMOTE_MGMT_OIDS)]["remote_mgmt_if_subtype"] = _failed()
    metadata = _discover()["topology_collection"]
    assert metadata["lldp_complete"] is False
    assert metadata["fdb_complete"] is True
    assert metadata["complete"] is False


@pytest.mark.parametrize("private_failure", [False, True])
def test_fs_completeness_uses_chosen_profile(discovery_tables, private_failure):
    tables, system = discovery_tables
    system["sys_object_id"] = snmp_live.FS_ENTERPRISE_OID + ".1"
    tables[id(snmp_live.LLDP_REMOTE_MGMT_OIDS)]["remote_mgmt_if_subtype"] = _failed()
    tables[id(snmp_live.FS_LLDP_OIDS)]["remote_sys_name"] = {"1.1": "neighbor"}
    if private_failure:
        tables[id(snmp_live.FS_LLDP_REMOTE_MGMT_OIDS)]["remote_mgmt_address"] = _failed()
    result = _discover()
    assert result["counts"]["lldp_oid_profile"] == "fs-private"
    assert result["topology_collection"]["lldp_complete"] is (not private_failure)
    assert not any("standard" in error for error in result["topology_collection"]["errors"])


def test_fs_anonymous_filter_depends_on_complete_fdb(discovery_tables):
    tables, system = discovery_tables
    system["sys_object_id"] = snmp_live.FS_ENTERPRISE_OID + ".1"
    tables[id(snmp_live.FS_LLDP_OIDS)]["remote_chassis_id"] = {"1.1": ENDPOINT}
    tables[id(snmp_live.BRIDGE_OIDS)]["fdb_port"] = _failed()
    result = _discover()
    assert result["counts"]["lldp_remote_rows_suppressed"] == 1
    assert result["topology_collection"]["lldp_complete"] is False


@pytest.mark.parametrize("known_source", ["interface", "management", "neighbor"])
def test_known_macs_do_not_hide_single_endpoint(discovery_tables, known_source):
    tables, _ = discovery_tables
    if known_source == "interface":
        known_mac = "00:aa:bb:cc:dd:ee"
        tables[id(snmp_live.TABLE_OIDS)]["if_phys_address"] = {"2": known_mac}
    elif known_source == "neighbor":
        known_mac = "00:aa:bb:cc:dd:ee"
        tables[id(snmp_live.LLDP_OIDS)]["remote_chassis_id"] = {"3.2.1": known_mac}
    else:
        known_mac = INFRASTRUCTURE
    _add_fdb(tables, [known_mac, ENDPOINT])
    result = _discover()
    assert result["counts"]["mac_endpoints"] == 1
    assert result["counts"]["shared_segments"] == 0
    endpoint = next(candidate for candidate in result["candidates"] if candidate.get("mac") == ENDPOINT)
    assert endpoint["observed_port_mac_count"] == 1


def test_genuine_multi_mac_segment_is_preserved(discovery_tables):
    tables, _ = discovery_tables
    second = "00:11:22:33:44:66"
    _add_fdb(tables, [INFRASTRUCTURE, ENDPOINT, second])
    result = _discover()
    assert result["counts"]["mac_endpoints"] == 0
    assert result["counts"]["shared_segments"] == 1
    assert result["candidates"][0]["observed_macs"] == [ENDPOINT, second]


def test_labeled_endpoint_remains_visible_on_shared_port(discovery_tables):
    tables, _ = discovery_tables
    _add_fdb(tables, [INFRASTRUCTURE, ENDPOINT, "00:11:22:33:44:66", "00:11:22:33:44:77"])
    result = asyncio.run(snmp_live.discover_seed(CONFIG, labeled_macs={ENDPOINT}))
    assert result["counts"]["mac_endpoints"] == 1
    assert result["counts"]["shared_segments"] == 1
    assert result["candidates"][0]["mac"] == ENDPOINT
    assert result["candidates"][0]["observed_port_mac_count"] == 3


def test_all_known_endpoints_keep_shared_port_mac_count(discovery_tables):
    tables, _ = discovery_tables
    endpoints = {ENDPOINT, "00:11:22:33:44:66", "00:11:22:33:44:77"}
    _add_fdb(tables, [INFRASTRUCTURE, *endpoints])
    result = asyncio.run(snmp_live.discover_seed(CONFIG, labeled_macs=endpoints))
    assert result["counts"]["mac_endpoints"] == 3
    assert result["counts"]["shared_segments"] == 0
    assert {candidate["mac"] for candidate in result["candidates"]} == endpoints
    assert all(candidate["observed_port_mac_count"] == 3 for candidate in result["candidates"])


def test_description_endpoint_reports_zero_observed_port_macs(discovery_tables):
    tables, _ = discovery_tables
    tables[id(snmp_live.TABLE_OIDS)]["if_alias"] = {"1": "NAS"}
    tables[id(snmp_live.TABLE_OIDS)]["if_oper_status"] = {"1": 1}
    result = _discover()
    assert result["counts"]["described_endpoints"] == 1
    assert result["candidates"][0]["observed_port_mac_count"] == 0


def test_lldp_uplink_still_suppresses_fdb_endpoints(discovery_tables):
    tables, _ = discovery_tables
    _add_fdb(tables, [ENDPOINT])
    tables[id(snmp_live.LLDP_OIDS)]["remote_sys_name"] = {"3.1.1": "neighbor"}
    assert _discover()["counts"]["mac_endpoints"] == 0


def _seed(ip="192.0.2.1", device_id="live-a"):
    return {
        "id": device_id, "name": device_id, "ip": ip, "status": "up",
        "device_type": "switch", "seed_key": ip + ":161",
        "interfaces": [
            {"id": device_id + "-if-" + str(index), "name": "port" + str(index),
             "if_index": str(index), "oper_status": "up", "admin_status": "up",
             "vlan_untagged": ["1001"], "in_octets": 10, "out_octets": 20,
             "in_bps": 10, "out_bps": 20}
            for index in (1, 2)
        ],
    }


def _endpoint(device_id="endpoint-a", mac=ENDPOINT, status="observed"):
    return {
        "id": device_id, "name": "Endpoint", "status": status, "device_type": "endpoint",
        "seed_key": "192.0.2.1:161", "mac": mac, "fingerprint": mac,
        "observed_source": "Q-BRIDGE-MIB" if mac else "PORT-DESCRIPTION",
        "layout": {"x": 111, "y": 222, "source": "saved-map"},
        "interfaces": [{"id": device_id + "-mac", "name": mac, "in_bps": None, "out_bps": None}],
    }


def _link(seed, target, port=1):
    return {
        "id": "old-link", "from": seed["id"], "to": target["id"],
        "from_interface": seed["id"] + "-if-" + str(port),
        "to_interface": target["interfaces"][0]["id"], "local_port": "port" + str(port),
        "remote_port": target.get("mac") or "description", "status": "observed",
        "evidence": "MAC table Q-BRIDGE-MIB" if target.get("mac") else "Port description",
        "seed_key": seed["seed_key"], "last_seen": "2026-10-01T12:00:00+00:00",
    }


def _state(status="observed"):
    state = NetWatchState()
    seed, endpoint = _seed(), _endpoint(status=status)
    state.devices = [seed, endpoint]
    state.links = [_link(seed, endpoint)]
    state.seeds = [{"key": seed["seed_key"], "status": "up"}]
    state.mac_labels = {ENDPOINT.replace(":", ""): {"mac": ENDPOINT, "name": "Custom asset"}}
    state.device_labels = {}
    return state


def _inventory(seed, candidates=(), links=(), complete=True):
    return {
        "device": deepcopy(seed), "candidates": deepcopy(list(candidates)),
        "links": deepcopy(list(links)), "system": {},
        "counts": {"interfaces": len(seed["interfaces"]), "lldp_candidates": 0},
        "topology_collection": {"complete": complete, "errors": [] if complete else ["timeout"]},
    }


@pytest.mark.parametrize("status", ["observed", "unknown", "down"])
def test_incomplete_inventory_keeps_last_connection_without_aging(status):
    state = _state(status)
    seed = deepcopy(state.devices[0])
    for _ in range(4):
        state.import_live_discovery(_inventory(seed, complete=False), seed["seed_key"], record_history=False)
    assert len(state.links) == 1
    assert len(state.devices) == 2
    link = state.links[0]
    assert link["missing_polls"] == 0
    assert link["needs_confirmation"] is True
    assert link["stale"] is True
    assert link["line_style"] == "dashed"
    assert link["last_seen"] == "2026-10-01T12:00:00+00:00"
    assert "Awaiting reconfirmation" in link["topology_decision"]
    assert "observed_traffic" not in state.devices[1]


@pytest.mark.parametrize("status", ["observed", "unknown", "down"])
def test_only_three_complete_absences_retire_endpoint_and_keep_labels(status):
    state = _state(status)
    seed = deepcopy(state.devices[0])
    for index in range(3):
        state.import_live_discovery(_inventory(seed), seed["seed_key"], record_history=False)
        if index < 2:
            assert len(state.links) == 1
            assert state.links[0]["missing_polls"] == index + 1
    assert state.links == []
    assert [device["id"] for device in state.devices] == [seed["id"]]
    assert state.mac_labels[ENDPOINT.replace(":", "")]["name"] == "Custom asset"


def test_complete_absence_counter_survives_partial_inventory():
    state = _state()
    seed = state.devices[0]
    state.import_live_discovery(_inventory(seed), seed["seed_key"], record_history=False)
    state.import_live_discovery(_inventory(seed, complete=False), seed["seed_key"], record_history=False)
    assert state.links[0]["missing_polls"] == 1


def test_fresh_observation_reconfirms_connection_without_moving_layout():
    state = _state("down")
    seed, endpoint = deepcopy(state.devices)
    state.import_live_discovery(_inventory(seed, complete=False), seed["seed_key"], record_history=False)
    state.import_live_discovery(_inventory(seed, [endpoint], [_link(seed, endpoint)]), seed["seed_key"], record_history=False)
    assert state.links[0]["missing_polls"] == 0
    assert state.links[0]["needs_confirmation"] is False
    assert "Awaiting reconfirmation" not in state.links[0]["topology_decision"]
    assert state.devices[1]["layout"] == endpoint["layout"]
    assert state.devices[1]["name"] == "Custom asset"
    assert state.links[0]["evidence_sources"][0]["observed_at"] == state.links[0]["last_seen"]


def test_failure_counters_are_per_seed_and_success_does_not_reset_other_seed():
    state = _state()
    other = _seed("192.0.2.2", "live-b")
    state.devices.append(other)
    state.seeds.append({"key": other["seed_key"], "status": "up"})
    state.mark_live_poll_failed("timeout", "192.0.2.1:161")
    state.mark_live_poll_failed("timeout", other["seed_key"])
    state.import_live_metrics({"interfaces": []}, other["seed_key"])
    state.mark_live_poll_failed("timeout", "192.0.2.1:161")
    assert state.seeds[0]["consecutive_failures"] == 2
    assert state.seeds[0]["status"] == "unknown"
    assert state.seeds[1]["consecutive_failures"] == 0
    state.mark_live_poll_failed("timeout", "192.0.2.1:161")
    assert state.seeds[0]["status"] == "down"
    assert other["status"] == "up"


def test_metrics_do_not_update_an_endpoint_that_precedes_its_seed():
    state = _state()
    seed, endpoint = state.devices
    state.devices = [endpoint, seed]
    state.import_live_metrics({"interfaces": [{"if_index": "1", "in_octets": 1234}]}, seed["seed_key"])
    assert seed["interfaces"][0]["in_octets"] == 1234
    assert "in_octets" not in endpoint["interfaces"][0]


def test_mac_move_uses_fresh_port_and_keeps_identity_layout_and_label():
    state = _state()
    seed, endpoint = deepcopy(state.devices)
    state.import_live_discovery(_inventory(seed, [endpoint], [_link(seed, endpoint, 2)]), seed["seed_key"], record_history=False)
    assert len(state.links) == 1
    assert state.links[0]["local_port"] == "port2"
    assert state.devices[1]["id"] == endpoint["id"]
    assert state.devices[1]["layout"] == endpoint["layout"]
    assert state.devices[1]["name"] == "Custom asset"


def test_mac_access_port_beats_another_switch_uplink():
    state = _state()
    seed, endpoint = deepcopy(state.devices)
    other = _seed("192.0.2.2", "live-b")
    other["interfaces"][0]["if_alias"] = "uplink trunk"
    other["interfaces"][0]["vlan_tagged"] = ["1001", "1003", "1005"]
    state.devices.append(other)
    copy = _endpoint("endpoint-other")
    state.import_live_discovery(_inventory(other, [copy], [_link(other, copy)]), other["seed_key"], record_history=False)
    assert len(state.links) == 1
    assert state.links[0]["from"] == seed["id"]
    assert state.devices[1]["seed_key"] == seed["seed_key"]
    assert state.devices[1]["layout"] == endpoint["layout"]
    assert len(state.links[0]["candidate_observations"]) == 2


def test_fresh_mac_supersedes_same_port_description_but_not_other_port():
    state = _state()
    seed, endpoint = deepcopy(state.devices)
    first = _endpoint("description-1", mac="")
    second = _endpoint("description-2", mac="")
    state.devices.extend([first, second])
    state.links.extend([_link(seed, first), _link(seed, second, 2)])
    state._reconcile_described_endpoints()
    assert first["id"] not in {device["id"] for device in state.devices}
    assert second["id"] in {device["id"] for device in state.devices}
    assert state.mac_labels[ENDPOINT.replace(":", "")]["name"] == "Custom asset"


def test_unnamed_mac_does_not_merge_with_same_named_description_on_other_port():
    state = _state()
    description = _endpoint("portdesc-other", mac="")
    assert state._find_existing_device_id(description, {device["id"]: device for device in state.devices}) is None


def test_orphan_aging_and_failure_counts_survive_server_reload(tmp_path):
    state = _state("down")
    path = tmp_path / "state.json"
    state.persistence_path = path
    state.links = []
    state._prune_unlinked_observed_endpoints("192.0.2.1:161")
    state.mark_live_poll_failed("timeout", "192.0.2.1:161")
    reloaded = NetWatchState(path)
    assert reloaded.devices[1]["missing_inventory_polls"] == 1
    assert reloaded.seeds[0]["consecutive_failures"] == 1
    for _ in range(2):
        reloaded._prune_unlinked_observed_endpoints("192.0.2.1:161")
    assert len(reloaded.devices) == 1
    assert reloaded.mac_labels[ENDPOINT.replace(":", "")]["name"] == "Custom asset"


def test_invalid_extra_fdb_status_rows_are_not_incomplete(discovery_tables):
    tables, _ = discovery_tables
    _add_fdb(tables, [ENDPOINT])
    index = ".".join(str(int(octet, 16)) for octet in "00:11:22:33:44:77".split(":"))
    tables[id(snmp_live.BRIDGE_OIDS)]["fdb_status"][index] = 2
    assert _discover()["topology_collection"]["complete"] is True


def test_description_is_not_matched_to_one_known_mac_on_a_shared_port():
    state = _state()
    seed = state.devices[0]
    description = _endpoint("portdesc-shared", mac="")
    state.links[0]["port_mac_count"] = 3
    state.devices.append(description)
    state.links.append(_link(seed, description))
    state._reconcile_described_endpoints()
    assert description["id"] in {device["id"] for device in state.devices}


def test_database_persists_confirmation_and_retirement_state(tmp_path):
    state = _state()
    from netwatch_light.storage import DatabaseStore

    url = "sqlite:///" + str(tmp_path / "test.db")
    state.store = DatabaseStore(url)
    state.persistence_path = None
    seed = deepcopy(state.devices[0])
    state.mark_live_poll_failed("timeout", seed["seed_key"])
    state.import_live_discovery(_inventory(seed), seed["seed_key"], record_history=False)
    reloaded = NetWatchState(database_url=url, load_history=False)
    assert reloaded.links[0]["needs_confirmation"] is True
    assert reloaded.links[0]["missing_polls"] == 1
    assert reloaded.seeds[0]["consecutive_failures"] == 0
    assert reloaded.mac_labels[ENDPOINT.replace(":", "")]["name"] == "Custom asset"


def test_polling_keeps_known_macs_and_reports_incomplete_topology(monkeypatch):
    from netwatch_light import polling

    state = _state()
    seed = deepcopy(state.devices[0])
    seen_macs = set()

    async def discover(config, labeled_macs=None):
        seen_macs.update(labeled_macs or [])
        return _inventory(seed, complete=False)

    monkeypatch.setattr(polling, "discover_seed", discover)
    result = asyncio.run(polling.poll_live_seeds(state, "test", {seed["seed_key"]: CONFIG}))
    assert ENDPOINT in seen_macs
    assert result["snapshot"]["poll_runs"][0]["status"] == "partial"
    assert len(state.links) == 1
    assert state.links[0]["needs_confirmation"] is True
