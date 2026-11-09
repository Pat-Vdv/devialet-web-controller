"""mDNS / Bonjour discovery (read-only: queries only, never announces)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from zeroconf import IPVersion, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .constants import (
    IPCONTROL_MIN_DOS_VERSION,
    IPCONTROL_REQUIRED_VERSION,
    MDNS_SERVICE_TYPES,
)


@dataclass
class MdnsService:
    service_type: str
    name: str
    server: str | None = None
    port: int | None = None
    addresses: list[str] = field(default_factory=list)
    txt: dict[str, str] = field(default_factory=dict)

    @property
    def is_devialet(self) -> bool:
        if self.txt.get("manufacturer") == "Devialet":
            return True
        # _whatsup._tcp is a Devialet-only service type.
        return self.service_type.startswith("_whatsup.")

    @property
    def serial(self) -> str | None:
        # IP Control TXT uses "serialNumber", WhatsUp TXT uses "sn"
        # (a lost service is reported with an empty record).
        return self.txt.get("serialNumber") or self.txt.get("sn")


def parse_version(text: str | None) -> tuple[int, ...]:
    """Parse "2.18.3.1" style versions like CanonicalVersion; bad parts become 0."""
    parts = []
    for chunk in (text or "0.0.0.0").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def classify_ipcontrol(txt: dict[str, str]) -> tuple[bool, str]:
    """IP Control eligibility of a Bonjour TXT record.

    Returns (eligible, human-readable reason).
    """
    if txt.get("manufacturer") != "Devialet":
        return False, "manufacturer TXT != 'Devialet'"
    if txt.get("ipControlVersion") != IPCONTROL_REQUIRED_VERSION:
        return False, f"ipControlVersion={txt.get('ipControlVersion')!r} (requires '1')"
    family = txt.get("firmwareFamily")
    if family == "SDOS":
        return True, "firmwareFamily=SDOS"
    if family == "DOS":
        version = parse_version(txt.get("firmwareVersion"))
        if version >= IPCONTROL_MIN_DOS_VERSION:
            return True, f"firmwareFamily=DOS, firmwareVersion {txt.get('firmwareVersion')} >= 2.18.0.0"
        return False, f"firmwareFamily=DOS but firmwareVersion {txt.get('firmwareVersion')} < 2.18.0.0"
    return False, f"unknown firmwareFamily={family!r}"


def _decode_txt(properties: dict[bytes, bytes | None]) -> dict[str, str]:
    out = {}
    for k, v in properties.items():
        key = k.decode("utf-8", "replace")
        out[key] = v.decode("utf-8", "replace") if v is not None else ""
    return out


async def browse(timeout: float = 8.0, service_types=MDNS_SERVICE_TYPES,
                 ip_version: IPVersion = IPVersion.All) -> list[MdnsService]:
    """Browse the given service types for `timeout` seconds and resolve them."""
    aiozc = AsyncZeroconf(ip_version=ip_version)
    seen: set[tuple[str, str]] = set()

    def on_change(zeroconf: Zeroconf, service_type: str, name: str,
                  state_change: ServiceStateChange) -> None:
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            seen.add((service_type, name))

    browser = AsyncServiceBrowser(aiozc.zeroconf, list(service_types), handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
        results = []
        for service_type, name in sorted(seen):
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(aiozc.zeroconf, 3000)
            results.append(MdnsService(
                service_type=service_type,
                name=name,
                server=info.server,
                port=info.port,
                addresses=info.parsed_scoped_addresses(),
                txt=_decode_txt(info.properties or {}),
            ))
        return results
    finally:
        await browser.async_cancel()
        await aiozc.async_close()


async def list_service_types(timeout: float = 5.0) -> list[str]:
    """Enumerate every service type announced on the LAN (DNS-SD meta query)."""
    from zeroconf import ZeroconfServiceTypes

    return sorted(await asyncio.to_thread(ZeroconfServiceTypes.find, timeout=timeout))
