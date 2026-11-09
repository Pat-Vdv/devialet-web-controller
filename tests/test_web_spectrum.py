"""Spectrum relay: agent <-> backend <-> browsers (simulated, no audio, no speakers)."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client
from starlette.websockets import WebSocketDisconnect

from devialet_web.app import create_app
from devialet_web.spectrum import FLOOR_DB, SpectrumHub, Viewer, parse_levels
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

L10 = [-3.0, -6.0, -9.0, -12.0, -15.0, -18.0, -21.0, -24.0, -27.0, -30.0]
R10 = [-30.0, -27.0, -24.0, -21.0, -18.0, -15.0, -12.0, -9.0, -6.0, -3.0]


def levels(seq=1, l=L10, r=R10, silent=False):
    return json.dumps({"type": "levels", "seq": seq, "l": l, "r": r, "silent": silent, "rate": 44100})


def status(state="capturing", **kw):
    return json.dumps({"type": "status", "state": state, "device": "Haut-parleurs (My Phantom System)",
                       "isDefault": True, "message": "", "discontinuities": 0,
                       "format": "44100 Hz, 2 ch, 32-bit float", **kw})


class Recorder:
    """Fake WebSocket send side."""

    def __init__(self):
        self.messages: list[dict] = []
        self.closed = False

    async def send(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True

    def controls(self) -> list[bool]:
        return [m["capture"] for m in self.messages if m.get("type") == "control"]

    def of_type(self, kind: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == kind]


async def settle(viewer: Viewer | None = None) -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5))


# --- validation ---------------------------------------------------------------

def test_parse_levels_validates_and_clamps():
    assert parse_levels(L10) == L10
    assert parse_levels([-200] * 10) == [FLOOR_DB] * 10
    assert parse_levels([50] * 10) == [6.0] * 10
    for bad in (None, [0] * 9, [0] * 11, ["x"] * 10, [float("nan")] * 10, [True] * 10, "nope"):
        assert parse_levels(bad) is None


def test_agent_endpoint_is_local_and_non_browser_only():
    hub = SpectrumHub()
    assert hub.agent_allowed("127.0.0.1", {})
    assert hub.agent_allowed("::1", {})
    assert not hub.agent_allowed("192.168.1.20", {})
    assert not hub.agent_allowed(None, {})
    assert not hub.agent_allowed("127.0.0.1", {"Origin": "http://evil.example"})


# --- capture control ----------------------------------------------------------

def test_capture_only_when_enabled_and_watched():
    async def scenario():
        hub, agent, browser = SpectrumHub(), Recorder(), Recorder()
        await hub.agent_connected(agent.send)
        viewer = await hub.viewer_connected(browser.send)
        assert agent.controls() == [False]           # watched but disabled
        await hub.set_enabled(True)
        assert agent.controls()[-1] is True
        await hub.viewer_disconnected(viewer)        # nobody watching: stop capturing
        assert agent.controls()[-1] is False
        viewer = await hub.viewer_connected(browser.send)
        assert agent.controls()[-1] is True
        await hub.set_enabled(False)
        assert agent.controls()[-1] is False
        await hub.viewer_disconnected(viewer)

    run(scenario())


def test_levels_relayed_to_browsers_and_bad_frames_rejected():
    async def scenario():
        hub, agent, browser = SpectrumHub(enabled=True), Recorder(), Recorder()
        token = await hub.agent_connected(agent.send)
        viewer = await hub.viewer_connected(browser.send)
        task = asyncio.create_task(viewer.run())
        assert await hub.agent_message(token, status())
        assert await hub.agent_message(token, levels(seq=1, silent=False))
        await settle()
        frames = browser.of_type("levels")
        assert frames and frames[-1]["l"] == L10 and frames[-1]["r"] == R10
        assert frames[-1]["seq"] == 1 and frames[-1]["silent"] is False
        # rejected: wrong band count, not JSON, unknown type, oversized, raw samples
        assert not await hub.agent_message(token, json.dumps({"type": "levels", "l": [0] * 3, "r": R10}))
        assert not await hub.agent_message(token, "not json")
        assert not await hub.agent_message(token, json.dumps({"type": "samples", "pcm": [0.1] * 100}))
        assert not await hub.agent_message(token, levels() + " " * 5000)
        snap = hub.snapshot()
        assert snap["available"] is True and snap["agent"]["device"] == "Haut-parleurs (My Phantom System)"
        viewer.closed = True
        viewer.wake.set()
        await task

    run(scenario())


def test_relayed_frames_never_contain_samples():
    async def scenario():
        hub, browser = SpectrumHub(enabled=True), Recorder()
        token = await hub.agent_connected(Recorder().send)
        viewer = await hub.viewer_connected(browser.send)
        task = asyncio.create_task(viewer.run())
        # a big sample payload is rejected outright (size limit)...
        assert not await hub.agent_message(token, json.dumps({"type": "levels", "seq": 2, "l": L10,
                                                              "r": R10, "pcm": [0.5] * 1000}))
        # ...and unknown fields of a valid frame are dropped, never relayed
        assert await hub.agent_message(token, json.dumps({"type": "levels", "seq": 3, "l": L10, "r": R10,
                                                          "pcm": [0.5] * 10, "extra": "x"}))
        await settle()
        frame = browser.of_type("levels")[-1]
        assert frame["seq"] == 3 and set(frame) == {"type", "seq", "l", "r", "silent"}
        viewer.closed = True
        viewer.wake.set()
        await task

    run(scenario())


def test_browser_rate_is_limited_latest_value_wins():
    async def scenario():
        browser = Recorder()
        viewer = Viewer(browser.send, max_rate=20)
        task = asyncio.create_task(viewer.run())
        for i in range(1, 101):                       # 100 frames in a burst
            viewer.push_levels(json.dumps({"type": "levels", "seq": i}))
            await asyncio.sleep(0)
        await asyncio.sleep(0.2)
        seqs = [m["seq"] for m in browser.messages]
        assert seqs[-1] == 100                        # nothing stale is delivered last
        assert len(seqs) <= 6                         # ~20/s over ~0.2 s, not 100
        viewer.closed = True
        viewer.wake.set()
        await task

    run(scenario())


# --- agent lifecycle ------------------------------------------------------------

def test_agent_disconnect_and_reconnect():
    async def scenario():
        hub, browser = SpectrumHub(enabled=True), Recorder()
        viewer = await hub.viewer_connected(browser.send)
        task = asyncio.create_task(viewer.run())
        first = Recorder()
        token = await hub.agent_connected(first.send)
        await hub.agent_message(token, status())
        assert first.controls() == [True]
        await hub.agent_disconnected(token)
        await settle()
        last = browser.of_type("status")[-1]
        assert last["agent"]["connected"] is False and last["available"] is False
        assert last["reason"] == "Agent Windows non connecté"
        second = Recorder()                            # the agent comes back
        token2 = await hub.agent_connected(second.send)
        assert second.controls() == [True]             # capture request is re-sent
        await hub.agent_message(token2, status())
        await settle()
        assert browser.of_type("status")[-1]["available"] is True
        viewer.closed = True
        viewer.wake.set()
        await task

    run(scenario())


def test_new_agent_replaces_old_session():
    async def scenario():
        hub, old, new = SpectrumHub(), Recorder(), Recorder()
        old_token = await hub.agent_connected(old.send, old.close)
        new_token = await hub.agent_connected(new.send, new.close)
        assert old.closed is True and new.closed is False
        assert not await hub.agent_message(old_token, status())   # stale session ignored
        assert await hub.agent_message(new_token, status())
        await hub.agent_disconnected(old_token)                   # late close of the old one
        assert hub.agent.connected is True

    run(scenario())


def test_frozen_agent_is_detected():
    async def scenario():
        hub, agent = SpectrumHub(enabled=True, stale_after=0.05), Recorder()
        await hub.viewer_connected(Recorder().send)
        token = await hub.agent_connected(agent.send)
        await hub.agent_message(token, status())
        assert hub.snapshot()["available"] is True
        await asyncio.sleep(0.1)                     # no heartbeat
        await hub.refresh()
        snap = hub.snapshot()
        assert snap["available"] is False and snap["reason"] == "Agent Windows sans signe de vie"
        assert agent.controls()[-1] is False         # capture released
        await hub.agent_message(token, status())     # heartbeat again
        await hub.refresh()
        assert hub.snapshot()["available"] is True and agent.controls()[-1] is True

    run(scenario())


@pytest.mark.parametrize("state,reason", [
    ("no_device", "Sortie audio introuvable : no active output contains 'My Phantom System'"),
    ("error", "Erreur de capture : no active output contains 'My Phantom System'"),
    ("idle", "Capture en cours de démarrage"),
])
def test_device_problems_make_analyzer_unavailable(state, reason):
    async def scenario():
        hub = SpectrumHub(enabled=True)
        token = await hub.agent_connected(Recorder().send)
        await hub.agent_message(token, status(state, message="no active output contains 'My Phantom System'"))
        return hub.snapshot()

    snap = run(scenario())
    assert snap["available"] is False and snap["reason"] == reason


def test_device_change_back_to_capturing():
    async def scenario():
        hub = SpectrumHub(enabled=True)
        token = await hub.agent_connected(Recorder().send)
        await hub.agent_message(token, status("no_device", message="gone"))
        assert hub.snapshot()["available"] is False
        await hub.agent_message(token, status("capturing", isDefault=False))
        return hub.snapshot()

    snap = run(scenario())
    assert snap["available"] is True and snap["agent"]["isDefault"] is False


def test_disabled_reason_and_stop_releases_capture():
    async def scenario():
        hub, agent = SpectrumHub(), Recorder()
        await hub.agent_connected(agent.send)
        assert hub.snapshot()["reason"] == "Analyseur désactivé"
        await hub.set_enabled(True)
        await hub.viewer_connected(Recorder().send)
        assert agent.controls()[-1] is True
        await hub.stop()
        assert agent.controls()[-1] is False

    run(scenario())


# --- HTTP / WebSocket integration ---------------------------------------------

def make(hub: SpectrumHub) -> TestClient:
    return local_client(create_app([LEFT, RIGHT], transport=FakePair().transport(), spectrum=hub))


def receive_until(ws, predicate, limit=30):
    for _ in range(limit):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("expected message not received")


def test_rest_enable_disable():
    with make(SpectrumHub()) as client:
        assert client.get("/api/spectrum").json()["enabled"] is False
        assert client.post("/api/spectrum", json={"enabled": True}).json()["enabled"] is True
        assert client.post("/api/spectrum", json={"enabled": "maybe"}).status_code == 422


def test_websocket_agent_rejected_when_not_local():
    with make(SpectrumHub()) as client:                # TestClient host is "testclient"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/spectrum/agent") as ws:
                ws.receive_text()


def test_websocket_end_to_end():
    hub = SpectrumHub(agent_hosts=frozenset({"testclient"}))
    with make(hub) as client:
        client.post("/api/spectrum", json={"enabled": True})
        with client.websocket_connect("/api/spectrum/ws") as browser:
            first = browser.receive_json()
            assert first["type"] == "status" and first["agent"]["connected"] is False
            with client.websocket_connect("/api/spectrum/agent") as agent:
                agent.send_text(json.dumps({"type": "hello", "version": "2.3.0"}))
                assert agent.receive_json() == {"type": "control", "capture": True}
                agent.send_text(status())
                receive_until(browser, lambda m: m["type"] == "status" and m["available"])
                agent.send_text(levels(seq=42))
                frame = receive_until(browser, lambda m: m["type"] == "levels")
                assert frame["seq"] == 42 and frame["l"] == L10
            gone = receive_until(browser, lambda m: m["type"] == "status" and not m["agent"]["connected"])
            assert gone["reason"] == "Agent Windows non connecté"
