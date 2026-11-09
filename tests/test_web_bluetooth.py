"""Bluetooth (Windows audio output) status and pairing mode, simulated speakers only."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web.app import create_app
from devialet_web.ipcontrol import ALLOWED_POSTS, START_ADVERTISING, IpControlClient
from devialet_web.pair import StereoPair, parse_stream
from tests.fake_speakers import ACTIVE_SOURCE, LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

SOURCE = "/groups/current/sources/current"
PC_SOURCE = {**ACTIVE_SOURCE, "peerDeviceName": "DESKTOP-EXAMPLE",
             "streamInfo": {"codec": "pcm", "lossless": False, "supported": True}}


def make(fake: FakePair) -> TestClient:
    return local_client(create_app([LEFT, RIGHT], transport=fake.transport()))


def run_pair(fake, fn):
    async def go():
        async with httpx.AsyncClient(transport=fake.transport()) as http:
            return await fn(StereoPair([IpControlClient(LEFT, http), IpControlClient(RIGHT, http)]))
    return asyncio.run(go())


def test_start_advertising_is_in_the_allow_list():
    assert START_ADVERTISING == "/systems/current/bluetooth/startAdvertising"
    assert START_ADVERTISING in ALLOWED_POSTS and len(ALLOWED_POSTS) == 7  # + equalizer (V2.4.1)


def test_status_with_windows_connected():
    fake = FakePair()
    fake.routes[RIGHT][SOURCE] = (200, PC_SOURCE)
    bt = run_pair(fake, lambda p: p.bluetooth_status())
    assert bt["available"] and bt["advertising"] is False and bt["sourceActive"]
    assert bt["peerDeviceName"] == "DESKTOP-EXAMPLE"
    assert bt["stream"] == {"codec": "pcm", "lossless": False}
    assert bt["systemName"] == "My Phantom System" and bt["errors"] == []
    assert fake.posts == []


def test_status_free_and_other_sources():
    fake = FakePair()
    fake.routes[RIGHT][SOURCE] = (200, {**ACTIVE_SOURCE, "peerDeviceName": ""})
    bt = run_pair(fake, lambda p: p.bluetooth_status())
    assert bt["sourceActive"] and bt["peerDeviceName"] is None
    fake.routes[RIGHT][SOURCE] = (200, {"source": {"type": "airplay2", "sourceId": "x"}})
    bt = run_pair(fake, lambda p: p.bluetooth_status())
    assert bt["sourceActive"] is False and bt["peerDeviceName"] is None


@pytest.mark.parametrize("body", [{"error": {"code": "NoCurrentSource"}}, ""])
def test_status_without_current_source(body):
    fake = FakePair()
    fake.routes[RIGHT][SOURCE] = (200, body)
    bt = run_pair(fake, lambda p: p.bluetooth_status())
    assert bt["sourceActive"] is False and bt["errors"] == []


def test_status_reports_errors():
    fake = FakePair()
    fake.routes[RIGHT][SOURCE] = (200, {"error": {"code": "Busy"}})
    fake.advertising = "weird"
    bt = run_pair(fake, lambda p: p.bluetooth_status())
    assert bt["advertising"] is None and any("Busy" in e for e in bt["errors"])


def test_start_pairing_posts_once_and_is_idempotent():
    fake = FakePair()

    async def twice(p):
        first = await p.start_pairing()
        second = await p.start_pairing()
        return first, second

    first, second = run_pair(fake, twice)
    assert first["advertising"] is True and first["pairingStartedAt"]
    assert second["advertising"] is True
    assert fake.posts == [(RIGHT, START_ADVERTISING, b"")]  # leader only, sent once


def test_pairing_timestamp_cleared_when_window_closes():
    fake = FakePair()

    async def scenario(p):
        await p.start_pairing()
        fake.advertising = "off"  # speaker closed the window after ~2 min
        return await p.bluetooth_status()

    assert run_pair(fake, scenario)["pairingStartedAt"] is None


def test_parse_stream():
    assert parse_stream(None) is None and parse_stream({}) is None
    assert parse_stream({"bitDepth": 16, "samplingRate": 44100, "lossless": False,
                         "channels": "2.0", "supported": True}) == \
        {"samplingRate": 44100, "bitDepth": 16, "lossless": False, "channels": "2.0"}


def test_api_endpoints():
    fake = FakePair()
    fake.routes[RIGHT][SOURCE] = (200, PC_SOURCE)
    with make(fake) as client:
        bt = client.get("/api/bluetooth").json()
        assert bt["peerDeviceName"] == "DESKTOP-EXAMPLE" and bt["advertising"] is False
        assert fake.posts == []
        r = client.post("/api/bluetooth/pairing")
        assert r.status_code == 200 and r.json()["advertising"] is True
        assert client.get("/api/bluetooth").json()["advertising"] is True
    assert fake.posts == [(RIGHT, START_ADVERTISING, b"")]
    # stereo configuration endpoints are never touched
    assert not any("/systems/create" in p or "leaveSystem" in p for _, _, p, _ in fake.requests)


def test_api_pairing_offline():
    fake = FakePair()
    for host in (LEFT, RIGHT):
        fake.routes[host]["/devices/current"] = (200, httpx.ConnectError("x"))
    with make(fake) as client:
        assert client.get("/api/bluetooth").json()["available"] is False
        assert client.post("/api/bluetooth/pairing").status_code == 503
    assert fake.posts == []


def test_api_pairing_speaker_error():
    fake = FakePair()
    original = fake.handler

    def handler(request):
        if request.url.path.endswith("/startAdvertising"):
            fake.requests.append((request.url.host, "POST", request.url.path, request.content))
            return httpx.Response(200, json={"error": {"code": "NotAllowed"}})
        return original(request)

    fake.handler = handler
    with make(fake) as client:
        r = client.post("/api/bluetooth/pairing")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "NotAllowed"


def test_frontend_card_served():
    with make(FakePair()) as client:
        html = client.get("/").text
        assert "bluetooth.js" in html and "ms-settings:sound" in html
        assert client.get("/static/bluetooth.js").status_code == 200
