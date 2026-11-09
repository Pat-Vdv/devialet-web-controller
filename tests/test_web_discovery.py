"""DiscoveryService against a simulated LAN (httpx.MockTransport, no network)."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web.app import create_app
from devialet_web.discovery import (
    DiscoveryService,
    KnownSpeakers,
    SubnetNotAllowed,
    choose_system,
    parse_subnets,
)
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

BASE = "/ipcontrol/v1"


class FakeLan:
    """host -> device JSON; systems: systemId -> GET /systems/current JSON.
    Unknown hosts time out; `weird` hosts answer something that is not IP Control."""

    def __init__(self):
        self.devices: dict[str, dict] = {}
        self.systems: dict[str, dict] = {}
        self.weird: dict[str, httpx.Response] = {}
        self.requests: list[tuple[str, str, str]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.delay = 0.0

    def add(self, host, device_id, system_id, role="Mono", leader=True, name=None,
            system_name=None, system_type=None, members=None):
        self.devices[host] = {
            "deviceId": device_id, "systemId": system_id, "deviceName": name or device_id,
            "role": role, "isSystemLeader": leader, "model": "Phantom II 98 dB",
            "serial": f"SER{device_id}", "ipControlVersion": "1", "firmwareFamily": "DOS",
            "release": {"version": "2.19.1", "canonicalVersion": "2.19.1.0"}}
        if system_name is not None:
            self.systems[system_id] = {
                "systemId": system_id, "systemName": system_name, "systemType": system_type,
                "devices": members or [{"deviceId": device_id, "role": role}]}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path.removeprefix(BASE)
        self.requests.append((host, request.method, path))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if host in self.weird:
                return self.weird[host]
            dev = self.devices.get(host)
            if dev is None:
                raise httpx.ConnectTimeout("no host", request=request)
            if path == "/devices/current":
                return httpx.Response(200, json=dev)
            if path == "/systems/current" and dev["systemId"] in self.systems:
                return httpx.Response(200, json=self.systems[dev["systemId"]])
            return httpx.Response(200, json={"error": {"code": "NotFound"}})
        finally:
            self.in_flight -= 1

    def transport(self):
        return httpx.MockTransport(self.handler)


def stereo_pair(lan: FakeLan, left="192.168.1.13", right="192.168.1.53", sid="sys-phantom",
                name="My Phantom System"):
    members = [{"deviceId": f"{sid}-L", "role": "FrontLeft", "deviceName": "gauche"},
               {"deviceId": f"{sid}-R", "role": "FrontRight", "deviceName": "droite"}]
    lan.add(left, f"{sid}-L", sid, "FrontLeft", leader=False, name="gauche")
    lan.add(right, f"{sid}-R", sid, "FrontRight", leader=True, name="droite",
            system_name=name, system_type="stereo", members=members)


def service(lan, tmp_path, subnets=("192.168.1.0/28",), native_hosts=None, **kw):
    async def native():
        return set(native_hosts or ())

    return DiscoveryService(KnownSpeakers(tmp_path / "known.json"), subnets=subnets,
                            native=native_hosts is not None, transport=lan.transport(),
                            native_finder=native, **kw)


def scan(svc, **kw):
    return asyncio.run(svc.scan(**kw))


# --- configuration guards -------------------------------------------------

@pytest.mark.parametrize("bad", ["10.0.0.0/8", "192.168.0.0/16", "8.8.8.0/24",
                                 "127.0.0.0/24", "fd00::/120", "not-a-net"])
def test_subnet_guard_rejects_large_public_or_invalid(bad):
    with pytest.raises(SubnetNotAllowed):
        parse_subnets([bad])


def test_subnet_guard_accepts_private_up_to_slash22():
    nets = parse_subnets(["192.168.1.0/24", "10.1.4.0/22", " ", "172.16.5.7/30"])
    assert [str(n) for n in nets] == ["192.168.1.0/24", "10.1.4.0/22", "172.16.5.4/30"]


# --- scenarios requested ----------------------------------------------------

def test_single_speaker(tmp_path):
    lan = FakeLan()
    lan.add("192.168.1.5", "solo", "sys-solo", system_name="cuisine", system_type="single")
    result = scan(service(lan, tmp_path))
    assert len(result.systems) == 1
    sys_ = result.systems[0]
    assert (sys_.name, sys_.type, sys_.available, sys_.complete) == ("cuisine", "single", True, True)
    assert sys_.to_dict()["leader"] == "192.168.1.5"
    spk = sys_.speakers[0]
    assert (spk.model, spk.firmware, spk.host) == ("Phantom II 98 dB", "2.19.1", "192.168.1.5")


def test_stereo_pair_grouped_with_leader(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    result = scan(service(lan, tmp_path))
    assert len(result.systems) == 1
    s = result.systems[0]
    assert s.name == "My Phantom System" and s.type == "stereo" and s.complete
    assert s.leader.host == "192.168.1.9"
    assert s.hosts() == ["192.168.1.4", "192.168.1.9"]  # left first, like V1 --hosts


def test_multiple_systems_sorted_stereo_first(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.4", "192.168.1.9", sid="sys-b", name="My Phantom System")
    stereo_pair(lan, "192.168.1.2", "192.168.1.3", sid="sys-s", name="salon")
    lan.add("192.168.1.12", "solo", "sys-c", system_name="cuisine", system_type="single")
    result = scan(service(lan, tmp_path))
    assert [s.name for s in result.systems] == ["My Phantom System", "salon", "cuisine"]
    assert choose_system(result).name == "My Phantom System"
    assert choose_system(result, "salon").name == "salon"
    assert choose_system(result, "sys-c").name == "cuisine"


def test_unreachable_known_speaker_is_kept_and_flagged(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    svc = service(lan, tmp_path)
    scan(svc)
    del lan.devices["192.168.1.4"]  # left speaker switched off
    result = scan(svc)
    s = result.systems[0]
    left = next(x for x in s.speakers if x.role == "FrontLeft")
    assert left.reachable is False and left.host == "192.168.1.4"
    assert s.available and not s.complete
    assert s.hosts() == ["192.168.1.9"]
    assert "sys-phantom-L" in KnownSpeakers(tmp_path / "known.json").speakers  # still remembered


def test_ip_address_change_updates_cache_without_duplicate(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    svc = service(lan, tmp_path)
    scan(svc)
    lan.devices["192.168.1.11"] = lan.devices.pop("192.168.1.4")  # DHCP gave a new address
    result = scan(svc)
    speakers = result.systems[0].speakers
    assert sorted(s.host for s in speakers) == ["192.168.1.11", "192.168.1.9"]
    assert all(s.reachable for s in speakers) and len(speakers) == 2
    cache = json.loads((tmp_path / "known.json").read_text())
    assert cache["speakers"]["sys-phantom-L"]["host"] == "192.168.1.11"


def test_ip_change_found_without_subnet_is_reported_offline(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    scan(service(lan, tmp_path))
    lan.devices["192.168.5.20"] = lan.devices.pop("192.168.1.4")  # moved outside the allowed subnet
    result = scan(service(lan, tmp_path))
    left = next(s for s in result.systems[0].speakers if s.role == "FrontLeft")
    assert left.reachable is False


def test_partial_discovery_lists_missing_member(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.40", "192.168.1.9")  # left is outside 192.168.1.0/28
    result = scan(service(lan, tmp_path))
    s = result.systems[0]
    assert s.available and not s.complete and s.expected_devices == 2
    assert s.missing == [{"deviceId": "sys-phantom-L", "name": "gauche", "role": "FrontLeft"}]
    assert s.to_dict()["leader"] == "192.168.1.9"


# --- strategies, dedup, safety ------------------------------------------

def test_known_and_native_strategies_without_subnet(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.40", "192.168.1.90")
    svc = service(lan, tmp_path, subnets=(), native_hosts={"192.168.1.40", "192.168.1.90"})
    first = scan(svc)
    assert first.strategies["native"] == {"probed": 2, "found": 2}
    # second run: native finds nothing (e.g. WSL NAT), the cache is enough
    svc2 = service(lan, tmp_path, subnets=(), native_hosts=set())
    second = scan(svc2)
    assert second.strategies["known"] == {"probed": 2, "found": 2}
    assert second.systems[0].complete


def test_duplicates_across_strategies_are_merged(tmp_path):
    lan = FakeLan()
    lan.add("192.168.1.5", "solo", "sys-solo", system_name="cuisine", system_type="single")
    svc = service(lan, tmp_path, native_hosts={"192.168.1.5"})
    result = scan(svc, extra_hosts=["192.168.1.5"])
    assert len(result.systems) == 1 and len(result.systems[0].speakers) == 1
    # each address is probed only once per scan, whatever the number of strategies
    probes = [r for r in lan.requests if r == ("192.168.1.5", "GET", "/devices/current")]
    assert len(probes) == 1


def test_non_devialet_http_servers_are_ignored(tmp_path):
    lan = FakeLan()
    lan.weird = {"192.168.1.2": httpx.Response(200, text="<html>router</html>"),
                 "192.168.1.3": httpx.Response(404, text="nope"),
                 "192.168.1.4": httpx.Response(200, json={"hello": "world"})}
    result = scan(service(lan, tmp_path))
    assert result.systems == []


def test_concurrency_is_bounded_and_only_get_is_sent(tmp_path):
    lan = FakeLan()
    lan.delay = 0.01
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    svc = service(lan, tmp_path, subnets=("192.168.1.0/26",), concurrency=5)
    result = scan(svc)
    assert result.probed == 62
    assert lan.max_in_flight <= 5
    assert {m for _, m, _ in lan.requests} == {"GET"}
    assert {p for _, _, p in lan.requests} <= {"/devices/current", "/systems/current"}


def test_native_failure_does_not_break_scan(tmp_path):
    lan = FakeLan()
    lan.add("192.168.1.5", "solo", "sys-solo", system_name="cuisine", system_type="single")

    async def broken():
        raise OSError("multicast unavailable")

    svc = DiscoveryService(KnownSpeakers(tmp_path / "k.json"), subnets=["192.168.1.0/28"],
                           transport=lan.transport(), native_finder=broken)
    result = scan(svc)
    assert len(result.systems) == 1 and any("multicast" in e for e in result.errors)


def test_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "known.json"
    path.write_text("{not json")
    assert KnownSpeakers(path).speakers == {}


# --- web integration --------------------------------------------------------

def make_app(lan, tmp_path, hosts=None, subnets=("192.168.1.0/28",), **kw):
    svc = service(lan, tmp_path, subnets=subnets)
    return local_client(create_app(hosts, transport=lan.transport(), discovery=svc, **kw))


def test_app_starts_without_hosts_and_selects_stereo(tmp_path):
    lan = FakeLan()
    lan.add("192.168.1.12", "solo", "sys-c", system_name="cuisine", system_type="single")
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    with make_app(lan, tmp_path) as client:
        disc = client.get("/api/discovery").json()
        assert disc["selectedSystemId"] == "sys-phantom" and len(disc["systems"]) == 2
        state = client.get("/api/state").json()
        assert state["system"]["name"] == "My Phantom System" and state["leader"] == "192.168.1.9"
    assert {m for _, m, _ in lan.requests} == {"GET"}


def test_app_select_other_system_and_persist(tmp_path):
    lan = FakeLan()
    lan.add("192.168.1.12", "solo", "sys-c", system_name="cuisine", system_type="single")
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    with make_app(lan, tmp_path) as client:
        r = client.post("/api/systems/select", json={"systemId": "sys-c"})
        assert r.status_code == 200 and r.json()["selectedSystemId"] == "sys-c"
        assert client.get("/api/state").json()["system"]["name"] == "cuisine"
        assert client.post("/api/systems/select", json={"systemId": "nope"}).status_code == 404
    # the saved selection wins at next startup
    with make_app(lan, tmp_path) as client:
        assert client.get("/api/discovery").json()["selectedSystemId"] == "sys-c"


def test_app_select_unavailable_system_is_refused(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    lan.add("192.168.1.12", "solo", "sys-c", system_name="cuisine", system_type="single")
    with make_app(lan, tmp_path) as client:
        del lan.devices["192.168.1.12"]
        assert client.post("/api/discovery/scan").status_code == 200
        assert client.post("/api/systems/select", json={"systemId": "sys-c"}).status_code == 409


def test_app_with_nothing_found_stays_usable(tmp_path):
    lan = FakeLan()
    with make_app(lan, tmp_path) as client:
        state = client.get("/api/state").json()
        assert state["leader"] is None and state["errors"] == ["Aucun système sélectionné"]
        assert client.post("/api/volume", json={"volume": 10}).status_code == 503
        assert client.post("/api/playback/play").status_code == 503
        stereo_pair(lan, "192.168.1.4", "192.168.1.9")  # speakers come online
        systems = client.post("/api/discovery/scan").json()["systems"]
        client.post("/api/systems/select", json={"systemId": systems[0]["systemId"]})
        assert client.get("/api/state").json()["system"]["name"] == "My Phantom System"
    assert {m for _, m, _ in lan.requests} == {"GET"}


def test_app_v1_hosts_mode_scan_reprobes_current_pair(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.40", "192.168.1.90")
    with make_app(lan, tmp_path, hosts=["192.168.1.40", "192.168.1.90"], subnets=()) as client:
        assert client.get("/api/state").json()["leader"] == "192.168.1.90"
        disc = client.post("/api/discovery/scan").json()
        assert disc["selectedSystemId"] == "sys-phantom"
        assert disc["systems"][0]["complete"] is True


def test_quick_scan_uses_cache_only_when_complete(tmp_path):
    lan = FakeLan()
    stereo_pair(lan, "192.168.1.4", "192.168.1.9")
    scan(service(lan, tmp_path))  # fills the cache
    lan.requests.clear()
    quick = scan(service(lan, tmp_path), quick=True)
    assert "quick" in quick.strategies and quick.probed == 2
    assert quick.systems[0].complete
    # a known speaker is missing: quick mode falls back to the full scan
    lan.devices["192.168.1.11"] = lan.devices.pop("192.168.1.4")
    full = scan(service(lan, tmp_path), quick=True)
    assert "quick" not in full.strategies and full.systems[0].complete
    assert sorted(s.host for s in full.systems[0].speakers) == ["192.168.1.11", "192.168.1.9"]
