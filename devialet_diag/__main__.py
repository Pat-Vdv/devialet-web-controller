"""Non-destructive diagnostic of Devialet speakers: python -m devialet_diag [options]"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from . import ble, mdns, network
from .constants import (
    IPCONTROL_BASE_PATH,
    IPCONTROL_DEFAULT_PORT,
    UDP_V1_PORT,
    UDP_V2_PORT,
    UDP_V2_SOLICITATION,
)
from .report import DiagnosticRun, Host, group_by_host, now_iso, render_markdown


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="devialet_diag", description=__doc__)
    p.add_argument("--mdns-timeout", type=float, default=8.0)
    p.add_argument("--no-ble", action="store_true", help="skip the BLE scan")
    p.add_argument("--ble-timeout", type=float, default=10.0)
    p.add_argument("--ble-all", action="store_true", help="list every BLE device in the report")
    p.add_argument("--udp-listen", type=float, default=0.0, metavar="SECONDS",
                   help=f"listen on UDP {UDP_V1_PORT}/{UDP_V2_PORT} (Devialet broadcast discovery)")
    p.add_argument("--udp-solicit", action="store_true",
                   help=f"while listening, broadcast the app's V2 discovery query on {UDP_V2_PORT}")
    p.add_argument("--ports", default="80,443",
                   help="extra TCP ports to check on each host (comma separated)")
    p.add_argument("--http-probe", action="store_true",
                   help="send read-only HTTP GET requests to the IP Control API")
    p.add_argument("--host", action="append", default=[],
                   help="also probe this IP even if not discovered")
    p.add_argument("--out", default="reports", help="output directory")
    return p.parse_args(argv)


async def run(args: argparse.Namespace) -> DiagnosticRun:
    start = time.monotonic()
    started_at = now_iso()
    errors: list[str] = []

    udp_task = None
    if args.udp_listen > 0:
        print(f"[UDP] listening on {UDP_V1_PORT}/{UDP_V2_PORT} for {args.udp_listen:.0f}s"
              f"{' (with V2 solicitation)' if args.udp_solicit else ''} ...", flush=True)
        solicitation = (UDP_V2_SOLICITATION, UDP_V2_PORT) if args.udp_solicit else None
        udp_task = asyncio.create_task(asyncio.to_thread(
            network.listen_udp, [UDP_V1_PORT, UDP_V2_PORT], args.udp_listen,
            solicitation=solicitation))

    print(f"[mDNS] browsing for {args.mdns_timeout:.0f}s ...", flush=True)
    types_task = asyncio.create_task(mdns.list_service_types(timeout=min(args.mdns_timeout, 5)))
    services: list[mdns.MdnsService] = []
    service_types: list[str] = []
    try:
        services = await mdns.browse(timeout=args.mdns_timeout)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report, not crash
        errors.append(f"mDNS browse failed: {exc!r}")
    try:
        service_types = await types_task
    except Exception as exc:  # noqa: BLE001
        errors.append(f"mDNS type enumeration failed: {exc!r}")

    hosts = group_by_host(services)
    print(f"[mDNS] {len(hosts)} Devialet host(s)", flush=True)

    udp = await udp_task if udp_task else None
    if udp:
        for packet in udp.packets:
            if packet.decoded is not None:
                hosts.setdefault(packet.source, Host(packet.source))
        print(f"[UDP] {len(udp.packets)} packet(s)", flush=True)
    for extra in args.host:
        hosts.setdefault(extra, Host(extra))

    extra_ports = [int(p) for p in args.ports.split(",") if p.strip()]
    for host in hosts.values():
        ports = extra_ports + host.advertised_ports + [IPCONTROL_DEFAULT_PORT]
        host.ports = await network.check_ports(host.address, ports)
        if args.http_probe:
            http_svc = next((s for s in host.services if s.txt.get("ipControlVersion")), None)
            port = http_svc.port if http_svc and http_svc.port else IPCONTROL_DEFAULT_PORT
            base = (http_svc.txt.get("path") if http_svc else None) or IPCONTROL_BASE_PATH
            print(f"[HTTP] GET {host.address}:{port}{base}/...", flush=True)
            host.http = await network.ipcontrol_get(host.address, port, base)

    ble_devices, ble_error = None, None
    if not args.no_ble:
        print(f"[BLE] passive scan for {args.ble_timeout:.0f}s ...", flush=True)
        try:
            ble_devices = await ble.scan(args.ble_timeout)
        except Exception as exc:  # noqa: BLE001
            ble_error = f"{type(exc).__name__}: {exc}"

    return DiagnosticRun(
        started_at=started_at,
        duration_s=time.monotonic() - start,
        options={k: v for k, v in vars(args).items() if k != "out"},
        service_types=service_types,
        mdns=services,
        hosts=hosts,
        ble=ble_devices,
        ble_error=ble_error,
        udp=udp,
        errors=errors,
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    result = asyncio.run(run(args))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = result.started_at.replace(":", "").replace("-", "")
    md_path = out / f"diagnostic-results-{stamp}.md"
    json_path = out / f"diagnostic-results-{stamp}.json"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    json_path.write_text(result.to_json(), encoding="utf-8")
    print(f"Report: {md_path}\nRaw data: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
