"""Aggregate probe results per host and render a Markdown technical report."""
from __future__ import annotations

import json
import platform
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import __version__
from .ble import BleDevice
from .mdns import MdnsService, classify_ipcontrol
from .network import HttpResult, PortResult, UdpListenResult


@dataclass
class Host:
    address: str
    hostnames: set[str] = field(default_factory=set)
    serials: set[str] = field(default_factory=set)
    services: list[MdnsService] = field(default_factory=list)
    ports: list[PortResult] = field(default_factory=list)
    http: list[HttpResult] = field(default_factory=list)

    @property
    def advertised_ports(self) -> list[int]:
        return sorted({s.port for s in self.services if s.port})

    @property
    def ipcontrol(self) -> tuple[bool, str] | None:
        """Best IP Control verdict among this host's HTTP announcements."""
        verdicts = [classify_ipcontrol(s.txt) for s in self.services
                    if not s.service_type.startswith("_whatsup.") and s.txt]
        if not verdicts:
            return None
        return next((v for v in verdicts if v[0]), verdicts[0])


def group_by_host(services: list[MdnsService]) -> dict[str, Host]:
    """Group Devialet services by IPv4 address (falling back to first address)."""
    hosts: dict[str, Host] = {}
    for svc in services:
        if not svc.is_devialet or not svc.addresses:
            continue
        ipv4 = [a for a in svc.addresses if ":" not in a]
        address = (ipv4 or svc.addresses)[0]
        host = hosts.setdefault(address, Host(address))
        host.services.append(svc)
        if svc.server:
            host.hostnames.add(svc.server.rstrip("."))
        if svc.serial:
            host.serials.add(svc.serial)
    return hosts


@dataclass
class DiagnosticRun:
    started_at: str
    duration_s: float
    options: dict
    service_types: list[str]
    mdns: list[MdnsService]
    hosts: dict[str, Host]
    ble: list[BleDevice] | None
    ble_error: str | None
    udp: UdpListenResult | None
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        def default(o):
            if isinstance(o, set):
                return sorted(o)
            raise TypeError(type(o))

        data = asdict(self)
        for addr, host in self.hosts.items():
            data["hosts"][addr]["ipcontrol"] = host.ipcontrol
        return json.dumps(data, indent=2, default=default, ensure_ascii=False)


def _txt_table(txt: dict[str, str]) -> list[str]:
    if not txt:
        return ["  _(no TXT record)_"]
    return [f"  - `{k}` = `{v}`" for k, v in sorted(txt.items())]


