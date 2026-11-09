"""Native tone control (BASS / TREBLE) of a Phantom II stereo system (V2.4.1).

The firmware exposes a 2-band equalizer at the system level:
band `low` = bass, band `high` = treble,
-6..+6 dB in 1 dB steps. Both speakers of the pair report the same value; the
write goes to the system leader only and always carries both bands, merged
with the values just read, so the other band is kept. Writes are serialised and
spaced so concurrent requests (two tabs, bass and treble) never interleave.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import TYPE_CHECKING, Any

from .ipcontrol import TONE_GAIN_MAX, TONE_GAIN_MIN, IpControlClient, IpControlError, \
    SpeakerUnreachable

if TYPE_CHECKING:
    from .pair import StereoPair

BANDS = {"bass": "low", "treble": "high"}
# Presets whose values equal currentEqualization; others (voice…) must be replaced.
EDITABLE_PRESETS = frozenset({"custom", "flat"})


class ToneValueError(ValueError):
    """Requested gain is not an integer within the announced range."""


def _gain(eq: Any, band: str) -> float | int | None:
    entry = eq.get(band) if isinstance(eq, dict) else None
    value = entry.get("gain") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value) if float(value).is_integer() else round(float(value), 1)


def _number(value: Any, default: float) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok else default


def parse_equalizer(data: Any) -> dict:
    """Normalise GET …/settings/audio/equalizer. `bass`/`treble` are the applied
    values (currentEqualization), None when absent or unsupported ({})."""
    data = data if isinstance(data, dict) else {}
    current = data.get("currentEqualization")
    rng = data.get("gainRange") if isinstance(data.get("gainRange"), dict) else {}
    lo = max(TONE_GAIN_MIN, math.ceil(_number(rng.get("min"), TONE_GAIN_MIN)))
    hi = min(TONE_GAIN_MAX, math.floor(_number(rng.get("max"), TONE_GAIN_MAX)))
    step = _number(rng.get("stepPrecision"), 1.0)
    return {
        "bass": _gain(current, "low"),
        "treble": _gain(current, "high"),
        "preset": data.get("preset") if isinstance(data.get("preset"), str) else None,
        "enabled": data.get("enabled") is not False,
        "min": lo, "max": hi, "step": int(step) if step.is_integer() and step > 0 else 1,
    }


def validate_gain(name: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToneValueError(f"{name} : valeur entière attendue (dB)")
    if not lo <= value <= hi:
        raise ToneValueError(f"{name} : {value} dB hors plage ({lo} à {hi} dB)")
    return value


class ToneController:
    def __init__(self, pair: StereoPair, min_interval: float = 0.2,
                 confirm_attempts: int = 3, confirm_delay: float = 0.25):
        self._pair = pair
        self._lock = asyncio.Lock()
        self._interval = min_interval
        self._attempts = confirm_attempts
        self._confirm_delay = confirm_delay
        self._last_write = 0.0
        self.writes: list[tuple[float, float]] = []

    def _system_leader(self) -> IpControlClient:
        """Writes need the identified system leader, never a fallback speaker."""
        pair = self._pair
        leader = next((c for c in pair.clients
                       if pair.speakers[c.host].reachable and pair.speakers[c.host].is_leader), None)
        if leader is None:
            raise SpeakerUnreachable("leader du système introuvable, réglage refusé")
        return leader

    async def status(self) -> dict:
        """Read-only: applied values from the leader, follower cross-check."""
        pair = self._pair
        await pair.discover()
        leader = pair.leader_client()
        features = pair.system.get("availableFeatures")
        out: dict[str, Any] = {
            "available": False, "systemName": pair.system.get("systemName"),
            "leader": leader.host if leader else None,
            "leaderDetected": any(s.is_leader and s.reachable for s in pair.speakers.values()),
            "bass": None, "treble": None, "preset": None, "enabled": None,
            "min": TONE_GAIN_MIN, "max": TONE_GAIN_MAX, "step": 1,
            "consistent": None, "speakers": [], "errors": [], "updatedAt": time.time(),
        }
        if leader is None:
            out["errors"].append("Aucune enceinte joignable")
            return out
        if isinstance(features, list) and "equalizer" not in features:
            out["errors"].append("Tonalité non prise en charge par ce système")
            return out
        clients = [c for c in pair.clients if pair.speakers[c.host].reachable]
        results = await asyncio.gather(*(c.equalizer() for c in clients), return_exceptions=True)
        values = {}
        for client, result in zip(clients, results):
            entry: dict[str, Any] = {"host": client.host, "bass": None, "treble": None, "error": None}
            if isinstance(result, Exception):
                entry["error"] = str(result)
            else:
                parsed = parse_equalizer(result)
                entry["bass"], entry["treble"] = parsed["bass"], parsed["treble"]
                values[client.host] = parsed
            out["speakers"].append(entry)
        main = values.get(leader.host)
        if main is None:
            error = next((e["error"] for e in out["speakers"] if e["host"] == leader.host), None)
            out["errors"].append(f"Tonalité : {error or 'lecture impossible'}")
            return out
        if main["bass"] is None or main["treble"] is None:
            out["errors"].append("Tonalité non prise en charge par ce système")
            return out
        out.update(main, available=True)
        readable = [v for v in values.values() if v["bass"] is not None]
        out["consistent"] = all((v["bass"], v["treble"]) == (main["bass"], main["treble"])
                                for v in readable)
        if not out["consistent"]:
            out["errors"].append("Valeurs différentes entre les deux enceintes")
        return out

    async def apply(self, bass: int | None = None, treble: int | None = None) -> dict:
        """Set one or both bands, then read back until the leader reports them."""
        if bass is None and treble is None:
            raise ToneValueError("indiquez bass et/ou treble")
        await self._pair.discover()
        leader = self._system_leader()
        async with self._lock:
            current = parse_equalizer(await leader.equalizer())
            if current["bass"] is None or current["treble"] is None:
                raise IpControlError("ToneUnsupported", "tonalité non prise en charge")
            lo, hi = current["min"], current["max"]
            target = {
                "bass": current["bass"] if bass is None else validate_gain("Basses", bass, lo, hi),
                "treble": current["treble"] if treble is None
                else validate_gain("Aigus", treble, lo, hi),
            }
            unchanged = (target["bass"], target["treble"]) == (current["bass"], current["treble"])
            written = not (unchanged and current["preset"] in EDITABLE_PRESETS)
            if written:
                wait = self._interval - (time.monotonic() - self._last_write)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    await leader.set_equalizer(target["bass"], target["treble"])
                finally:
                    self._last_write = time.monotonic()
                self.writes.append((target["bass"], target["treble"]))
            state = await self.status()
            for _ in range(self._attempts - 1):
                if not written or (state["bass"], state["treble"]) == (target["bass"], target["treble"]):
                    break
                await asyncio.sleep(self._confirm_delay)
                state = await self.status()
        state["requested"] = target
        state["written"] = written
        state["confirmed"] = (state["bass"], state["treble"]) == (target["bass"], target["treble"])
        if not state["confirmed"]:
            state["errors"].append("Réglage envoyé mais non confirmé par l'enceinte")
        return state
