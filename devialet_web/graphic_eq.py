"""10-band graphic equalizer through Equalizer APO (V2.4.3-B).

Equalizer APO does the audio processing on the Windows Bluetooth output. Its
config.txt, set up by hand once (reports/graphic-eq-v243b.md, B1), only says
`Device: <output>` / `Include: devialet-eq.txt`. This module writes that one
included file and nothing else: config.txt is only ever read, for diagnosis.

devialet-eq.txt is the single source of truth. Its `# devialet-state: {...}`
comment holds the logical state (enabled, preset, gains, and since B4 the last
manual curve "custom"); the Preamp and Filter
lines below it must be exactly what that state generates. A file without valid
metadata is "unmanaged", one whose filters differ from its metadata is
"inconsistent" (edited by hand): both are reported and never overwritten,
except by an explicit forced reset that keeps a copy of the old content.

Nothing is written at startup. Writes are atomic (temporary file in the same
folder, fsync, os.replace, read back) and serialized by a lock; every change
re-reads the file first, so concurrent band changes never undo each other.

Presets (B4) are fixed gain curves. Discrete actions (preset, reset, enable,
disable) are refused with "confirm" when they would raise the steady-state
level by more than 6 dB at some frequency, until the caller confirms: Equalizer
APO reloads the file as a whole in one to two seconds, so a smooth ramp made of
successive writes cannot be guaranteed (reports/graphic-eq-presets.md).

A present, consistent file does not prove the processing is active (Equalizer
APO may have been dropped by a Windows update or a new pairing): the status
always reports the processing as unverified.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from prototypes.eq10 import (
    BANDS_HZ,
    GAIN_MAX,
    GAIN_MIN,
    GAIN_STEP,
    Q_OCTAVE,
    EqValueError,
    cascade,
    log_grid,
    max_boost_db,
    response_db,
    validate_gains,
)

EQ_FILENAME = "devialet-eq.txt"
CONFIG_FILENAME = "config.txt"                 # read only, never written
DEFAULT_CONFIG_DIR = "/mnt/c/Program Files/EqualizerAPO/config"
SAMPLE_RATE = 44100                            # format of the Bluetooth A2DP output
HEADROOM_DB = 1.0                              # margin above the real response peak (B1)
STATE_PREFIX = "# devialet-state: "
MAX_FILE_BYTES = 64 * 1024
FLAT = (0.0,) * len(BANDS_HZ)
INCLUDE_RE = re.compile(r"^\s*include\s*:\s*devialet-eq\.txt\s*$", re.I)
DEVICE_RE = re.compile(r"^\s*device\s*:", re.I)
PRESET_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
JUMP_CONFIRM_DB = 6.0                          # above this rise, a discrete action asks to confirm
JUMP_GRID = tuple(log_grid(hi=20000.0, per_octave=24))
# Fixed curves (B4), dB per band 31 Hz ... 16 kHz; "custom" is the last manual curve.
PRESETS = {
    "flat":       ("Flat",       FLAT),
    "bass-boost": ("Bass Boost", (4.0, 3.5, 2.5, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
    "vocal":      ("Vocal",      (-1.0, -1.0, -0.5, 0.0, 1.5, 2.5, 2.5, 1.0, 0.0, -1.0)),
    "rock":       ("Rock",       (3.0, 2.5, 1.0, 0.0, -1.0, -1.0, 0.0, 1.0, 2.0, 2.5)),
    "classical":  ("Classical",  (2.0, 1.5, 1.0, 0.0, 0.0, 0.0, 0.0, 0.5, 1.5, 2.0)),
}
CUSTOM, CUSTOM_LABEL = "custom", "Custom"
HEADER = ("# Devialet Web Controller - egaliseur 10 bandes (V2.4.3-B)",
          "# Fichier ecrit par le controleur web : ne pas modifier a la main.")


# --- errors ----------------------------------------------------------------------------

class EqError(Exception):
    """Base error; `code` and `status` give the HTTP answer."""
    code, status = "error", 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class EqUnavailable(EqError):
    code, status = "unavailable", 503     # folder absent: Equalizer APO not installed or path wrong


class EqMissing(EqError):
    code, status = "missing", 409


class EqUnmanaged(EqError):
    code, status = "unmanaged", 409


class EqInconsistent(EqError):
    code, status = "inconsistent", 409


class EqUnreadable(EqError):
    code, status = "unreadable", 500


class EqPermission(EqError):
    code, status = "permission", 500


class EqWriteFailed(EqError):
    code, status = "write_failed", 500


class EqConflict(EqError):
    code, status = "conflict", 409


class EqConfirm(EqError):
    """The action would raise the level too much: repeat it with confirm=true."""
    code, status = "confirm", 409

    def __init__(self, message: str, jump_db: float, revision: str | None):
        super().__init__(message)
        self.extra = {"jumpDb": jump_db, "limitDb": JUMP_CONFIRM_DB, "revision": revision}


# --- model -------------------------------------------------------------------------------

def safety_preamp_db(gains) -> float:
    """Negative preamp from the peak of the real cascade response, plus 1 dB.

    It guarantees no frequency is amplified in steady state. It does not bound
    transient peaks (filter phase can still raise a peak, see the report): that
    would need the sum of |h[n]|, which costs 6 to 27 dB even for pure cuts."""
    boost = max_boost_db(gains, SAMPLE_RATE)
    return -round(boost + HEADROOM_DB, 1) if boost > 1e-6 else 0.0


def preset_for(gains) -> str:
    """Name of the preset with exactly these gains, else "custom"."""
    gains = tuple(float(g) for g in gains)
    return next((name for name, (_, curve) in PRESETS.items() if curve == gains), CUSTOM)


def preset_list() -> list[dict]:
    return [{"id": name, "label": label, "gains": list(curve), "preamp": safety_preamp_db(curve)}
            for name, (label, curve) in PRESETS.items()]


@dataclass(frozen=True)
class EqSettings:
    enabled: bool = True
    gains: tuple[float, ...] = FLAT
    preset: str = "flat"
    custom: tuple[float, ...] | None = None      # last manual curve, recalled by "Custom"

    @property
    def preamp(self) -> float:
        """Preamp written to the file: 0 when bypassed (no filter at all)."""
        return safety_preamp_db(self.gains) if self.enabled else 0.0

    def with_gains(self, gains) -> "EqSettings":
        gains = validate_gains(list(gains))
        preset = preset_for(gains)
        return EqSettings(self.enabled, gains, preset, gains if preset == CUSTOM else self.custom)

    def with_preset(self, name: str) -> "EqSettings":
        if name == CUSTOM:
            if self.custom is None:
                raise EqValueError("aucun réglage personnalisé enregistré")
            return EqSettings(self.enabled, self.custom, CUSTOM, self.custom)
        if name not in PRESETS:
            raise EqValueError(f"préréglage inconnu : {name} (préréglages : {', '.join([*PRESETS, CUSTOM])})")
        return EqSettings(self.enabled, PRESETS[name][1], name, self.custom)


def _plain(gains) -> list:
    return [float(g) if not float(g).is_integer() else int(g) for g in gains]


def level_db(settings: EqSettings) -> tuple[float, ...]:
    """Steady-state gain (preamp + filters) of the file on JUMP_GRID; 0 when bypassed."""
    return _level(settings.gains) if settings.enabled else (0.0,) * len(JUMP_GRID)


@lru_cache(maxsize=64)
def _level(gains: tuple[float, ...]) -> tuple[float, ...]:
    filters, pre = cascade(gains, SAMPLE_RATE), safety_preamp_db(gains)
    return tuple(pre + (response_db(filters, f, SAMPLE_RATE) if filters else 0.0) for f in JUMP_GRID)


def level_jump_db(old: EqSettings, new: EqSettings) -> float:
    """Largest rise of the level, any frequency, when going from old to new (0 if none)."""
    return round(max(0.0, *(b - a for a, b in zip(level_db(old), level_db(new)))), 1)


def _num(value: float) -> str:
    value = round(float(value), 2)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def render(settings: EqSettings) -> str:
    """Full text of devialet-eq.txt (LF; CRLF is applied on write)."""
    state = {"enabled": settings.enabled, "preset": settings.preset, "gains": _plain(settings.gains)}
    if settings.custom is not None:
        state["custom"] = _plain(settings.custom)
    lines = [*HEADER, STATE_PREFIX + json.dumps(state)]
    if not settings.enabled:
        lines.append("# egaliseur desactive : aucun filtre (reglages conserves ci-dessus)")
    lines.append(f"Preamp: {_num(settings.preamp)} dB")
    if settings.enabled:
        lines += [f"Filter: ON PK Fc {f} Hz Gain {_num(g)} dB Q {Q_OCTAVE:.2f}"
                  for f, g in zip(BANDS_HZ, settings.gains)]
    return "\n".join(lines) + "\n"


def _directives(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def parse(text: str) -> EqSettings:
    """Rebuild the settings; raise EqUnmanaged / EqInconsistent."""
    states = [line.strip()[len(STATE_PREFIX):] for line in text.splitlines()
              if line.strip().startswith(STATE_PREFIX.strip())]
    if not states:
        raise EqUnmanaged("Fichier non géré : métadonnées « devialet-state » absentes")
    if len(states) > 1:
        raise EqUnmanaged("Fichier non géré : plusieurs lignes « devialet-state »")
    try:
        data = json.loads(states[0])
        if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
            raise ValueError("enabled")
        preset = data.get("preset")
        if not isinstance(preset, str) or not PRESET_RE.match(preset):
            raise ValueError("preset")
        gains = validate_gains(data.get("gains"))
        custom = validate_gains(data["custom"]) if data.get("custom") is not None else None
        if custom is None and preset == CUSTOM:
            custom = gains                       # file written before B4
        settings = EqSettings(data["enabled"], gains, preset, custom)
    except (ValueError, TypeError) as exc:
        raise EqUnmanaged(f"Fichier non géré : métadonnées invalides ({exc})") from exc
    found, expected = _directives(text), _directives(render(settings))
    if found != expected:
        diff = next((f"« {a} » au lieu de « {b} »" for a, b in zip(found, expected) if a != b),
                    f"{len(found)} lignes actives au lieu de {len(expected)}")
        raise EqInconsistent(f"Filtres différents des métadonnées (modification manuelle ?) : {diff}")
    return settings


def revision_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


# --- service -------------------------------------------------------------------------------

class GraphicEq:
    def __init__(self, config_dir: str | os.PathLike):
        self.dir = Path(config_dir)
        if not self.dir.is_absolute():
            raise ValueError("le dossier de configuration Equalizer APO doit être un chemin absolu")
        self.path = self.dir / EQ_FILENAME
        self._lock = asyncio.Lock()

    # file access (synchronous, run in a thread) ---------------------------------------

    def _read(self) -> bytes:
        if not self.dir.is_dir():
            raise EqUnavailable(f"Dossier Equalizer APO introuvable : {self.dir}")
        try:
            if self.path.stat().st_size > MAX_FILE_BYTES:
                raise EqUnmanaged("Fichier non géré : taille anormale")
            return self.path.read_bytes()
        except FileNotFoundError as exc:
            raise EqMissing(f"{EQ_FILENAME} absent : aucun filtre appliqué (son neutre)") from exc
        except PermissionError as exc:
            raise EqPermission(f"Lecture refusée : {self.path}") from exc
        except OSError as exc:
            raise EqUnreadable(f"{EQ_FILENAME} illisible : {exc}") from exc

    @staticmethod
    def _decode(data: bytes) -> str:
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("latin-1")

    def _load(self) -> tuple[EqSettings, str]:
        data = self._read()
        return parse(self._decode(data)), revision_of(data)

    def _write(self, text: str) -> str:
        """Atomic replace of devialet-eq.txt only; returns the new revision."""
        assert self.path.name == EQ_FILENAME and self.path.parent == self.dir
        data = text.replace("\n", "\r\n").encode("ascii")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".devialet-eq.", suffix=".tmp", dir=self.dir)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            tmp = None
        except PermissionError as exc:
            raise EqPermission(f"Écriture refusée dans {self.dir}") from exc
        except OSError as exc:
            raise EqWriteFailed(f"Écriture de {EQ_FILENAME} impossible : {exc}") from exc
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        try:
            written = self.path.read_bytes()
        except OSError as exc:
            raise EqWriteFailed(f"Relecture après écriture impossible : {exc}") from exc
        if written != data:
            raise EqWriteFailed("Relecture après écriture différente du contenu envoyé")
        return revision_of(data)

    def _backup(self, data: bytes) -> str:
        name = EQ_FILENAME.replace(".txt", ".unmanaged.bak")
        try:
            (self.dir / name).write_bytes(data)
        except OSError as exc:
            raise EqWriteFailed(f"Sauvegarde de l'ancien fichier impossible : {exc}") from exc
        return name

    # public API ----------------------------------------------------------------------------

    def payload(self, settings: EqSettings | None, revision: str | None,
                error: EqError | None = None) -> dict:
        base = {"status": error.code if error else "ok", "available": error is None,
                "message": error.message if error else None,
                "bands": list(BANDS_HZ), "min": GAIN_MIN, "max": GAIN_MAX, "step": GAIN_STEP,
                "file": str(self.path), "revision": revision, "processing": "unverified",
                "presets": preset_list(), "jumpLimitDb": JUMP_CONFIRM_DB}
        if settings is None:
            return {**base, "enabled": None, "preset": None, "gains": None, "custom": None,
                    "preamp": None, "safetyPreamp": None}
        return {**base, "enabled": settings.enabled, "preset": settings.preset,
                "gains": list(settings.gains), "preamp": settings.preamp,
                "safetyPreamp": safety_preamp_db(settings.gains),
                "custom": list(settings.custom) if settings.custom is not None else None}

    async def state(self) -> dict:
        """Current settings; file problems are part of the answer, not errors."""
        try:
            settings, revision = await asyncio.to_thread(self._load)
            return self.payload(settings, revision)
        except EqError as exc:
            return self.payload(None, None, exc)

    async def _mutate(self, change, revision: str | None = None, *, allow_missing: bool = False,
                      force: bool = False, guard: bool = False, confirm: bool = False) -> dict:
        """guard: discrete action, refused with EqConfirm above JUMP_CONFIRM_DB
        unless confirm. Fader moves are not guarded: the listener drives them."""
        async with self._lock:
            current, backup = None, None
            try:
                current, rev = await asyncio.to_thread(self._load)
            except EqMissing:
                if not allow_missing:
                    raise
                rev = None
            except (EqUnmanaged, EqInconsistent):
                if not force:
                    raise
                backup = await asyncio.to_thread(self._backup, await asyncio.to_thread(self._read))
                rev = None
            if revision is not None and revision != rev:
                raise EqConflict("Réglages modifiés entre-temps : rechargez l'état avant de recommencer")
            new = change(current or EqSettings())
            if current is not None and new == current:
                return self.payload(current, rev)          # nothing to write
            if guard and current is not None and not confirm:
                jump = await asyncio.to_thread(level_jump_db, current, new)
                if jump > JUMP_CONFIRM_DB:
                    raise EqConfirm(f"Hausse de niveau prévisible : +{jump:g} dB sur certaines fréquences "
                                    f"(au-delà de {JUMP_CONFIRM_DB:g} dB). Baissez le volume si besoin, "
                                    "puis confirmez.", jump, rev)
            new_rev = await asyncio.to_thread(self._write, render(new))
            out = self.payload(new, new_rev)
            if backup:
                out["backup"] = backup
            return out

    async def set_gains(self, gains, enabled: bool | None = None, revision: str | None = None) -> dict:
        gains = validate_gains(list(gains))

        def change(s: EqSettings) -> EqSettings:
            s = s.with_gains(gains)
            return EqSettings(s.enabled if enabled is None else enabled, s.gains, s.preset)
        return await self._mutate(change, revision)

    async def set_bands(self, bands: dict, revision: str | None = None) -> dict:
        """bands: {frequency (int): gain}; the other bands keep their value."""
        if not bands:
            raise EqValueError("aucune bande indiquée")
        index = {}
        for freq, gain in bands.items():
            if freq not in BANDS_HZ:
                raise EqValueError(f"bande inconnue : {freq} Hz (bandes : {', '.join(map(str, BANDS_HZ))})")
            index[BANDS_HZ.index(freq)] = gain

        def change(s: EqSettings) -> EqSettings:
            gains = list(s.gains)
            for i, g in index.items():
                gains[i] = g
            return s.with_gains(gains)
        validate_gains([index.get(i, 0.0) for i in range(len(BANDS_HZ))])   # fail before locking
        return await self._mutate(change, revision)

    async def set_enabled(self, enabled: bool, revision: str | None = None, confirm: bool = False) -> dict:
        """Bypass keeps the gains in the metadata; enabling restores them."""
        return await self._mutate(lambda s: EqSettings(enabled, s.gains, s.preset, s.custom), revision,
                                  guard=True, confirm=confirm)

    async def set_preset(self, name: str, revision: str | None = None, confirm: bool = False) -> dict:
        """Apply a fixed curve, or "custom" (the last manual curve). The on/off
        state is kept: a preset chosen while bypassed is heard on enabling."""
        if name != CUSTOM and name not in PRESETS:
            EqSettings().with_preset(name)                         # raises EqValueError
        return await self._mutate(lambda s: s.with_preset(name), revision, guard=True, confirm=confirm)

    async def reset(self, revision: str | None = None, force: bool = False, confirm: bool = False) -> dict:
        """All bands to 0 dB (enabled, flat). Creates a missing file; replaces an
        unmanaged or inconsistent one only with force, after a backup. The
        custom curve is kept, so "Custom" can bring it back."""
        return await self._mutate(lambda s: EqSettings(True, FLAT, "flat", s.custom), revision,
                                  allow_missing=True, force=force, guard=True, confirm=confirm)

    def _include_status(self) -> dict:
        path = self.dir / CONFIG_FILENAME
        try:
            lines = self._decode(path.read_bytes()).splitlines()
        except FileNotFoundError:
            return {"status": "missing", "device": None}
        except OSError:
            return {"status": "unreadable", "device": None}
        include = next((i for i, l in enumerate(lines) if INCLUDE_RE.match(l)), None)
        devices = [l.split(":", 1)[1].strip() for l in lines[:include or 0] if DEVICE_RE.match(l)]
        return {"status": "present" if include is not None else "absent",
                "device": devices[-1] if devices else None}

    async def diagnostics(self) -> dict:
        """What can be checked from here, item by item. Processing is never
        claimed: only listening or an analyzer measurement can confirm it."""
        state = await self.state()
        folder = await asyncio.to_thread(self.dir.is_dir)
        include = await asyncio.to_thread(self._include_status) if folder else \
            {"status": "unavailable", "device": None}
        writable = folder and await asyncio.to_thread(os.access, self.dir, os.W_OK)
        return {"configDir": {"path": str(self.dir), "present": folder, "writable": bool(writable)},
                "include": include,
                "file": {"path": str(self.path), "status": state["status"], "message": state["message"],
                         "revision": state["revision"]},
                "processing": {"status": "unverified",
                               "message": "Traitement non vérifiable automatiquement : écoute ou mesure "
                                          "de l'analyseur nécessaire (rapport V2.4.3-B)"}}
