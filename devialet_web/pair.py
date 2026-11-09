"""Stereo pair model: leader detection, state aggregation, capped volume."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .ipcontrol import IpControlClient, IpControlError, SpeakerUnreachable
from .tone import ToneController

ROLE_LABELS = {"FrontLeft": "Gauche", "FrontRight": "Droite", "Mono": "Mono"}

# The speaker reports "no source" as an error code rather than an empty body.
NO_SOURCE_CODES = frozenset({"NoCurrentSource"})

# Hard upper bound for this first version, whatever the configuration says.
VOLUME_HARD_CAP = 50


@dataclass
class Speaker:
    host: str
    reachable: bool = False
    error: str | None = None
    device_id: str | None = None
    name: str | None = None
    role: str | None = None
    is_leader: bool = False
    system_id: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    checked_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "host": self.host, "reachable": self.reachable, "error": self.error,
            "deviceId": self.device_id, "name": self.name, "role": self.role,
            "roleLabel": ROLE_LABELS.get(self.role or "", self.role or "?"),
            "isLeader": self.is_leader, "systemId": self.system_id,
            "model": self.model, "serial": self.serial, "firmware": self.firmware,
        }


def parse_source(data: Any) -> dict:
    """Normalise GET /groups/current/sources/current (dict or None)."""
    if not isinstance(data, dict) or not isinstance(data.get("source"), dict):
        return {"present": False}
    src = data["source"]
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "present": True,
        "sourceId": src.get("sourceId"),
        "type": src.get("type"),
        "name": src.get("displayName") or src.get("type") or "Source inconnue",
        "peerDeviceName": data.get("peerDeviceName"),
        "playingState": data.get("playingState"),
        "muteState": data.get("muteState"),
        "availableOperations": sorted(data.get("availableOperations") or []),
        "title": meta.get("title"),
        "artist": meta.get("artist"),
        "album": meta.get("album"),
    }


class VolumeThrottler:
    """Coalesce volume writes: only the latest target is sent, at most once per interval."""

    def __init__(self, send, min_interval: float = 0.15):
        self._send = send
        self._interval = min_interval
        self._pending: int | None = None
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.sent: list[int] = []

    @property
    def pending(self) -> int | None:
        return self._pending

    def submit(self, value: int) -> None:
        self._pending = value
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self._pending is not None:
            value, self._pending = self._pending, None
            try:
                await self._send(value)
                self.sent.append(value)
                self.last_error = None
            except (IpControlError, SpeakerUnreachable) as exc:
                self.last_error = str(exc)
            await asyncio.sleep(self._interval)

    async def drain(self) -> None:
        if self._task is not None:
            await self._task


# Bluetooth (Windows audio output) ----------------------------------------
# Observed on Phantom II DOS 2.19.1: pairing mode lasts about two minutes.
PAIRING_WINDOW_S = 120


def parse_stream(info: Any) -> dict | None:
    """Normalise streamInfo; keys vary with the source (codec only, or rate/depth)."""
    if not isinstance(info, dict) or not info:
        return None
    return {k: info.get(k) for k in ("codec", "samplingRate", "bitDepth", "lossless", "channels")
            if info.get(k) is not None}


@dataclass
class StereoPair:
    clients: list[IpControlClient]
    max_volume: int = VOLUME_HARD_CAP
    volume_step: int = 2
    device_ttl: float = 10.0
    speakers: dict[str, Speaker] = field(default_factory=dict)
    system: dict = field(default_factory=dict)
    system_error: str | None = None
    pairing_started_at: float | None = None

    def __post_init__(self) -> None:
        self.max_volume = max(0, min(self.max_volume, VOLUME_HARD_CAP))
        self.speakers = {c.host: Speaker(c.host) for c in self.clients}
        self._by_host = {c.host: c for c in self.clients}
        self.volume_throttle = VolumeThrottler(self._send_volume)
        self._discover_lock = asyncio.Lock()
        self.tone = ToneController(self)

    # Discovery (GET only) --------------------------------------------------
    async def _probe(self, client: IpControlClient) -> None:
        spk = self.speakers[client.host]
        spk.checked_at = time.monotonic()
        try:
            dev = await client.device()
        except (IpControlError, SpeakerUnreachable) as exc:
            spk.reachable, spk.error, spk.is_leader = False, str(exc), False
            return
        release = dev.get("release") if isinstance(dev.get("release"), dict) else {}
        spk.reachable, spk.error = True, None
        spk.device_id = dev.get("deviceId")
        spk.name = dev.get("deviceName")
        spk.role = dev.get("role")
        spk.is_leader = dev.get("isSystemLeader") is True
        spk.system_id = dev.get("systemId")
        spk.model = dev.get("model")
        spk.serial = dev.get("serial")
        spk.firmware = release.get("version") or release.get("canonicalVersion")

    async def discover(self, force: bool = False) -> None:
        async with self._discover_lock:
            now = time.monotonic()
            stale = [c for c in self.clients
                     if force or now - self.speakers[c.host].checked_at > self.device_ttl]
            if not stale:
                return
            await asyncio.gather(*(self._probe(c) for c in stale))
            leader = self.leader_client()
            if leader is None:
                return
            try:
                self.system = await leader.system()
                self.system_error = None
            except (IpControlError, SpeakerUnreachable) as exc:
                self.system_error = str(exc)

    def leader_client(self) -> IpControlClient | None:
        """The system leader; falls back to any reachable speaker of the pair."""
        reachable = [s for s in self.speakers.values() if s.reachable]
        leader = next((s for s in reachable if s.is_leader), None) or \
            (reachable[0] if reachable else None)
        return self._by_host[leader.host] if leader else None

    # State ---------------------------------------------------------------
    async def state(self) -> dict:
        await self.discover()
        leader = self.leader_client()
        out: dict[str, Any] = {
            "system": {
                "name": self.system.get("systemName"),
                "type": self.system.get("systemType"),
                "error": self.system_error,
            },
            "speakers": [self.speakers[c.host].to_dict() for c in self.clients],
            "leader": leader.host if leader else None,
            "leaderDetected": any(s.is_leader and s.reachable for s in self.speakers.values()),
            "maxVolume": self.max_volume,
            "volumeStep": self.volume_step,
            "volume": None,
            "volumePending": self.volume_throttle.pending,
            "source": {"present": False},
            "errors": [],
            "updatedAt": time.time(),
        }
        if leader is None:
            out["errors"].append("Aucune enceinte joignable")
            return out
        volume, source = await asyncio.gather(self._read_volume(leader),
                                              self._read_source(leader),
                                              return_exceptions=True)
        for result in (volume, source):
            if isinstance(result, Exception):
                out["errors"].append(str(result))
        if not isinstance(volume, Exception):
            out["volume"] = volume
        if not isinstance(source, Exception):
            out["source"] = source
        if self.volume_throttle.last_error:
            out["errors"].append(f"Volume : {self.volume_throttle.last_error}")
        return out

    async def _read_volume(self, client: IpControlClient) -> int | None:
        return await client.volume()

    async def _read_source(self, client: IpControlClient) -> dict:
        try:
            return parse_source(await client.active_source())
        except IpControlError as exc:
            if exc.code in NO_SOURCE_CODES:
                return {"present": False, "reason": exc.code}
            raise

    # Commands --------------------------------------------------------------
    def _require_leader(self) -> IpControlClient:
        leader = self.leader_client()
        if leader is None:
            raise SpeakerUnreachable("aucune enceinte joignable")
        return leader

    async def _send_volume(self, value: int) -> None:
        await self._require_leader().set_volume(value)

    def clamp_volume(self, value: int) -> int:
        return max(0, min(int(value), self.max_volume))

    async def set_volume(self, value: int) -> int:
        """Queue a capped absolute volume; returns the value that will be sent."""
        await self.discover()
        self._require_leader()
        target = self.clamp_volume(value)
        self.volume_throttle.submit(target)
        return target

    async def step_volume(self, direction: int) -> int:
        """+/- one step from the current (or pending) value, never above the cap.

        Implemented as an absolute set rather than volumeUp/volumeDown so the
        cap is guaranteed whatever step size the firmware uses.
        """
        await self.discover()
        leader = self._require_leader()
        base = self.volume_throttle.pending
        if base is None:
            base = await leader.volume()
            if base is None:
                raise IpControlError("NoVolume", "volume illisible")
        if direction > 0 and base >= self.max_volume:
            return base  # already at or above the cap: never raise it further
        return await self.set_volume(base + direction * self.volume_step)

    async def playback(self, action: str) -> None:
        await self.discover()
        await self._require_leader().playback(action)

    # Bluetooth (Windows audio output) -------------------------------------
    async def bluetooth_status(self) -> dict:
        """Read-only: pairing mode, connected Bluetooth device and stream quality."""
        await self.discover()
        leader = self.leader_client()
        out: dict[str, Any] = {
            "available": leader is not None,
            "systemName": self.system.get("systemName"),
            "advertising": None,
            "pairingStartedAt": self.pairing_started_at,
            "pairingWindowS": PAIRING_WINDOW_S,
            "sourceActive": False,
            "peerDeviceName": None,
            "stream": None,
            "errors": [],
        }
        if leader is None:
            out["errors"].append("Aucune enceinte joignable")
            return out
        bt, src = await asyncio.gather(leader.bluetooth(), leader.active_source(),
                                       return_exceptions=True)
        if isinstance(bt, Exception):
            out["errors"].append(f"Bluetooth : {bt}")
        elif isinstance(bt, dict) and bt.get("advertising") in ("on", "off"):
            out["advertising"] = bt["advertising"] == "on"
        if isinstance(src, IpControlError) and src.code in NO_SOURCE_CODES:
            src = None
        if isinstance(src, Exception):
            out["errors"].append(f"Source : {src}")
        elif isinstance(src, dict) and isinstance(src.get("source"), dict) \
                and src["source"].get("type") == "bluetooth":
            out["sourceActive"] = True
            out["peerDeviceName"] = src.get("peerDeviceName") or None
            out["stream"] = parse_stream(src.get("streamInfo"))
        if out["advertising"] is False:
            self.pairing_started_at = out["pairingStartedAt"] = None
        return out

    async def start_pairing(self) -> dict:
        """Make the pair discoverable (no pairing, no stereo change). Idempotent."""
        await self.discover()
        leader = self._require_leader()
        current = await leader.bluetooth()
        if current.get("advertising") != "on":
            await leader.start_advertising()
            self.pairing_started_at = time.time()
        return await self.bluetooth_status()
