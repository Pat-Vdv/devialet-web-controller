"""10-band graphic equalizer, isolated prototype (V2.4.3-A feasibility study).

Pure Python, no dependency, no audio device, no network: nothing here touches
Windows, Equalizer APO or the Phantom. It validates the model the real
implementation would use (reports/graphic-eq-study-v243a.md):

- 10 octave bands 31 Hz .. 16 kHz, -12..+12 dB, presets, bypass, JSON state;
- RBJ "Audio EQ Cookbook" peaking biquads (the filter Equalizer APO's
  `Filter: ON PK` implements), exact cascade response;
- automatic negative preamp so the summed boost never exceeds 0 dB
  (anti-clipping), computed from the real response, not from the slider values;
- generation of the Equalizer APO configuration text for one Windows output (DEVICE_PATTERN);
- an offline processor to check gains and peaks on synthetic signals.
"""
from __future__ import annotations

import cmath
import json
import math
from dataclasses import dataclass, field

BANDS_HZ = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
GAIN_MIN, GAIN_MAX, GAIN_STEP = -12.0, 12.0, 0.5
Q_OCTAVE = 1.41          # one-octave bandwidth between band centres
HEADROOM_DB = 0.5        # margin kept below 0 dBFS on top of the computed boost
DEVICE_PATTERN = "My Phantom System"

