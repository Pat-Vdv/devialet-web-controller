"""Speaker discovery: known addresses, native (mDNS / UDP) and allowed subnets.

Every candidate address is confirmed with GET /ipcontrol/v1/devices/current,
as the speakers expect. Only GET requests are sent.

Strategies, in order:
1. known   - addresses remembered from previous scans (JSON cache file);
2. native  - mDNS (_whatsup/_http/_devialet-http) and Devialet UDP broadcast
             discovery (24243 listen, 24244 solicitation). These rely on
             multicast/broadcast and do NOT cross routers or the WSL2 NAT;
3. subnets - HTTP probe of every host of explicitly configured private
             subnets, with bounded size, concurrency and timeouts.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable

import httpx

from .ipcontrol import IpControlClient, IpControlError, SpeakerUnreachable

PROBE_TIMEOUT = httpx.Timeout(2.0, connect=0.8)
MAX_SUBNET_HOSTS = 1024          # /22 at most per configured subnet
DEFAULT_CONCURRENCY = 32
MAX_CONCURRENCY = 64

NativeFinder = Callable[[], Awaitable[set[str]]]


class SubnetNotAllowed(ValueError):
    pass


def parse_subnets(values: Iterable[str]) -> list[ipaddress.IPv4Network]:
    """Validate configured subnets: private IPv4 only, at most MAX_SUBNET_HOSTS hosts."""
    nets = []
    for raw in values:
        raw = raw.strip()
        if not raw:
            continue
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            raise SubnetNotAllowed(f"{raw}: {exc}") from exc
        if net.version != 4 or not net.is_private or net.is_loopback:
            raise SubnetNotAllowed(f"{raw}: seuls les sous-réseaux IPv4 privés sont autorisés")
        if net.num_addresses > MAX_SUBNET_HOSTS:
            raise SubnetNotAllowed(f"{raw}: trop large (max {MAX_SUBNET_HOSTS} adresses, soit /22)")
        nets.append(net)
    return nets


@dataclass
class DiscoveredSpeaker:
    device_id: str
    host: str
    name: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    role: str | None = None
    is_leader: bool = False
    system_id: str | None = None
    reachable: bool = True
    last_seen: float = 0.0
    found_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"deviceId": self.device_id, "host": self.host, "name": self.name,
                "model": self.model, "serial": self.serial, "firmware": self.firmware,
                "role": self.role, "isLeader": self.is_leader, "systemId": self.system_id,
                "reachable": self.reachable, "lastSeen": self.last_seen,
                "foundBy": sorted(set(self.found_by))}


def speaker_from_device(host: str, dev: dict, strategy: str) -> DiscoveredSpeaker | None:
    """Build a speaker from GET /devices/current; None if it is not an IP Control device."""
    if not isinstance(dev, dict) or not dev.get("deviceId"):
        return None
    release = dev.get("release") if isinstance(dev.get("release"), dict) else {}
    return DiscoveredSpeaker(
        device_id=str(dev["deviceId"]), host=host, name=dev.get("deviceName"),
        model=dev.get("model"), serial=dev.get("serial"),
        firmware=release.get("version") or release.get("canonicalVersion"),
        role=dev.get("role"), is_leader=dev.get("isSystemLeader") is True,
        system_id=dev.get("systemId"), last_seen=time.time(), found_by=[strategy])


@dataclass
class DiscoveredSystem:
    system_id: str
    name: str | None
    type: str | None
    speakers: list[DiscoveredSpeaker]
    expected_devices: int | None = None
    missing: list[dict] = field(default_factory=list)

    @property
    def leader(self) -> DiscoveredSpeaker | None:
        up = [s for s in self.speakers if s.reachable]
        return next((s for s in up if s.is_leader), None) or (up[0] if up else None)

    @property
    def available(self) -> bool:
        return any(s.reachable for s in self.speakers)

    @property
    def complete(self) -> bool:
        reachable = sum(1 for s in self.speakers if s.reachable)
        expected = self.expected_devices or len(self.speakers)
        return reachable >= expected and not self.missing

    def hosts(self) -> list[str]:
        """Reachable hosts, left speaker first (matches the V1 --hosts order)."""
        order = {"FrontLeft": 0, "FrontRight": 1}
        up = [s for s in self.speakers if s.reachable]
        return [s.host for s in sorted(up, key=lambda s: (order.get(s.role or "", 9), s.host))]

    def to_dict(self) -> dict:
        leader = self.leader
        return {"systemId": self.system_id, "name": self.name, "type": self.type,
                "available": self.available, "complete": self.complete,
                "expectedDevices": self.expected_devices,
                "leader": leader.host if leader and leader.is_leader else None,
                "speakers": [s.to_dict() for s in self.speakers], "missing": self.missing}


class KnownSpeakers:
    """Persistent deviceId -> last known speaker record (JSON file)."""

    def __init__(self, path: Path | None):
        self.path = path
        self.speakers: dict[str, dict] = {}
        self.selected_system: str | None = None
        self.load()

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # a corrupt cache must never prevent startup
        self.speakers = {k: v for k, v in (data.get("speakers") or {}).items() if v.get("host")}
        self.selected_system = data.get("selectedSystem")

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"speakers": self.speakers,
                                   "selectedSystem": self.selected_system},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def hosts(self) -> set[str]:
        return {v["host"] for v in self.speakers.values()}

    def remember(self, speaker: DiscoveredSpeaker) -> None:
        record = asdict(speaker)
        record.pop("reachable", None)
        record.pop("found_by", None)
        self.speakers[speaker.device_id] = record

    def offline_speakers(self, exclude_ids: set[str]) -> list[DiscoveredSpeaker]:
        out = []
        for device_id, rec in self.speakers.items():
            if device_id in exclude_ids:
                continue
            fields = {k: rec.get(k) for k in DiscoveredSpeaker.__dataclass_fields__
                      if k in rec and k not in ("reachable", "found_by")}
            out.append(DiscoveredSpeaker(**{**fields, "device_id": device_id,
                                            "host": rec["host"]},
                                         reachable=False, found_by=["known"]))
        return out


async def native_candidates(timeout: float = 3.0) -> set[str]:
    """mDNS browse + UDP broadcast discovery. Returns candidate IPv4 addresses."""
    from devialet_diag import mdns, network
    from devialet_diag.constants import UDP_V1_PORT, UDP_V2_PORT, UDP_V2_SOLICITATION

    async def from_mdns() -> set[str]:
        services = await mdns.browse(timeout=timeout)
        return {a for s in services if s.is_devialet for a in s.addresses if ":" not in a}

    async def from_udp() -> set[str]:
        result = await asyncio.to_thread(network.listen_udp, [UDP_V1_PORT, UDP_V2_PORT],
                                         timeout, solicitation=(UDP_V2_SOLICITATION, UDP_V2_PORT))
        return {p.source for p in result.packets if p.decoded is not None}

    found: set[str] = set()
    for result in await asyncio.gather(from_mdns(), from_udp(), return_exceptions=True):
        if isinstance(result, set):
            found |= result
    return found


@dataclass
class DiscoveryResult:
    systems: list[DiscoveredSystem]
    started_at: float
    duration_s: float
    probed: int
    strategies: dict[str, dict]
    errors: list[str]

    def find(self, system_id: str) -> DiscoveredSystem | None:
        return next((s for s in self.systems if s.system_id == system_id), None)

    def to_dict(self) -> dict:
        return {"systems": [s.to_dict() for s in self.systems], "startedAt": self.started_at,
                "durationS": round(self.duration_s, 2), "probed": self.probed,
                "strategies": self.strategies, "errors": self.errors}


class DiscoveryService:
    def __init__(self, known: KnownSpeakers, subnets: Iterable[str] = (), native: bool = True,
                 concurrency: int = DEFAULT_CONCURRENCY,
                 transport: httpx.AsyncBaseTransport | None = None,
                 native_finder: NativeFinder | None = None,
                 probe_timeout: httpx.Timeout = PROBE_TIMEOUT):
        self.known = known
        self.subnets = parse_subnets(subnets)
        self.native = native
        self.concurrency = max(1, min(concurrency, MAX_CONCURRENCY))
        self._transport = transport
        self._native_finder = native_finder or native_candidates
        self._timeout = probe_timeout
        self._lock = asyncio.Lock()
        self.last: DiscoveryResult | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def scan(self, extra_hosts: Iterable[str] = (), quick: bool = False) -> DiscoveryResult:
        """Full scan; with `quick`, stop after the known addresses if they all
        answer and form at least one complete system (fast startup)."""
        async with self._lock:
            self.last = await self._scan(list(extra_hosts), quick)
            return self.last

    async def _scan(self, extra_hosts: list[str], quick: bool = False) -> DiscoveryResult:
        started, t0 = time.time(), time.monotonic()
        errors: list[str] = []
        strategies: dict[str, dict] = {}
        found: dict[str, DiscoveredSpeaker] = {}
        probed: set[str] = set()
        limits = httpx.Limits(max_connections=self.concurrency,
                              max_keepalive_connections=self.concurrency)
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport,
                                     limits=limits) as http:
            sem = asyncio.Semaphore(self.concurrency)

            async def probe(host: str, strategy: str) -> DiscoveredSpeaker | None:
                async with sem:
                    try:
                        dev = await IpControlClient(host, http).device()
                    except (IpControlError, SpeakerUnreachable):
                        return None
                return speaker_from_device(host, dev, strategy)

            async def run(strategy: str, hosts: Iterable[str]) -> None:
                todo = [h for h in dict.fromkeys(hosts) if h not in probed]
                probed.update(todo)
                results = await asyncio.gather(*(probe(h, strategy) for h in todo))
                hits = [s for s in results if s]
                strategies.setdefault(strategy, {"probed": 0, "found": 0})
                strategies[strategy]["probed"] += len(todo)
                strategies[strategy]["found"] += len(hits)
                for spk in hits:
                    self._merge(found, spk)

            # 1. known addresses (+ explicit hosts), cheap and router-proof
            await run("known", [*extra_hosts, *sorted(self.known.hosts())])
            if quick and found and set(self.known.speakers) <= set(found):
                systems = await self._group(found, http, errors)
                if any(s.complete for s in systems):
                    strategies["quick"] = {"probed": 0, "found": 0}
                    return self._finish(found, systems, started, t0, probed, strategies, errors)
            # 2. native multicast/broadcast discovery, candidates confirmed by HTTP
            if self.native:
                try:
                    candidates = await self._native_finder()
                except Exception as exc:  # noqa: BLE001 - optional strategy
                    candidates = set()
                    errors.append(f"découverte native : {exc}")
                await run("native", sorted(candidates))
            # 3. explicitly allowed subnets
            for net in self.subnets:
                await run(f"subnet {net}", (str(ip) for ip in net.hosts()))

            systems = await self._group(found, http, errors)
        return self._finish(found, systems, started, t0, probed, strategies, errors)

    def _finish(self, found, systems, started, t0, probed, strategies, errors) -> DiscoveryResult:
        for spk in found.values():
            self.known.remember(spk)
        try:
            self.known.save()
        except OSError as exc:
            errors.append(f"cache non enregistré : {exc}")
        return DiscoveryResult(systems, started, time.monotonic() - t0, len(probed),
                               strategies, errors)

    @staticmethod
    def _merge(found: dict[str, DiscoveredSpeaker], spk: DiscoveredSpeaker) -> None:
        """De-duplicate by deviceId; first answer wins the host, strategies accumulate."""
        current = found.get(spk.device_id)
        if current is None:
            found[spk.device_id] = spk
        else:
            current.found_by.extend(spk.found_by)

    async def _group(self, found: dict[str, DiscoveredSpeaker], http: httpx.AsyncClient,
                     errors: list[str]) -> list[DiscoveredSystem]:
        speakers = list(found.values()) + self.known.offline_speakers(set(found))
        by_system: dict[str, list[DiscoveredSpeaker]] = {}
        for spk in speakers:
            by_system.setdefault(spk.system_id or f"device:{spk.device_id}", []).append(spk)

        async def build(system_id: str, members: list[DiscoveredSpeaker]) -> DiscoveredSystem:
            system = DiscoveredSystem(system_id, None, None, members)
            leader = system.leader
            if leader is None:  # only remembered, nothing answers
                system.name = next((m.name for m in members if m.name), None)
                return system
            try:
                info = await IpControlClient(leader.host, http).system() or {}
            except (IpControlError, SpeakerUnreachable) as exc:
                errors.append(f"système {system_id} : {exc}")
                info = {}
            system.name = info.get("systemName") or leader.name
            system.type = info.get("systemType") or ("single" if len(members) == 1 else None)
            devices = info.get("devices") if isinstance(info.get("devices"), list) else []
            if devices:
                system.expected_devices = len(devices)
                known_ids = {m.device_id for m in members}
                system.missing = [{"deviceId": d.get("deviceId"), "name": d.get("deviceName"),
                                   "role": d.get("role")}
                                  for d in devices if d.get("deviceId") not in known_ids]
            return system

        systems = await asyncio.gather(*(build(k, v) for k, v in by_system.items()))
        return sorted(systems, key=lambda s: (not s.available, s.type != "stereo",
                                              (s.name or "").lower(), s.system_id))


def choose_system(result: DiscoveryResult, preferred: str | None = None) -> DiscoveredSystem | None:
    """Preferred (saved/requested) system if available, else first available stereo, else any."""
    available = [s for s in result.systems if s.available]
    if preferred:
        match = next((s for s in available
                      if preferred in (s.system_id, (s.name or "").lower(), s.name)), None)
        if match:
            return match
    return next((s for s in available if s.type == "stereo"), None) or \
        (available[0] if available else None)
