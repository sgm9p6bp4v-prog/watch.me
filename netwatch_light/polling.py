from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .smc import SmcSourceConfig, discover_smc, smc_config_from_record
from .snmp_live import SnmpSeedConfig, discover_seed, discover_seed_metrics
from .state import NetWatchState

PublishCallback = Callable[[dict[str, Any]], Awaitable[None]]
SOURCE_DISCOVERY_CONCURRENCY = 4
METRIC_POLL_CONCURRENCY = 4


def seed_key(config: SnmpSeedConfig) -> str:
    return f"{config.host}:{config.port}"


def seed_metadata(config: SnmpSeedConfig, discovery: dict[str, Any], status: str = "up") -> dict[str, Any]:
    key = seed_key(config)
    system = discovery.get("system", {})
    return {
        "key": key,
        "host": config.host,
        "port": config.port,
        "version": config.version,
        "sys_name": system.get("sys_name") or config.host,
        "sys_object_id": system.get("sys_object_id") or "unknown",
        "status": status,
        "last_error": "",
        "last_counts": discovery.get("counts", {}),
        "topology_collection": discovery.get("topology_collection", {"complete": True}),
    }


def seed_credentials_record(config: SnmpSeedConfig) -> dict[str, Any]:
    return {
        "key": seed_key(config),
        "host": config.host,
        "port": config.port,
        "version": config.version,
        "community": config.community,
        "username": config.username,
        "auth_key": config.auth_key,
        "priv_key": config.priv_key,
        "auth_protocol": config.auth_protocol,
        "priv_protocol": config.priv_protocol,
    }


def seed_config_from_record(record: dict[str, Any]) -> SnmpSeedConfig:
    host = str(record.get("host") or "").strip()
    if not host:
        raise ValueError("seed credential record is missing host")
    return SnmpSeedConfig(
        host=host,
        port=int(record.get("port") or 161),
        version=str(record.get("version") or "2c"),
        community=str(record.get("community") or ""),
        username=str(record.get("username") or ""),
        auth_key=str(record.get("auth_key") or ""),
        priv_key=str(record.get("priv_key") or ""),
        auth_protocol=str(record.get("auth_protocol") or "SHA"),
        priv_protocol=str(record.get("priv_protocol") or "AES"),
    )


def load_seed_configs(
    state: NetWatchState,
    target: dict[str, SnmpSeedConfig] | None = None,
) -> dict[str, SnmpSeedConfig]:
    loaded: dict[str, SnmpSeedConfig] = {}
    for record in state.seed_credentials:
        try:
            config = seed_config_from_record(record)
        except (TypeError, ValueError):
            continue
        loaded[seed_key(config)] = config
    if target is not None:
        target.clear()
        target.update(loaded)
    return loaded


def load_smc_configs(state: NetWatchState) -> dict[str, SmcSourceConfig]:
    loaded: dict[str, SmcSourceConfig] = {}
    for record in state.smc_sources:
        try:
            config = smc_config_from_record(record)
        except (TypeError, ValueError):
            continue
        loaded[config.key] = config
    return loaded


async def poll_live_seeds(
    state: NetWatchState,
    source: str,
    seed_configs: dict[str, SnmpSeedConfig] | None = None,
    publish: PublishCallback | None = None,
) -> dict[str, Any]:
    state.reload()
    configs = seed_configs if seed_configs is not None else load_seed_configs(state)
    if seed_configs is not None and not configs and state.seed_credentials:
        configs = load_seed_configs(state, seed_configs)
    smc_configs = load_smc_configs(state)

    run_id = state.start_poll_run(source, seed_count=len(configs) + len(smc_configs))
    if not configs and not smc_configs:
        try:
            result = state.run_poll()
            if publish:
                await publish({"type": "poll.skipped", "event": result["event"]})
            state.reload()
            state.finish_poll_run(run_id, "skipped", successes=0, failures=0)
            return result
        except Exception as exc:
            state.reload()
            state.finish_poll_run(run_id, "failed", successes=0, failures=0, error=str(exc))
            raise

    successes = 0
    failures = 0
    incomplete_topologies = 0
    last_result: dict[str, Any] | None = None
    try:
        semaphore = asyncio.Semaphore(SOURCE_DISCOVERY_CONCURRENCY)

        async def discover_snmp(key: str, config: SnmpSeedConfig) -> dict[str, Any]:
            async with semaphore:
                try:
                    known_macs = set(state.mac_labels)
                    known_macs.update(
                        str(device.get("mac")) for device in state.devices
                        if device.get("device_type") == "endpoint" and device.get("mac")
                    )
                    discovery = await discover_seed(config, labeled_macs=known_macs)
                    return {"kind": "snmp", "key": key, "config": config, "discovery": discovery}
                except Exception as exc:
                    return {"kind": "snmp", "key": key, "config": config, "error": exc}

        async def discover_smc_source(key: str, config: SmcSourceConfig) -> dict[str, Any]:
            async with semaphore:
                try:
                    discovery = await discover_smc(config)
                    return {"kind": "smc", "key": key, "config": config, "discovery": discovery}
                except Exception as exc:
                    return {"kind": "smc", "key": key, "config": config, "error": exc}

        discoveries = await asyncio.gather(
            *(discover_snmp(key, config) for key, config in list(configs.items())),
            *(discover_smc_source(key, config) for key, config in list(smc_configs.items())),
        )

        state.reload()
        with state.deferred_persistence():
            for item in discoveries:
                key = item["key"]
                error = item.get("error")
                if item["kind"] == "snmp":
                    config = item["config"]
                    if error is not None:
                        failures += 1
                        last_result = state.mark_live_poll_failed(str(error), key)
                        if publish:
                            await publish({"type": "poll.failed", "event": last_result["event"], "seed": key})
                        continue
                    discovery = item["discovery"]
                    successes += 1
                    if discovery.get("topology_collection", {}).get("complete") is False:
                        incomplete_topologies += 1
                    state.register_live_seed(seed_metadata(config, discovery))
                    last_result = state.import_live_discovery(discovery, key, record_history=False)
                    if publish:
                        await publish({"type": "poll.completed", "event": last_result["event"], "seed": key})
                    continue

                if error is not None:
                    failures += 1
                    last_result = state.mark_smc_poll_failed(key, str(error))
                    if publish:
                        await publish({"type": "poll.failed", "event": last_result["event"], "smc": key})
                    continue
                discovery = item["discovery"]
                successes += 1
                state.register_smc_source(discovery["source"])
                last_result = state.import_smc_discovery(discovery, key, record_history=False)
                if publish:
                    await publish({"type": "poll.completed", "event": last_result["event"], "smc": key})

            state.record_interface_history()
            if len(configs) + len(smc_configs) > 1:
                event = state.add_event(
                    f"Live {source} finished: {successes} source(s) ok, {failures} failed, "
                    f"{incomplete_topologies} topology collection(s) incomplete"
                )
                last_result = {"event": event, "snapshot": state.snapshot(), "successes": successes, "failures": failures}

            status = "succeeded" if failures == 0 and successes > 0 and incomplete_topologies == 0 else "partial" if successes > 0 else "failed"
            state.finish_poll_run(
                run_id, status, successes=successes, failures=failures,
                error=f"{incomplete_topologies} incomplete topology collection(s)" if incomplete_topologies else "",
            )
        if last_result is not None:
            last_result["snapshot"] = state.snapshot()
        return last_result or {
            "event": state.add_event("Live poll skipped: no seeds configured"),
            "snapshot": state.snapshot(),
            "successes": successes,
            "failures": failures,
        }
    except Exception as exc:
        state.reload()
        state.finish_poll_run(run_id, "failed", successes=successes, failures=failures, error=str(exc))
        raise