PRESETS: dict[str, tuple[float, ...]] = {
    "flat":      (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "rock":      (5, 4, 3, 1, -1, -1, 1, 3, 4, 5),
    "jazz":      (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "classical": (4, 3, 2, 1, 0, 0, 0, 2, 3, 4),
    "vocal":     (-2, -2, -1, 1, 3, 4, 3, 1, 0, -1),
}


class EqValueError(ValueError):
    pass


def validate_gains(gains) -> tuple[float, ...]:
    """Exactly 10 finite numbers in -12..+12, on a 0.5 dB grid."""
    if not isinstance(gains, (list, tuple)) or len(gains) != len(BANDS_HZ):
        raise EqValueError(f"{len(BANDS_HZ)} gains attendus")
    out = []
    for f, g in zip(BANDS_HZ, gains):
        if isinstance(g, bool) or not isinstance(g, (int, float)) or not math.isfinite(g):
            raise EqValueError(f"{f} Hz : valeur numérique attendue")
        if not GAIN_MIN <= g <= GAIN_MAX:
            raise EqValueError(f"{f} Hz : {g} dB hors plage ({GAIN_MIN:g} à {GAIN_MAX:g} dB)")
        if abs(g / GAIN_STEP - round(g / GAIN_STEP)) > 1e-9:
            raise EqValueError(f"{f} Hz : pas de {GAIN_STEP} dB")
        out.append(float(g))
    return tuple(out)


# --- filters -----------------------------------------------------------------------

def peaking(fs: float, f0: float, gain_db: float, q: float = Q_OCTAVE) -> tuple[float, ...]:
    """Normalised (b0, b1, b2, a1, a2) of an RBJ peaking EQ."""
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    cos = math.cos(w0)
    b0, b1, b2 = 1 + alpha * a, -2 * cos, 1 - alpha * a
    a0, a1, a2 = 1 + alpha / a, -2 * cos, 1 - alpha / a
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def cascade(gains, fs: float) -> list[tuple[float, ...]]:
    """One biquad per non-zero band (0 dB bands are exact identities, skipped)."""
    return [peaking(fs, f, g) for f, g in zip(BANDS_HZ, gains) if g != 0 and f < fs / 2]


def response_db(filters, freq: float, fs: float) -> float:
    z = cmath.exp(-1j * 2 * math.pi * freq / fs)
    h = 1 + 0j
    for b0, b1, b2, a1, a2 in filters:
        h *= (b0 + b1 * z + b2 * z * z) / (1 + a1 * z + a2 * z * z)
    return 20 * math.log10(abs(h))


def log_grid(lo: float = 20.0, hi: float = 20000.0, per_octave: int = 48) -> list[float]:
    n = int(math.log2(hi / lo) * per_octave) + 1
    return [lo * 2 ** (i / per_octave) for i in range(n)]


def max_boost_db(gains, fs: float) -> float:
    """Highest point of the real cascade response (bands overlap and add up)."""
    filters = cascade(gains, fs)
    if not filters:
        return 0.0
    return max(response_db(filters, f, fs) for f in log_grid(hi=min(20000.0, fs / 2 * 0.999)))


def auto_preamp_db(gains, fs: float = 48000.0) -> float:
    """Negative preamp that keeps the equalised peak below 0 dB gain."""
    boost = max_boost_db(gains, fs)
    return -round(boost + HEADROOM_DB, 1) if boost > 0 else 0.0


# --- state, presets, persistence -------------------------------------------------------

@dataclass
class EqState:
    enabled: bool = False                 # bypass by default: nothing applied until asked
    preset: str = "flat"
    gains: tuple[float, ...] = field(default_factory=lambda: PRESETS["flat"])

    def to_json(self) -> str:
        return json.dumps({"enabled": self.enabled, "preset": self.preset, "gains": list(self.gains)})

    @classmethod
    def from_json(cls, text: str) -> "EqState":
        """Corrupt or invalid saved state gives the default (bypassed, flat)."""
        try:
            data = json.loads(text)
            return cls(enabled=data.get("enabled") is True,
                       preset=data.get("preset") if data.get("preset") in PRESETS else "custom",
                       gains=validate_gains(data.get("gains")))
        except (ValueError, TypeError, AttributeError):
            return cls()

    def apply_preset(self, name: str) -> None:
        if name not in PRESETS:
            raise EqValueError(f"préréglage inconnu : {name}")
        self.preset, self.gains = name, tuple(float(g) for g in PRESETS[name])

    def set_band(self, index: int, gain: float) -> None:
        gains = list(self.gains)
        gains[index] = gain
        self.gains = validate_gains(gains)
        self.preset = next((n for n, p in PRESETS.items() if tuple(map(float, p)) == self.gains), "custom")

    def reset(self) -> None:
        self.apply_preset("flat")


# --- Equalizer APO configuration ----------------------------------------------------------

def apo_config(state: EqState, fs: float = 48000.0, device: str = DEVICE_PATTERN) -> str:
    """Text for the file Equalizer APO would load. Bypass writes no filter at
    all, so the device goes back to the untouched signal."""
    lines = ["# Généré par le contrôleur Devialet - ne pas modifier à la main",
             f"Device: {device}"]
    if not state.enabled or all(g == 0 for g in state.gains):
        lines.append("# bypass : aucun filtre")
        return "\n".join(lines) + "\n"
    lines.append(f"Preamp: {auto_preamp_db(state.gains, fs):.1f} dB")
    for f, g in zip(BANDS_HZ, state.gains):
        if g != 0:
            lines.append(f"Filter: ON PK Fc {f} Hz Gain {g:.1f} dB Q {Q_OCTAVE:.2f}")
    return "\n".join(lines) + "\n"


# --- offline processing (verification only) -------------------------------------------------

def process(samples: list[float], fs: float, gains, preamp_db: float = 0.0) -> list[float]:
    """Preamp then biquad cascade, transposed direct form II, float64."""
    pre = 10 ** (preamp_db / 20)
    out = [s * pre for s in samples]
    for b0, b1, b2, a1, a2 in cascade(gains, fs):
        z1 = z2 = 0.0
        for i, x in enumerate(out):
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            out[i] = y
    return out


def sine(freq: float, fs: float, seconds: float, amplitude: float = 1.0) -> list[float]:
    return [amplitude * math.sin(2 * math.pi * freq * n / fs) for n in range(int(fs * seconds))]


def peak(samples) -> float:
    return max(abs(s) for s in samples)
