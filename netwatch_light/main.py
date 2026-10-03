from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .polling import (
    load_seed_configs,
    poll_live_seeds,
    seed_credentials_record,
    seed_key,
    seed_metadata,
)
from .smc import SmcSourceConfig, discover_smc
from .snmp_live import SnmpSeedConfig, discover_seed
from .state import NetWatchState
from .storage import default_database_url


ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
STATE_PATH = ROOT_DIR / "data" / "netwatch_state.json"
DATABASE_URL = default_database_url(ROOT_DIR)

app = FastAPI(title="NetWatch Light", version="0.1.0")
state = NetWatchState(STATE_PATH, DATABASE_URL)
subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
seed_configs: dict[str, SnmpSeedConfig] = {}
poll_lock = asyncio.Lock()


class SnmpSeedRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=161, ge=1, le=65535)
    version: str = Field(default="2c", pattern="^(2c|3)$")
    community: str = ""
    username: str = ""
    auth_key: str = ""
    priv_key: str = ""
    auth_protocol: str = "SHA"
    priv_protocol: str = "AES"


class SmcSourceRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=80, ge=1, le=65535)
    scheme: str = Field(default="http", pattern="^(http|https)$")


class PollingRequest(BaseModel):
    enabled: bool
    interval_seconds: int = Field(default=30, ge=5, le=3600)


class TopologyLayoutPoint(BaseModel):
    x: float
    y: float
    locked: bool = False


class TopologyLayoutRequest(BaseModel):
    layouts: dict[str, TopologyLayoutPoint]


class MacLabelRequest(BaseModel):
    mac: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=400)


class DeviceLabelRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=400)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def publish(message: dict[str, Any]) -> None:
    stale: list[asyncio.Queue[dict[str, Any]]] = []
    for queue in subscribers:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            stale.append(queue)
    for queue in stale:
        subscribers.discard(queue)


def load_persisted_seed_configs() -> int:
    return len(load_seed_configs(state, seed_configs))


@app.on_event("startup")
async def startup() -> None:
    state.reload()
    load_persisted_seed_configs()


@app.on_event("shutdown")
async def shutdown() -> None:
    return None


@app.get("/api/health")
async def health() -> dict[str, Any]:
    state.reload()
    return {"status": "ok", "service": "netwatch-light", "storage": state.storage_backend}


