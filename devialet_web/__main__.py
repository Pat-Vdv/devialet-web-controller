"""Run the web controller.

    python -m devialet_web --hosts 192.0.2.10,192.0.2.11         # fixed pair (V1)
    python -m devialet_web --subnet 192.168.1.0/24               # automatic discovery
    python -m devialet_web --subnet 192.168.1.0/24 --discover-only

Listens on 127.0.0.1 only. Access from other devices of the LAN needs
--allow-lan, --bind, at least one --allowed-host and DEVIALET_ACCESS_TOKEN
(see README, "Accès depuis le réseau local").
(192.0.2.x: documentation addresses, RFC 5737; use your speakers' IPs.)
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import sys
from pathlib import Path

import uvicorn

from .app import create_app
from .graphic_eq import DEFAULT_CONFIG_DIR, GraphicEq
from .discovery import (
    DEFAULT_CONCURRENCY,
    DiscoveryService,
    KnownSpeakers,
    SubnetNotAllowed,
    choose_system,
)
from .pair import VOLUME_HARD_CAP
from .security import SecurityPolicy

DEFAULT_CACHE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) \
    / "devialet-web" / "known_speakers.json"


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="devialet_web", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hosts", default=os.environ.get("DEVIALET_HOSTS", ""),
                   help="speaker IPs, comma separated (env DEVIALET_HOSTS); disables startup discovery")
    p.add_argument("--subnet", action="append", default=_split(os.environ.get("DEVIALET_SUBNETS", "")),
                   help="private subnet allowed for HTTP discovery, e.g. 192.168.1.0/24 "
                        "(repeatable, max /22, env DEVIALET_SUBNETS)")
    p.add_argument("--no-native", action="store_true",
                   help="disable mDNS / UDP broadcast discovery")
    p.add_argument("--cache", default=os.environ.get("DEVIALET_CACHE", str(DEFAULT_CACHE)),
                   help="known speakers file (env DEVIALET_CACHE)")
    p.add_argument("--system", default=os.environ.get("DEVIALET_SYSTEM"),
                   help="preferred system name or id (env DEVIALET_SYSTEM)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help="parallel HTTP probes during discovery (max 64)")
    p.add_argument("--discover-only", action="store_true",
                   help="run one discovery, print the result as JSON and exit")
    p.add_argument("--max-volume", type=int,
                   default=int(os.environ.get("DEVIALET_MAX_VOLUME", VOLUME_HARD_CAP)),
                   help=f"volume cap, at most {VOLUME_HARD_CAP} (env DEVIALET_MAX_VOLUME)")
    p.add_argument("--volume-step", type=int, default=2, help="step for the +/- buttons")
    p.add_argument("--eapo-config", default=os.environ.get("DEVIALET_EAPO_CONFIG", DEFAULT_CONFIG_DIR),
                   help="Equalizer APO config folder seen from WSL (env DEVIALET_EAPO_CONFIG); "
                        "only devialet-eq.txt is written there; empty string disables the equalizer")
    p.add_argument("--bind", default=os.environ.get("DEVIALET_BIND", "127.0.0.1"),
                   help="listening address (env DEVIALET_BIND); other than loopback only with --allow-lan")
    p.add_argument("--allow-lan", action="store_true", default=os.environ.get("DEVIALET_ALLOW_LAN") == "1",
                   help="accept other devices of the LAN (env DEVIALET_ALLOW_LAN=1); requires "
                        "--allowed-host and the DEVIALET_ACCESS_TOKEN environment variable")
    p.add_argument("--allowed-host", action="append", default=_split(os.environ.get("DEVIALET_ALLOWED_HOSTS", "")),
                   help="name or IP the other devices use to reach this server (repeatable, "
                        "env DEVIALET_ALLOWED_HOSTS)")
    p.add_argument("--port", type=int, default=int(os.environ.get("DEVIALET_PORT", 8000)))
    return p.parse_args(argv)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def security_policy(args: argparse.Namespace) -> SecurityPolicy:
    """Local only by default; LAN access only when every safeguard is given."""
    if not args.allow_lan:
        if not _is_loopback(args.bind):
            raise ValueError(f"--bind {args.bind} expose le serveur au réseau : ajoutez --allow-lan, "
                             "--allowed-host et DEVIALET_ACCESS_TOKEN, ou gardez 127.0.0.1")
        return SecurityPolicy()
    return SecurityPolicy.lan(args.allowed_host, os.environ.get("DEVIALET_ACCESS_TOKEN", ""))


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        policy = security_policy(args)
    except ValueError as exc:
        print(f"Accès réseau refusé : {exc}", file=sys.stderr)
        return 2
    hosts = _split(args.hosts)
    try:
        discovery = DiscoveryService(KnownSpeakers(Path(args.cache)), subnets=args.subnet,
                                     native=not args.no_native, concurrency=args.concurrency)
    except SubnetNotAllowed as exc:
        print(f"Sous-réseau refusé : {exc}", file=sys.stderr)
        return 2

    if args.discover_only:
        result = asyncio.run(discovery.scan(extra_hosts=hosts))
        chosen = choose_system(result, args.system or discovery.known.selected_system)
        print(json.dumps({**result.to_dict(),
                          "wouldSelect": chosen.system_id if chosen else None},
                         indent=2, ensure_ascii=False))
        return 0 if result.systems else 1

    if args.max_volume > VOLUME_HARD_CAP:
        print(f"--max-volume ramené à {VOLUME_HARD_CAP} (plafond de la V1)", file=sys.stderr)
    if not 1 <= args.volume_step <= 10:
        print("--volume-step doit être entre 1 et 10", file=sys.stderr)
        return 2
    if not hosts and not discovery.subnets and not discovery.known.hosts():
        print("Aucune adresse connue ni sous-réseau : la découverte native (mDNS/UDP) sera la "
              "seule source. Sous WSL2 en NAT, ajoutez --subnet 192.168.1.0/24 ou --hosts.",
              file=sys.stderr)
    try:
        graphic_eq = GraphicEq(args.eapo_config) if args.eapo_config else None
    except ValueError as exc:
        print(f"--eapo-config : {exc}", file=sys.stderr)
        return 2
    app = create_app(hosts, max_volume=args.max_volume, volume_step=args.volume_step,
                     discovery=discovery, preferred_system=args.system, graphic_eq=graphic_eq,
                     security=policy)
    if policy.access_token:
        print(f"Accès réseau activé. Ouvrez une fois http://<{'|'.join(sorted(args.allowed_host))}>:"
              f"{args.port}/?access=<DEVIALET_ACCESS_TOKEN> sur chaque appareil.", file=sys.stderr)
    uvicorn.run(app, host=args.bind, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
