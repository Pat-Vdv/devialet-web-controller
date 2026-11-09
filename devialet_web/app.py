"""FastAPI application: JSON API for the frontend + static files."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import asyncio

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ipcontrol import (
    DEFAULT_TIMEOUT,
    PLAYBACK_ACTIONS,
    TONE_GAIN_MAX,
    TONE_GAIN_MIN,
    ForbiddenCommand,
    IpControlClient,
    IpControlError,
    SpeakerUnreachable,
)
from .discovery import DiscoveryService, KnownSpeakers, choose_system
from .graphic_eq import BANDS_HZ, JUMP_CONFIRM_DB, EqError, EqValueError, GraphicEq, preset_list
from .pair import VOLUME_HARD_CAP, StereoPair
from .security import RequestGuard, SecurityPolicy
from .spectrum import SpectrumHub
from .tone import ToneValueError

STATIC_DIR = Path(__file__).parent / "static"


class VolumeBody(BaseModel):
    volume: int = Field(ge=0, le=VOLUME_HARD_CAP)


class StepBody(BaseModel):
    direction: int = Field(description="+1 or -1")


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, SpeakerUnreachable):
        return HTTPException(503, f"Enceinte injoignable : {exc}")
    if isinstance(exc, IpControlError):
        return HTTPException(502, {"code": exc.code, "message": exc.message})
    if isinstance(exc, ForbiddenCommand):
        return HTTPException(403, f"Commande interdite : {exc}")
    return HTTPException(500, str(exc))


class SelectBody(BaseModel):
    systemId: str


def offline_state(max_volume: int, volume_step: int) -> dict:
    """/api/state shape when no system is selected (nothing discovered yet)."""
    return {"system": {"name": None, "type": None, "error": None}, "speakers": [],
            "leader": None, "leaderDetected": False, "maxVolume": max_volume,
            "volumeStep": volume_step, "volume": None, "volumePending": None,
            "source": {"present": False}, "errors": ["Aucun système sélectionné"],
            "updatedAt": time.time()}


class SpectrumBody(BaseModel):
    enabled: bool


class ToneBody(BaseModel):
    # strict: 2.0, "2" or true are rejected, not coerced
    bass: int | None = Field(default=None, ge=TONE_GAIN_MIN, le=TONE_GAIN_MAX, strict=True)
    treble: int | None = Field(default=None, ge=TONE_GAIN_MIN, le=TONE_GAIN_MAX, strict=True)


Gain = Annotated[float, Field(strict=True, allow_inf_nan=False)]  # 3 or 2.5; not "3", true or NaN


class EqRevision(BaseModel):
    # optional optimistic check: refused with 409 if the file changed since this revision
    revision: str | None = Field(default=None, max_length=64)


class EqGainsBody(EqRevision):
    gains: list[Gain] = Field(min_length=len(BANDS_HZ), max_length=len(BANDS_HZ))
    enabled: bool | None = Field(default=None, strict=True)


class EqBandBody(EqRevision):
    gain: Gain


class EqBandsBody(EqRevision):
    bands: dict[int, Gain] = Field(min_length=1, max_length=len(BANDS_HZ))  # {"1000": -3}


class EqConfirmBody(EqRevision):
    # discrete actions: true accepts a level rise above 6 dB (answered 409 "confirm" otherwise)
    confirm: bool = Field(default=False, strict=True)


class EqResetBody(EqConfirmBody):
    force: bool = Field(default=False, strict=True)


class EqPresetBody(EqConfirmBody):
    preset: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")   # a name only, never a curve or a path


def _eq_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EqValueError):
        return HTTPException(422, {"code": "invalid", "message": str(exc)})
    return HTTPException(exc.status, {"code": exc.code, "message": exc.message, **getattr(exc, "extra", {})})


def offline_eq() -> dict:
    return {"status": "disabled", "available": False,
            "message": "Égaliseur graphique non configuré (option --eapo-config)",
            "bands": list(BANDS_HZ), "enabled": None, "gains": None, "processing": "unverified",
            "preset": None, "custom": None, "presets": preset_list(), "jumpLimitDb": JUMP_CONFIRM_DB}


def offline_tone() -> dict:
    return {"available": False, "systemName": None, "leader": None, "leaderDetected": False,
            "bass": None, "treble": None, "preset": None, "enabled": None,
            "min": TONE_GAIN_MIN, "max": TONE_GAIN_MAX, "step": 1, "consistent": None,
            "speakers": [], "errors": ["Aucun système sélectionné"], "updatedAt": time.time()}


def create_app(hosts: list[str] | None = None, max_volume: int = VOLUME_HARD_CAP,
               volume_step: int = 2, transport: httpx.AsyncBaseTransport | None = None,
               discovery: DiscoveryService | None = None,
               preferred_system: str | None = None,
               spectrum: SpectrumHub | None = None,
               graphic_eq: GraphicEq | None = None,
               security: SecurityPolicy | None = None) -> FastAPI:
    """`transport` lets tests plug an httpx.MockTransport (no network).

    `security` (security.py): local use by default, Host limited to this
    machine and writes requiring the X-Devialet-Request header.

    `graphic_eq` is only given by __main__ (real Equalizer APO folder): the
    default is no equalizer, so tests can never reach Program Files.

    With `hosts`, the pair is fixed at startup (V1 behaviour). Without, the
    discovery service runs at startup and the best system is selected.
    """
    hosts = list(hosts or [])
    if discovery is None:
        # No cache, no native, no subnet: a scan only re-probes the given hosts.
        discovery = DiscoveryService(KnownSpeakers(None), native=False, transport=transport)
    hub = spectrum or SpectrumHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport) as http:
            app.state.http = http
            app.state.pair = None
            app.state.selected_system = None
            hub.start()
            if hosts:
                await use_hosts(hosts)
            else:
                result = await discovery.scan(quick=True)
                preferred = preferred_system or discovery.known.selected_system
                chosen = choose_system(result, preferred)
                if chosen is not None:
                    await use_hosts(chosen.hosts(), chosen.system_id)
            yield
            await hub.stop()  # tells the agent to release the audio device
            if app.state.pair is not None:
                await app.state.pair.volume_throttle.drain()

    app = FastAPI(title="Devialet Phantom II", lifespan=lifespan)
    app.add_middleware(RequestGuard, policy=security or SecurityPolicy())

    async def use_hosts(selected: list[str], system_id: str | None = None) -> StereoPair:
        new = StereoPair([IpControlClient(h, app.state.http) for h in selected],
                         max_volume=max_volume, volume_step=volume_step)
        await new.discover(force=True)  # GET only: never touches the volume
        old, app.state.pair = app.state.pair, new
        app.state.selected_system = system_id or new.system.get("systemId")
        if old is not None:
            await old.volume_throttle.drain()
        return new

    def pair() -> StereoPair:
        if app.state.pair is None:
            raise HTTPException(503, "Aucun système sélectionné : lancez une recherche")
        return app.state.pair

    @app.get("/api/state")
    async def get_state():
        if app.state.pair is None:
            return offline_state(min(max_volume, VOLUME_HARD_CAP), volume_step)
        return await pair().state()

    def discovery_payload() -> dict:
        data = discovery.last.to_dict() if discovery.last else {"systems": []}
        return {**data, "running": discovery.running,
                "selectedSystemId": app.state.selected_system,
                "subnets": [str(n) for n in discovery.subnets], "native": discovery.native}

    @app.get("/api/discovery")
    async def get_discovery():
        return discovery_payload()

    @app.post("/api/discovery/scan")
    async def scan():
        current = [c.host for c in app.state.pair.clients] if app.state.pair else []
        await discovery.scan(extra_hosts=current)
        return discovery_payload()

    @app.post("/api/systems/select")
    async def select_system(body: SelectBody):
        system = discovery.last.find(body.systemId) if discovery.last else None
        if system is None:
            raise HTTPException(404, "Système inconnu : relancez une recherche")
        if not system.available:
            raise HTTPException(409, "Système indisponible")
        await use_hosts(system.hosts(), system.system_id)
        discovery.known.selected_system = system.system_id
        try:
            discovery.known.save()
        except OSError:
            pass  # selection still applies for this session
        return discovery_payload()

    @app.post("/api/discover")
    async def rediscover():
        await pair().discover(force=True)
        return await pair().state()

    @app.post("/api/volume")
    async def set_volume(body: VolumeBody):
        if body.volume > pair().max_volume:
            raise HTTPException(422, f"Volume maximal autorisé : {pair().max_volume}")
        try:
            return {"volume": await pair().set_volume(body.volume)}
        except (SpeakerUnreachable, IpControlError) as exc:
            raise _error(exc)

    @app.post("/api/volume/step")
    async def step_volume(body: StepBody):
        if body.direction not in (1, -1):
            raise HTTPException(422, "direction doit valoir 1 ou -1")
        try:
            return {"volume": await pair().step_volume(body.direction)}
        except (SpeakerUnreachable, IpControlError) as exc:
            raise _error(exc)

    @app.post("/api/playback/{action}")
    async def playback(action: str):
        if action not in PLAYBACK_ACTIONS:
            raise HTTPException(404, f"Action inconnue : {action}")
        try:
            await pair().playback(action)
        except (SpeakerUnreachable, IpControlError, ForbiddenCommand) as exc:
            raise _error(exc)
        return {"ok": True}

    @app.get("/api/bluetooth")
    async def bluetooth_status():
        if app.state.pair is None:
            return {"available": False, "advertising": None, "sourceActive": False,
                    "peerDeviceName": None, "stream": None, "pairingStartedAt": None,
                    "systemName": None, "errors": ["Aucun système sélectionné"]}
        return await pair().bluetooth_status()

    @app.post("/api/bluetooth/pairing")
    async def start_pairing():
        try:
            return await pair().start_pairing()
        except (SpeakerUnreachable, IpControlError, ForbiddenCommand) as exc:
            raise _error(exc)

    # Tone control (V2.4.1) ---------------------------------------------------
    @app.get("/api/tone")
    async def tone_status():
        if app.state.pair is None:
            return offline_tone()
        return await pair().tone.status()

    @app.post("/api/tone")
    async def tone_set(body: ToneBody):
        try:
            return await pair().tone.apply(bass=body.bass, treble=body.treble)
        except ToneValueError as exc:
            raise HTTPException(422, str(exc))
        except (SpeakerUnreachable, IpControlError, ForbiddenCommand) as exc:
            raise _error(exc)

    # Graphic equalizer through Equalizer APO (V2.4.3-B) ------------------------
    def eq() -> GraphicEq:
        if graphic_eq is None:
            raise HTTPException(503, {"code": "disabled", "message": offline_eq()["message"]})
        return graphic_eq

    async def eq_call(coro_fn, *args, **kwargs):
        try:
            return await coro_fn(*args, **kwargs)
        except (EqError, EqValueError) as exc:
            raise _eq_error(exc)

    @app.get("/api/eq")
    async def eq_state():
        return offline_eq() if graphic_eq is None else await graphic_eq.state()

    @app.put("/api/eq")
    async def eq_put(body: EqGainsBody):
        return await eq_call(eq().set_gains, body.gains, enabled=body.enabled, revision=body.revision)

    @app.patch("/api/eq/bands")
    async def eq_bands(body: EqBandsBody):
        return await eq_call(eq().set_bands, body.bands, revision=body.revision)

    @app.patch("/api/eq/bands/{frequency}")
    async def eq_band(frequency: int, body: EqBandBody):
        return await eq_call(eq().set_bands, {frequency: body.gain}, revision=body.revision)

    @app.post("/api/eq/reset")
    async def eq_reset(body: EqResetBody | None = None):
        body = body or EqResetBody()
        return await eq_call(eq().reset, revision=body.revision, force=body.force, confirm=body.confirm)

    @app.post("/api/eq/preset")
    async def eq_preset(body: EqPresetBody):
        return await eq_call(eq().set_preset, body.preset, revision=body.revision, confirm=body.confirm)

    @app.post("/api/eq/enable")
    async def eq_enable(body: EqConfirmBody | None = None):
        body = body or EqConfirmBody()
        return await eq_call(eq().set_enabled, True, revision=body.revision, confirm=body.confirm)

    @app.post("/api/eq/disable")
    async def eq_disable(body: EqConfirmBody | None = None):
        body = body or EqConfirmBody()
        return await eq_call(eq().set_enabled, False, revision=body.revision, confirm=body.confirm)

    @app.get("/api/eq/status")
    async def eq_status():
        spec = hub.snapshot()
        agent = spec["agent"]
        device = agent.get("device") or ""
        out = {"configured": graphic_eq is not None,
               **(await graphic_eq.diagnostics() if graphic_eq else
                  {"processing": {"status": "unverified", "message": offline_eq()["message"]}})}
        out["agent"] = {"connected": agent["connected"] and not spec["agent"]["stale"],
                        "state": agent["state"], "device": agent.get("device")}
        # the agent only sees the output while the analyzer is capturing; the expected output
        # is the one named by the "Device:" line of Equalizer APO's config.txt
        expected = ((out.get("include") or {}).get("device") or "").lower()
        out["device"] = {"status": "available" if agent["state"] == "capturing" and expected
                         and expected in device.lower()
                         else "unknown" if not spec["enabled"] or not agent["connected"] else "unavailable",
                         "name": agent.get("device")}
        return out

    # Spectrum analyzer (V2.3) -------------------------------------------------
    @app.get("/api/spectrum")
    async def spectrum_status():
        return hub.snapshot()

    @app.post("/api/spectrum")
    async def spectrum_enable(body: SpectrumBody):
        return await hub.set_enabled(body.enabled)

    @app.websocket("/api/spectrum/agent")
    async def spectrum_agent(ws: WebSocket):
        if not hub.agent_allowed(ws.client.host if ws.client else None, dict(ws.headers)):
            await ws.close(code=1008)  # policy violation: not a local agent
            return
        await ws.accept()
        token = await hub.agent_connected(ws.send_text, ws.close)
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text") is not None:
                    await hub.agent_message(token, message["text"])
        except WebSocketDisconnect:
            pass
        finally:
            await hub.agent_disconnected(token)

    @app.websocket("/api/spectrum/ws")
    async def spectrum_viewer(ws: WebSocket):
        await ws.accept()
        viewer = await hub.viewer_connected(ws.send_text)
        sender = asyncio.create_task(viewer.run())
        try:
            while True:
                message = await ws.receive()  # browsers send nothing; this detects the close
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await hub.viewer_disconnected(viewer)
            sender.cancel()

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
