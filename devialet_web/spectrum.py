"""Spectrum analyzer relay between the Windows agent and the browsers.

The Windows agent (agent/windows) captures the Bluetooth output in WASAPI
loopback and sends only 2 x 10 band levels in dBFS; no audio sample ever
reaches this process. This hub:

* accepts one agent at a time, from the loopback interface only;
* asks the agent to capture only while the analyzer is enabled AND at least
  one browser is watching (capture stops otherwise);
* relays level frames to browsers, latest value wins, at most `max_rate`/s;
* reports availability (agent missing, frozen, device absent, disabled).
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
FLOOR_DB, CEIL_DB = -90.0, 6.0
MAX_MESSAGE_BYTES = 4096

Send = Callable[[str], Awaitable[None]]


def parse_levels(values: Any) -> list[float] | None:
    """Validate one channel: exactly 10 finite numbers, clamped to [FLOOR_DB, CEIL_DB]."""
    if not isinstance(values, list) or len(values) != len(BANDS):
        return None
    out = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        out.append(round(min(CEIL_DB, max(FLOOR_DB, float(v))), 1))
    return out


@dataclass
class AgentState:
    connected: bool = False
    version: str | None = None
    state: str = "disconnected"
    device: str | None = None
    is_default: bool | None = None
    message: str = ""
    format: str = ""
    discontinuities: int = 0
    last_seen: float = 0.0
    last_levels_at: float = 0.0


class Viewer:
    """One browser: a latest-value slot drained by its own sender task."""

    def __init__(self, send: Send, max_rate: float):
        self.send = send
        self.min_interval = 1.0 / max_rate
        self.status: str | None = None
        self.levels: str | None = None
        self.wake = asyncio.Event()
        self.sent_levels = 0
        self.closed = False

    def push_status(self, text: str) -> None:
        self.status = text
        self.wake.set()

    def push_levels(self, text: str) -> None:
        self.levels = text  # overwrite: a slow browser never builds a backlog
        self.wake.set()

    async def run(self) -> None:
        last_levels = 0.0
        while not self.closed:
            await self.wake.wait()
            self.wake.clear()
            if self.status is not None:
                text, self.status = self.status, None
                await self.send(text)
            if self.levels is not None:
                wait = self.min_interval - (time.monotonic() - last_levels)
                if wait > 0:
                    await asyncio.sleep(wait)
                if self.levels is not None:
                    text, self.levels = self.levels, None
                    await self.send(text)
                    self.sent_levels += 1
                    last_levels = time.monotonic()


@dataclass
class SpectrumHub:
    stale_after: float = 5.0          # agent heartbeat is every 2 s
    max_rate: float = 30.0            # level frames per second per browser
    agent_hosts: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})
    enabled: bool = False
    agent: AgentState = field(default_factory=AgentState)
    viewers: set[Viewer] = field(default_factory=set)
    _agent_send: Send | None = None
    _agent_close: Callable[[], Awaitable[None]] | None = None
    _agent_token: object | None = None
    _capture_sent: bool | None = None
    _watchdog: asyncio.Task | None = None
    _last_snapshot: str | None = None

    # -- availability --------------------------------------------------------
    @property
    def stale(self) -> bool:
        return self.agent.connected and time.monotonic() - self.agent.last_seen > self.stale_after

    @property
    def capture_wanted(self) -> bool:
        return self.enabled and bool(self.viewers) and self.agent.connected and not self.stale

    def snapshot(self) -> dict:
        a = self.agent
        if not self.enabled:
            available, reason = False, "Analyseur désactivé"
        elif not a.connected:
            available, reason = False, "Agent Windows non connecté"
        elif self.stale:
            available, reason = False, "Agent Windows sans signe de vie"
        elif a.state == "no_device":
            available, reason = False, f"Sortie audio introuvable : {a.message}"
        elif a.state == "error":
            available, reason = False, f"Erreur de capture : {a.message}"
        elif a.state != "capturing":
            available, reason = False, "Capture en cours de démarrage"
        else:
            available, reason = True, ""
        return {
            "type": "status", "enabled": self.enabled, "available": available, "reason": reason,
            "bands": list(BANDS), "floorDb": FLOOR_DB, "viewers": len(self.viewers),
            "agent": {"connected": a.connected, "stale": self.stale, "version": a.version,
                      "state": a.state, "device": a.device, "isDefault": a.is_default,
                      "message": a.message, "format": a.format,
                      "discontinuities": a.discontinuities,
                      "mono": "mono" in (a.format or "")},
        }

    async def _broadcast_status(self, force: bool = False) -> None:
        """Push the status to browsers when it changed (the viewer count alone does not count)."""
        snap = self.snapshot()
        key = json.dumps({k: v for k, v in snap.items() if k != "viewers"}, sort_keys=True)
        if not force and key == self._last_snapshot:
            return
        self._last_snapshot = key
        text = json.dumps(snap, ensure_ascii=False)
        for viewer in list(self.viewers):
            viewer.push_status(text)

    async def _update_capture(self) -> None:
        wanted = self.capture_wanted
        if self._agent_send is not None and wanted != self._capture_sent:
            self._capture_sent = wanted
            try:
                await self._agent_send(json.dumps({"type": "control", "capture": wanted}))
            except Exception:  # noqa: BLE001 - agent vanished; its session cleanup follows
                self._capture_sent = None

    async def refresh(self) -> None:
        await self._update_capture()
        await self._broadcast_status()

    # -- control -------------------------------------------------------------
    async def set_enabled(self, enabled: bool) -> dict:
        self.enabled = bool(enabled)
        await self.refresh()
        return self.snapshot()

    # -- agent side ----------------------------------------------------------
    def agent_allowed(self, host: str | None, headers: dict[str, str]) -> bool:
        """Only a local, non-browser client may act as the agent."""
        return host in self.agent_hosts and "origin" not in {k.lower() for k in headers}

    async def agent_connected(self, send: Send,
                              close: Callable[[], Awaitable[None]] | None = None) -> object:
        """Register a new agent session; an existing one is replaced and closed."""
        previous_close = self._agent_close
        token = object()
        self._agent_token, self._agent_send, self._capture_sent = token, send, None
        self._agent_close = close
        if previous_close is not None:
            try:
                await previous_close()
            except Exception:  # noqa: BLE001 - already gone
                pass
        self.agent = AgentState(connected=True, state="connected", last_seen=time.monotonic())
        await self.refresh()
        return token

    async def agent_disconnected(self, token: object) -> None:
        if token is not self._agent_token:
            return  # an older session replaced by a newer agent
        self._agent_token = self._agent_send = self._capture_sent = self._agent_close = None
        self.agent = AgentState()
        await self._broadcast_status()

    async def agent_message(self, token: object, text: str) -> bool:
        """Handle one agent message; returns False if it was rejected."""
        if token is not self._agent_token or len(text) > MAX_MESSAGE_BYTES:
            return False
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return False
        if not isinstance(msg, dict):
            return False
        a = self.agent
        a.last_seen = time.monotonic()
        kind = msg.get("type")
        if kind == "hello":
            a.version = str(msg.get("version") or "")
        elif kind == "status":
            a.state = str(msg.get("state") or "unknown")
            a.device = msg.get("device") if isinstance(msg.get("device"), str) else None
            a.is_default = msg.get("isDefault") if isinstance(msg.get("isDefault"), bool) else None
            a.message = str(msg.get("message") or "")
            a.format = str(msg.get("format") or "")
            if isinstance(msg.get("discontinuities"), int):
                a.discontinuities = msg["discontinuities"]
        elif kind == "levels":
            left, right = parse_levels(msg.get("l")), parse_levels(msg.get("r"))
            if left is None or right is None:
                return False
            a.last_levels_at = time.monotonic()
            frame = json.dumps({"type": "levels", "seq": msg.get("seq") if isinstance(msg.get("seq"), int) else 0,
                                "l": left, "r": right, "silent": msg.get("silent") is True})
            for viewer in list(self.viewers):
                viewer.push_levels(frame)
            return True
        else:
            return False
        await self.refresh()
        return True

    # -- browser side --------------------------------------------------------
    async def viewer_connected(self, send: Send) -> Viewer:
        viewer = Viewer(send, self.max_rate)
        self.viewers.add(viewer)
        viewer.push_status(json.dumps(self.snapshot(), ensure_ascii=False))
        await self._update_capture()
        await self._broadcast_status(force=True)
        return viewer

    async def viewer_disconnected(self, viewer: Viewer) -> None:
        viewer.closed = True
        viewer.wake.set()
        self.viewers.discard(viewer)
        await self.refresh()

    # -- lifecycle -------------------------------------------------------------
    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            await self.refresh()  # turns a frozen agent into "sans signe de vie"

    def start(self) -> None:
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._agent_send is not None:
            try:  # clean shutdown: the agent releases the audio device
                await self._agent_send(json.dumps({"type": "control", "capture": False}))
            except Exception:  # noqa: BLE001
                pass
        for viewer in list(self.viewers):
            viewer.closed = True
            viewer.wake.set()