@app.get("/api/snapshot")
async def snapshot(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    state.reload()
    data = state.snapshot()
    data["runtime"] = {
        "seed_credentials_loaded": len(seed_configs),
        "seed_credentials_saved": len(state.seed_credentials),
        "smc_sources_saved": len(state.smc_sources),
        "storage_backend": state.storage_backend,
        "external_poller_alive": state.is_external_poller_alive(),
        "last_poll_run": state.poll_runs[0] if state.poll_runs else None,
    }
    return data


@app.get("/api/poll-runs")
async def poll_runs(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    state.reload()
    return {"poll_runs": state.poll_runs}


@app.get("/api/devices/{device_id}/history")
async def device_history(device_id: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    state.reload(include_history=True)
    result = state.get_device_history(device_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return result


@app.post("/api/poll")
async def run_poll() -> dict[str, Any]:
    state.reload()
    if state.mode == "live":
        if state.is_external_poller_alive():
            result = state.request_manual_poll("manual poll")
            await publish({"type": "poll.queued", "event": result["event"]})
            return result
        async with poll_lock:
            return await poll_live_seeds(state, "manual poll fallback", seed_configs, publish)
    result = state.run_poll()
    await publish({"type": "poll.completed", "event": result["event"]})
    return result


@app.post("/api/discovery")
async def run_discovery() -> dict[str, Any]:
    state.reload()
    if state.mode == "live":
        if state.is_external_poller_alive():
            result = state.request_manual_poll("LLDP discovery")
            await publish({"type": "poll.queued", "event": result["event"]})
            return result
        async with poll_lock:
            return await poll_live_seeds(state, "LLDP discovery fallback", seed_configs, publish)
    result = state.run_discovery()
    await publish({"type": "discovery.completed", "event": result["event"]})
    return result


@app.post("/api/polling")
async def update_polling(payload: PollingRequest) -> dict[str, Any]:
    state.reload()
    result = state.set_backend_polling(payload.enabled, payload.interval_seconds)
    await publish({"type": "polling.updated", "event": result["event"]})
    return result


@app.post("/api/topology/layout")
async def save_topology_layout(payload: TopologyLayoutRequest) -> dict[str, Any]:
    state.reload()
    result = state.update_device_layouts(
        {device_id: point.model_dump() for device_id, point in payload.layouts.items()}
    )
    await publish({"type": "topology.layout.saved", "event": result["event"]})
    return result


@app.post("/api/mac-labels")
async def save_mac_label(payload: MacLabelRequest) -> dict[str, Any]:
    state.reload()
    try:
        result = state.update_mac_label(payload.mac, payload.name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await publish({"type": "mac.label.saved", "event": result["event"]})
    return result


@app.post("/api/device-labels")
async def save_device_label(payload: DeviceLabelRequest) -> dict[str, Any]:
    state.reload()
    try:
        result = state.update_device_label(payload.device_id, payload.name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await publish({"type": "device.label.saved", "event": result["event"]})
    return result


@app.post("/api/live/clear")
async def clear_live_inventory() -> dict[str, Any]:
    state.reload()
    seed_configs.clear()
    result = state.clear_live_inventory()
    await publish({"type": "live.cleared", "event": result["event"]})
    return result


@app.post("/api/live/seed")
async def add_live_seed(seed: SnmpSeedRequest) -> dict[str, Any]:
    state.reload()
    if seed.version == "2c" and not seed.community:
        raise HTTPException(status_code=400, detail="SNMPv2c community is required")
    if seed.version == "3" and not seed.username:
        raise HTTPException(status_code=400, detail="SNMPv3 username is required")
    config = SnmpSeedConfig(
        host=seed.host.strip(),
        port=seed.port,
        version=seed.version,
        community=seed.community,
        username=seed.username,
        auth_key=seed.auth_key,
        priv_key=seed.priv_key,
        auth_protocol=seed.auth_protocol,
        priv_protocol=seed.priv_protocol,
    )
    try:
        discovery = await discover_seed(config, labeled_macs=set(state.mac_labels))
    except Exception as exc:
        event = state.add_event(f"Live seed failed for {seed.host}: {exc}")
        await publish({"type": "live.seed.failed", "event": event})
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    key = seed_key(config)
    seed_configs[key] = config
    state.register_seed_credentials(seed_credentials_record(config))
    state.register_live_seed(seed_metadata(config, discovery))
    result = state.import_live_discovery(discovery, key)
    await publish({"type": "live.seed.imported", "event": result["event"]})
    return {
        "seed": state.seeds,
        "system": discovery["system"],
        "counts": discovery["counts"],
        "snapshot": result["snapshot"],
        "event": result["event"],
    }


@app.post("/api/live/smc")
async def add_smc_source(source: SmcSourceRequest) -> dict[str, Any]:
    state.reload()
    try:
        config = SmcSourceConfig(
            host=source.host.strip(),
            port=source.port,
            scheme=source.scheme,
        )
        discovery = await discover_smc(config)
    except Exception as exc:
        event = state.add_event(f"SMC host failed for {source.host}: {exc}")
        await publish({"type": "live.smc.failed", "event": event})
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state.register_smc_source(discovery["source"])
    result = state.import_smc_discovery(discovery, config.key)
    await publish({"type": "live.smc.imported", "event": result["event"]})
    return {
        "source": discovery["source"],
        "machine": discovery["machine"],
        "counts": discovery["counts"],
        "snapshot": result["snapshot"],
        "event": result["event"],
    }


@app.post("/api/alerts/{alert_id}/ack")
async def acknowledge_alert(alert_id: str) -> dict[str, Any]:
    state.reload()
    result = state.update_alert(alert_id, "ack")
    if result is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    await publish({"type": "alert.updated", "event": result["event"], "alert": result["alert"]})
    return result


@app.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str) -> dict[str, Any]:
    state.reload()
    result = state.update_alert(alert_id, "resolve")
    if result is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    await publish({"type": "alert.updated", "event": result["event"], "alert": result["alert"]})
    return result


@app.websocket("/ws/events")
async def events_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=20)
    subscribers.add(queue)
    await websocket.send_json({"type": "connected", "event": {"text": "WebSocket event stream connected"}})
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        subscribers.discard(queue)


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
