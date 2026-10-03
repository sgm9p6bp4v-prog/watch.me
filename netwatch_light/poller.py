from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
from typing import Any

from .polling import load_seed_configs, poll_live_metrics, poll_live_seeds
from .state import NetWatchState
from .storage import default_database_url


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT_DIR / "data" / "netwatch_state.json"
DATABASE_URL = default_database_url(ROOT_DIR)


def _poll_interval_seconds(state: NetWatchState) -> int:
    polling = state.settings.get("polling", {})
    try:
        return max(5, min(3600, int(polling.get("backend_interval_seconds") or 30)))
    except (TypeError, ValueError):
        return 30


def _inventory_interval_seconds(state: NetWatchState) -> int:
    polling = state.settings.get("polling", {})
    try:
        return max(60, min(86400, int(polling.get("inventory_minutes") or 15) * 60))
    except (TypeError, ValueError):
        return 15 * 60


async def _keep_polling_heartbeat(state: NetWatchState, pid: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except TimeoutError:
            state.update_external_poller_status("polling", pid)


async def run_poller() -> None:
    state = NetWatchState(STATE_PATH, DATABASE_URL, load_history=False)
    recovered_runs = state.recover_interrupted_poll_runs()
    seed_configs = load_seed_configs(state)
    next_poll_at = 0.0
    next_inventory_at = time.time() + _inventory_interval_seconds(state)
    last_heartbeat_at = 0.0
    pid = os.getpid()
    print(f"NetWatch poller started pid={pid} storage={state.storage_backend}", flush=True)
    if recovered_runs:
        print(f"Recovered {recovered_runs} interrupted poll run(s)", flush=True)

    try:
        while True:
            state.reload()
            now = time.time()
            interval = _poll_interval_seconds(state)
            inventory_interval = _inventory_interval_seconds(state)
            polling_settings = state.settings.get("polling", {})
            auto_enabled = bool(polling_settings.get("backend_auto_poll"))
            manual_request = state.pending_manual_poll_request()

            if now - last_heartbeat_at >= 10:
                state.update_external_poller_status("running", pid)
                last_heartbeat_at = now

            should_poll = bool(manual_request) or (auto_enabled and now >= next_poll_at)
            if should_poll:
                cycle_started_at = time.time()
                seed_configs = load_seed_configs(state)
                full_inventory = bool(manual_request) or now >= next_inventory_at
                source = (
                    manual_request["source"]
                    if manual_request
                    else "scheduled inventory"
                    if full_inventory
                    else "scheduled metrics"
                )
                state.update_external_poller_status("polling", pid)
                started = time.monotonic()
                result: dict[str, Any] | None = None
                heartbeat_stop = asyncio.Event()
                heartbeat_task = asyncio.create_task(_keep_polling_heartbeat(state, pid, heartbeat_stop))
                try:
                    if full_inventory:
                        result = await poll_live_seeds(state, source, seed_configs)
                    else:
                        result = await poll_live_metrics(state, source, seed_configs)
                finally:
                    heartbeat_stop.set()
                    await heartbeat_task
                    elapsed_ms = round((time.monotonic() - started) * 1000)
                    state.reload()
                    polling = state.settings.setdefault("polling", {})
                    polling["last_poll_duration_ms"] = elapsed_ms
                    polling["last_poll_finished_at"] = time.time()
                    if manual_request:
                        state.complete_manual_poll_request(manual_request["id"], result or {})
                    else:
                        state.persist()
                    state.update_external_poller_status("running", pid)
                    last_heartbeat_at = time.time()
                if full_inventory:
                    next_inventory_at = time.time() + inventory_interval
                    next_poll_at = time.time() + interval
                else:
                    next_poll_at = max(cycle_started_at + interval, time.time() + 1)

            await asyncio.sleep(1)
    except asyncio.CancelledError:
        raise
    finally:
        state.reload()
        state.update_external_poller_status("stopped", pid)
        print("NetWatch poller stopped", flush=True)


def main() -> None:
    try:
        asyncio.run(run_poller())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
