"""Read-only network probes: TCP connect checks, passive UDP listen, HTTP GET."""
from __future__ import annotations

import asyncio
import json
import select
import socket
import time
import uuid
from dataclasses import dataclass, field

from .constants import IPCONTROL_BASE_PATH, IPCONTROL_READONLY_PATHS, UDP_PREFIX


@dataclass
class PortResult:
    host: str
    port: int
    open: bool
    latency_ms: float | None = None
    error: str | None = None


async def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> PortResult:
    """Open then immediately close a TCP connection; no payload is sent."""
    start = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        return PortResult(host, port, False, error=type(exc).__name__)
    latency = (time.perf_counter() - start) * 1000
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return PortResult(host, port, True, round(latency, 1))


async def check_ports(host: str, ports: list[int], timeout: float = 2.0) -> list[PortResult]:
    return list(await asyncio.gather(*(check_tcp_port(host, p, timeout) for p in sorted(set(ports)))))


def parse_dvlt_packet(data: bytes) -> dict | None:
    """Decode a "_DVLT_" discovery packet (V1 or V2 announcement).

    Layout: "_DVLT_" | u16le version | u8 type | u16le length | payload.
    V1 payload is a 16-byte host UUID; V2 type 0 payload is JSON.
    Returns None when the prefix does not match.
    """
    if not data.startswith(UDP_PREFIX) or len(data) < 11:
        return None
    version = int.from_bytes(data[6:8], "little")
    ptype = data[8]
    length = int.from_bytes(data[9:11], "little")
    payload = data[11:11 + length]
    out: dict = {"version": version, "type": ptype, "length": length}
    if version == 2 and ptype == 0:
        try:
            out["json"] = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            out["payload_hex"] = payload.hex()
    elif version == 1 and len(payload) >= 16:
        out["host_id"] = str(uuid.UUID(bytes=payload[:16]))
    else:
        out["payload_hex"] = payload.hex()
    return out


@dataclass
class UdpPacket:
    source: str
    port: int
    local_port: int
    size: int
    hex: str
    received_at: float
    decoded: dict | None = None


@dataclass
class UdpListenResult:
    ports: list[int]
    solicited: bool = False
    packets: list[UdpPacket] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def listen_udp(ports: list[int], duration: float = 10.0, max_packets: int = 100,
               solicitation: tuple[bytes, int] | None = None) -> UdpListenResult:
    """Listen for broadcast packets on `ports`.

    Nothing is sent unless `solicitation` = (payload, port) is given, in which
    case the same discovery query the app emits is broadcast every 5 s.
    """
    result = UdpListenResult(list(ports), solicited=solicitation is not None)
    socks: list[socket.socket] = []
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", port))
            sock.setblocking(False)
            socks.append(sock)
        except OSError as exc:
            result.errors.append(f"port {port}: {type(exc).__name__}: {exc}")
            sock.close()
    try:
        deadline = time.monotonic() + duration
        next_solicit = time.monotonic()
        while socks and time.monotonic() < deadline and len(result.packets) < max_packets:
            if solicitation and time.monotonic() >= next_solicit:
                payload, port = solicitation
                sender = next((s for s in socks if s.getsockname()[1] == port), socks[0])
                try:
                    sender.sendto(payload, ("255.255.255.255", port))
                except OSError as exc:
                    result.errors.append(f"solicitation: {type(exc).__name__}: {exc}")
                    solicitation = None
                next_solicit = time.monotonic() + 5
            readable, _, _ = select.select(socks, [], [], 0.5)
            for sock in readable:
                data, (src, sport) = sock.recvfrom(65535)
                if solicitation and data == solicitation[0]:
                    continue  # our own broadcast echoed back
                result.packets.append(UdpPacket(src, sport, sock.getsockname()[1], len(data),
                                                data[:512].hex(), time.time(),
                                                parse_dvlt_packet(data)))
    finally:
        for sock in socks:
            sock.close()
    return result


@dataclass
class HttpResult:
    url: str
    status: int | None = None
    content_type: str | None = None
    body: object = None
    error: str | None = None


async def ipcontrol_get(host: str, port: int = 80, base_path: str = IPCONTROL_BASE_PATH,
                        paths=IPCONTROL_READONLY_PATHS, timeout: float = 4.0) -> list[HttpResult]:
    """Issue HTTP GET requests only; never POST/PUT/DELETE."""
    import httpx

    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    base = f"http://{host_part}:{port}{base_path.rstrip('/')}"
    results = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path in paths:
            url = base + path
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                results.append(HttpResult(url, error=f"{type(exc).__name__}: {exc}"))
                continue
            try:
                body = resp.json()
            except (json.JSONDecodeError, ValueError):
                body = resp.text[:2000]
            results.append(HttpResult(url, resp.status_code, resp.headers.get("content-type"), body))
    return results
