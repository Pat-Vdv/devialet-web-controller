"""Minimal async client for the Devialet IP Control v1 HTTP API.

Reads are GET, actions are POST with a
JSON body (possibly empty). Errors may arrive with HTTP 200 as
{"error": {"code": ..., "message": ...}}, so every body is checked.

Safety: POST is restricted to an explicit allow-list (volume, playback,
Bluetooth pairing mode and the 2-band tone equalizer). Any other write - notably /systems/create,
/devices/current/leaveSystem, role changes, settings, power or firmware
endpoints - raises before a request is built.
"""
from __future__ import annotations

import json
import math
from typing import Any

import httpx

from devialet_diag.constants import IPCONTROL_BASE_PATH, IPCONTROL_DEFAULT_PORT

# Paths relative to the base path; "current" ids only.
DEVICE = "/devices/current"
SYSTEM = "/systems/current"
ACTIVE_SOURCE = "/groups/current/sources/current"
SYSTEM_VOLUME = "/systems/current/sources/current/soundControl/volume"
PLAYBACK = "/groups/current/sources/current/playback/"

# Bluetooth pairing mode of the system: {"advertising": "on"|"off"}.
# startAdvertising makes the pair discoverable for ~2 min; it does not pair,
# unpair or change the stereo configuration (validated 2026-10-27).
BLUETOOTH = "/systems/current/bluetooth"
START_ADVERTISING = BLUETOOTH + "/startAdvertising"

# System-level 2-band equalizer = native BASS (low) / TREBLE (high) of the pair
# GET returns the preset and both bands; a write is
# {"preset": "custom", "customEqualization": {low, high}} with both bands.
EQUALIZER = "/systems/current/settings/audio/equalizer"
# Hard bounds, whatever gainRange the firmware announces (-6..+6 on DOS 2.19.1).
TONE_GAIN_MIN, TONE_GAIN_MAX = -6, 6

PLAYBACK_ACTIONS = ("play", "pause", "next", "previous")

ALLOWED_POSTS = frozenset({SYSTEM_VOLUME, START_ADVERTISING, EQUALIZER}
                          | {PLAYBACK + a for a in PLAYBACK_ACTIONS})

DEFAULT_TIMEOUT = httpx.Timeout(4.0, connect=2.0)


class IpControlError(Exception):
    """The speaker answered with an {"error": ...} object or a non-2xx status."""

    def __init__(self, code: str, message: str = "", status: int | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.status = status


class SpeakerUnreachable(Exception):
    """Connection error or timeout."""


class ForbiddenCommand(Exception):
    """A write outside ALLOWED_POSTS was requested."""


def parse_body(response: httpx.Response) -> Any:
    """Return the decoded JSON (None for an empty body) or raise IpControlError."""
    text = response.text.strip()
    data: Any = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            if response.is_success:
                raise IpControlError("InvalidJson", text[:200], response.status_code)
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            raise IpControlError(str(err.get("code") or "UnknownError"),
                                 str(err.get("message") or ""), response.status_code)
        raise IpControlError(str(err), "", response.status_code)
    if not response.is_success:
        raise IpControlError(f"HTTP{response.status_code}", text[:200], response.status_code)
    return data


class IpControlClient:
    """One speaker, addressed by IP."""

    def __init__(self, host: str, http: httpx.AsyncClient,
                 port: int = IPCONTROL_DEFAULT_PORT, base_path: str = IPCONTROL_BASE_PATH):
        self.host = host
        self._http = http
        self._base = f"http://{host}:{port}{base_path.rstrip('/')}"

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        kwargs: dict[str, Any] = {}
        if method == "POST":
            # Writes carry Content-Type: application/json; the body may be empty.
            kwargs["headers"] = {"Content-Type": "application/json"}
            kwargs["content"] = b"" if body is None else json.dumps(body).encode()
        try:
            response = await self._http.request(method, self._base + path, **kwargs)
        except httpx.TimeoutException as exc:
            raise SpeakerUnreachable(f"{self.host}: timeout") from exc
        except httpx.TransportError as exc:
            raise SpeakerUnreachable(f"{self.host}: {type(exc).__name__}") from exc
        return parse_body(response)

    async def get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def post(self, path: str, body: Any = None) -> Any:
        if path not in ALLOWED_POSTS:
            raise ForbiddenCommand(path)
        return await self._request("POST", path, body)

    # Typed helpers -------------------------------------------------------
    async def device(self) -> dict:
        return await self.get(DEVICE) or {}

    async def system(self) -> dict:
        return await self.get(SYSTEM) or {}

    async def active_source(self) -> dict | None:
        return await self.get(ACTIVE_SOURCE)

    async def volume(self) -> int | None:
        data = await self.get(SYSTEM_VOLUME)
        value = data.get("volume") if isinstance(data, dict) else None
        return value if isinstance(value, int) else None

    async def set_volume(self, value: int) -> None:
        await self.post(SYSTEM_VOLUME, {"volume": int(value)})

    async def playback(self, action: str) -> None:
        if action not in PLAYBACK_ACTIONS:
            raise ForbiddenCommand(action)
        await self.post(PLAYBACK + action)

    async def bluetooth(self) -> dict:
        return await self.get(BLUETOOTH) or {}

    async def start_advertising(self) -> None:
        await self.post(START_ADVERTISING)

    async def equalizer(self) -> dict:
        return await self.get(EQUALIZER) or {}

    async def set_equalizer(self, low: float, high: float) -> None:
        """Custom preset with both bands, the only equalizer write we send."""
        for gain in (low, high):
            if isinstance(gain, bool) or not isinstance(gain, (int, float)) \
                    or not math.isfinite(gain) or not TONE_GAIN_MIN <= gain <= TONE_GAIN_MAX:
                raise ValueError(f"gain hors limites : {gain!r}")
        await self.post(EQUALIZER, {"preset": "custom", "customEqualization": {
            "low": {"gain": low}, "high": {"gain": high}}})