async def poll_live_metrics(
    state: NetWatchState,
    source: str,
    seed_configs: dict[str, SnmpSeedConfig] | None = None,
    publish: PublishCallback | None = None,
) -> dict[str, Any]:
    state.reload()
    configs = seed_configs if seed_configs is not None else load_seed_configs(state)
    if seed_configs is not None and not configs and state.seed_credentials:
        configs = load_seed_configs(state, seed_configs)
    smc_configs = load_smc_configs(state)
    source_count = len(configs) + len(smc_configs)
    run_id = state.start_poll_run(source, seed_count=source_count)
    if source_count == 0:
        state.finish_poll_run(run_id, "skipped", successes=0, failures=0)
        return {"event": None, "snapshot": state.snapshot(), "successes": 0, "failures": 0}

    semaphore = asyncio.Semaphore(METRIC_POLL_CONCURRENCY)

    async def read_snmp(key: str, config: SnmpSeedConfig) -> dict[str, Any]:
        async with semaphore:
            try:
                return {"kind": "snmp", "key": key, "metrics": await discover_seed_metrics(config)}
            except Exception as exc:
                return {"kind": "snmp", "key": key, "error": exc}

    async def read_smc(key: str, config: SmcSourceConfig) -> dict[str, Any]:
        async with semaphore:
            try:
                return {"kind": "smc", "key": key, "discovery": await discover_smc(config)}
            except Exception as exc:
                return {"kind": "smc", "key": key, "error": exc}

    successes = 0
    failures = 0
    try:
        readings = await asyncio.gather(
            *(read_snmp(key, config) for key, config in list(configs.items())),
            *(read_smc(key, config) for key, config in list(smc_configs.items())),
        )
        state.reload()
        with state.deferred_persistence():
            for item in readings:
                key = item["key"]
                error = item.get("error")
                if error is not None:
                    failures += 1
                    if item["kind"] == "snmp":
                        result = state.mark_live_poll_failed(str(error), key)
                    else:
                        result = state.mark_smc_poll_failed(key, str(error))
                    if publish:
                        await publish({"type": "poll.failed", "event": result["event"], "source": key})
                    continue

                try:
                    if item["kind"] == "snmp":
                        result = state.import_live_metrics(item["metrics"], key)
                    else:
                        discovery = item["discovery"]
                        state.register_smc_source(discovery["source"])
                        result = state.import_smc_discovery(
                            discovery,
                            key,
                            record_event=False,
                            record_history=False,
                        )
                except Exception as exc:
                    failures += 1
                    if item["kind"] == "snmp":
                        result = state.mark_live_poll_failed(str(exc), key)
                    else:
                        result = state.mark_smc_poll_failed(key, str(exc))
                    if publish:
                        await publish({"type": "poll.failed", "event": result["event"], "source": key})
                    continue
                successes += 1
                if publish and result.get("event"):
                    await publish({"type": "poll.completed", "event": result["event"], "source": key})

            state.record_interface_history()
            status = "succeeded" if failures == 0 and successes > 0 else "partial" if successes > 0 else "failed"
            state.finish_poll_run(run_id, status, successes=successes, failures=failures)
        return {
            "event": None,
            "snapshot": state.snapshot(),
            "successes": successes,
            "failures": failures,
        }
    except Exception as exc:
        state.reload()
        state.finish_poll_run(run_id, "failed", successes=successes, failures=failures, error=str(exc))
        raise
