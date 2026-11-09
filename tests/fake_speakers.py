"""Simulated Phantom II pair for tests (httpx.MockTransport, no network)."""
from __future__ import annotations

import json

import httpx
import pytest

LEFT, RIGHT = "192.0.2.10", "192.0.2.11"   # documentation addresses (RFC 5737)
BASE = "/ipcontrol/v1"


def device(role: str, leader: bool, name: str) -> dict:
    return {"deviceId": f"id-{role}", "systemId": "sys-1", "groupId": "grp-1",
            "deviceName": name, "role": role, "isSystemLeader": leader,
            "serial": "XXXXXXXXXGAM1", "model": "Phantom II 98 dB", "ipControlVersion": "1",
            "firmwareFamily": "DOS",
            "release": {"version": "2.19.1", "canonicalVersion": "2.19.1.0"}}


ACTIVE_SOURCE = {
    "source": {"sourceId": "src-bt", "deviceId": "id-FrontRight", "type": "bluetooth"},
    "peerDeviceName": "Pixel", "playingState": "playing", "muteState": "unmuted",
    "metadata": {"artist": "Daft Punk", "album": "Discovery", "title": "One More Time"},
    "availableOperations": ["play", "pause", "next", "previous"],
}


class FakePair:
    """Two speakers; each GET path maps to a (status, body) where body may be
    a dict, a raw string (e.g. "" for empty) or an Exception to raise."""

    def __init__(self):
        self.requests: list[tuple[str, str, str, bytes]] = []  # host, method, path, body
        self.volume = 31
        self.advertising = "off"
        # System-level equalizer, shared by both speakers (observed on DOS 2.19.1)
        self.eq_preset = "flat"
        self.eq = {"low": 0, "high": 0}
        self.eq_apply = True           # False: the speaker accepts the POST but ignores it
        self.routes = {
            LEFT: {"/devices/current": (200, device("FrontLeft", False, "Phantom gauche"))},
            RIGHT: {
                "/devices/current": (200, device("FrontRight", True, "Phantom droite")),
                "/systems/current": (200, {"systemId": "sys-1", "groupId": "grp-1",
                                           "systemName": "My Phantom System", "systemType": "stereo"}),
                "/groups/current/sources/current": (200, ACTIVE_SOURCE),
            },
        }

    @property
    def posts(self) -> list[tuple[str, str, bytes]]:
        return [(h, p, b) for h, m, p, b in self.requests if m == "POST"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path.removeprefix(BASE)
        self.requests.append((host, request.method, path, request.content))
        vol_path = "/systems/current/sources/current/soundControl/volume"
        if path == vol_path:
            if request.method == "POST":
                self.volume = json.loads(request.content)["volume"]
                return httpx.Response(200, text="")
            return httpx.Response(200, json={"volume": self.volume})
        if path == "/systems/current/bluetooth" and request.method == "GET" and host == RIGHT:
            return httpx.Response(200, json={"advertising": self.advertising})
        if path == "/systems/current/bluetooth/startAdvertising" and request.method == "POST":
            self.advertising = "on"
            return httpx.Response(200, json={})  # real speaker answers "{}"
        if path == "/systems/current/settings/audio/equalizer" and host in self.eq_hosts:
            if request.method == "POST":
                body = json.loads(request.content)
                if self.eq_apply:
                    self.eq_preset = body["preset"]
                    custom = body.get("customEqualization") or {}
                    self.eq = {b: custom.get(b, {}).get("gain", 0) for b in ("low", "high")}
                return httpx.Response(200, text="")
            return httpx.Response(200, json=self.equalizer_body())
        if request.method == "POST":
            return httpx.Response(200, text="")  # playback actions: empty body = success
        status, body = self.routes.get(host, {}).get(path, (404, {"error": {"code": "NotFound"}}))
        if isinstance(body, Exception):
            raise body
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    eq_hosts = (LEFT, RIGHT)

    def equalizer_body(self) -> dict:
        bands = {b: {"gain": g} for b, g in self.eq.items()}
        return {"availablePresets": ["custom", "flat", "voice"], "currentEqualization": bands,
                "customEqualization": bands, "enabled": True,
                "gainRange": {"max": 6, "min": -6, "stepPrecision": 1}, "preset": self.eq_preset}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Fail loudly if any code path builds a real HTTP transport request."""

    async def forbidden(self, request):
        raise AssertionError(f"real network request attempted: {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