def render_markdown(run: DiagnosticRun) -> str:
    out = [
        "# Devialet diagnostic results",
        "",
        f"- Date (UTC): {run.started_at}",
        f"- Duration: {run.duration_s:.1f} s",
        f"- Tool version: {__version__}",
        f"- Machine: {socket.gethostname()} / {platform.system()} {platform.release()} / "
        f"Python {platform.python_version()}",
        f"- Options: `{json.dumps(run.options)}`",
        "",
        "All probes were read-only (mDNS queries, passive BLE scan, passive UDP listen, "
        "TCP connect checks, HTTP GET).",
        "",
        "## Summary",
        "",
        f"- Devialet hosts found via mDNS: **{len(run.hosts)}**",
    ]
    if run.ble is not None:
        cands = [d for d in run.ble if d.is_candidate]
        out.append(f"- BLE devices seen: {len(run.ble)}, Devialet candidates: **{len(cands)}**")
    if run.udp is not None:
        dvlt = sum(1 for p in run.udp.packets if p.decoded is not None)
        out.append(f"- UDP packets on ports {run.udp.ports}: {len(run.udp.packets)} "
                   f"({dvlt} Devialet `_DVLT_`)")
    if run.errors:
        out += ["", "### Errors", ""] + [f"- {e}" for e in run.errors]

    out += ["", "## Devialet hosts", ""]
    if not run.hosts:
        out.append("_None found. On Windows check the firewall (UDP 5353); under WSL2 in NAT "
                   "mode mDNS does not reach the LAN - run the tool with Windows Python._")
        out.append("")
    for addr, host in sorted(run.hosts.items()):
        out += [f"### {addr}", ""]
        out.append(f"- Hostnames: {', '.join(sorted(host.hostnames)) or '-'}")
        out.append(f"- Serial numbers: {', '.join(sorted(host.serials)) or '-'}")
        verdict = host.ipcontrol
        if verdict is None:
            out.append("- IP Control: _no HTTP announcement with TXT seen_")
        else:
            out.append(f"- IP Control eligible (app rules): **{'yes' if verdict[0] else 'no'}** "
                       f"- {verdict[1]}")
        out += ["", "**Announced services**", ""]
        for svc in host.services:
            out.append(f"- `{svc.service_type}` **{svc.name}** port {svc.port} "
                       f"addresses {', '.join(svc.addresses)}")
            out += _txt_table(svc.txt)
        if host.ports:
            out += ["", "**TCP ports**", "", "| Port | Open | Latency (ms) | Error |",
                    "|---|---|---|---|"]
            out += [f"| {p.port} | {'yes' if p.open else 'no'} | {p.latency_ms or ''} | "
                    f"{p.error or ''} |" for p in host.ports]
        if host.http:
            out += ["", "**IP Control HTTP GET**", ""]
            for r in host.http:
                out.append(f"- `GET {r.url}` -> {r.status or r.error} ({r.content_type or '-'})")
                if r.body not in (None, ""):
                    body = json.dumps(r.body, indent=2, ensure_ascii=False) \
                        if not isinstance(r.body, str) else r.body
                    out += ["", "```json", body[:4000], "```", ""]
        out.append("")

    out += ["## All mDNS service types on the LAN", ""]
    out += [f"- `{t}`" for t in run.service_types] or ["_not collected_"]

    out += ["", "## Non-Devialet mDNS answers for browsed types", ""]
    others = [s for s in run.mdns if not s.is_devialet]
    out += [f"- `{s.service_type}` {s.name} ({', '.join(s.addresses)})" for s in others] \
        or ["_none_"]

    out += ["", "## Bluetooth LE", ""]
    if run.ble_error:
        out.append(f"_BLE scan failed: {run.ble_error}_")
    elif run.ble is None:
        out.append("_skipped_")
    else:
        out += ["| Address | Name | RSSI | Devialet service UUID | Product type | Serial | "
                "Firmware | Manufacturer data |",
                "|---|---|---|---|---|---|---|---|"]
        for d in run.ble:
            if not d.is_candidate and not run.options.get("ble_all"):
                continue
            manu = ", ".join(f"0x{k:04x}:{v}" for k, v in d.manufacturer_data.items())
            adv = d.devialet_advertisement
            out.append(f"| {d.address} | {d.name or ''} | {d.rssi} | "
                       f"{'yes' if d.advertises_devialet_service else 'no'} | "
                       f"{adv.product_type if adv else ''} | {(adv.serial or '') if adv else ''} | "
                       f"{(adv.firmware or '') if adv else ''} | {manu} |")
        out.append("")
        out.append("Only candidates are listed (use `--ble-all` for every device). "
                   "No BLE connection was made.")

    if run.udp is not None:
        mode = "with V2 solicitation" if run.udp.solicited else "passive"
        out += ["", f"## UDP discovery on ports {run.udp.ports} ({mode})", ""]
        out += [f"_error: {e}_" for e in run.udp.errors]
        for p in run.udp.packets[:30]:
            out.append(f"- {p.source}:{p.port} -> :{p.local_port} {p.size} bytes `{p.hex[:160]}`")
            if p.decoded is not None:
                out.append(f"  - decoded: `{json.dumps(p.decoded, ensure_ascii=False)}`")
        if not run.udp.packets and not run.udp.errors:
            out.append("_no packet received_")
    out.append("")
    return "\n".join(out)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
