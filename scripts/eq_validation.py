"""Equalizer APO validation helpers (V2.4.3-A2). Nothing here changes Windows,
the backend or the Phantom.

  signal   write a deterministic pink-noise WAV (44.1 kHz, 16 bit, stereo)
  measure  average the analyzer levels (read-only viewer WebSocket of the
           running backend) for N seconds into a JSON file
  compare  band-by-band difference between two measurements, against what a
           pre-loopback or post-loopback equaliser would give

  .venv/bin/python scripts/eq_validation.py signal work/eq-test/pink-noise.wav
  .venv/bin/python scripts/eq_validation.py measure work/eq-test/A-neutre.json
  .venv/bin/python scripts/eq_validation.py compare A.json B.json --preamp -6
  .venv/bin/python scripts/eq_validation.py compare A.json C.json --pk 125 -12 1.41
  .venv/bin/python scripts/eq_validation.py compare A.json E.json --preamp -6 --pk 4000 6 1.41
  .venv/bin/python scripts/eq_validation.py compare A.json F.json --preamp 0   (doit être identique)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import struct
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prototypes.eq10 import peaking, response_db  # noqa: E402

BANDS_HZ = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
FLOOR_DB = -90.0
FS = 44100


# --- test signal -------------------------------------------------------------------

def pink_noise(seconds: float, fs: int = FS, rms_dbfs: float = -20.0, seed: int = 2410) -> list[float]:
    """Paul Kellet's pink filter on seeded white noise, normalised to an RMS level."""
    rng = random.Random(seed)
    b = [0.0] * 7
    out = []
    for _ in range(int(seconds * fs)):
        w = rng.uniform(-1, 1)
        b[0] = 0.99886 * b[0] + w * 0.0555179
        b[1] = 0.99332 * b[1] + w * 0.0750759
        b[2] = 0.96900 * b[2] + w * 0.1538520
        b[3] = 0.86650 * b[3] + w * 0.3104856
        b[4] = 0.55000 * b[4] + w * 0.5329522
        b[5] = -0.7616 * b[5] - w * 0.0168980
        out.append(b[0] + b[1] + b[2] + b[3] + b[4] + b[5] + b[6] + w * 0.5362)
        b[6] = w * 0.115926
    rms = math.sqrt(sum(s * s for s in out) / len(out))
    gain = 10 ** (rms_dbfs / 20) / rms
    return [max(-1.0, min(1.0, s * gain)) for s in out]


def write_wav(path: Path, samples: list[float], fs: int = FS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(fs)
        frames = bytearray()
        for s in samples:
            v = int(round(s * 32767))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


# --- measurement ---------------------------------------------------------------------

def power_mean_db(values: list[float]) -> float:
    live = [v for v in values if v > FLOOR_DB]
    if not live:
        return FLOOR_DB
    return 10 * math.log10(sum(10 ** (v / 10) for v in live) / len(live))


def average_frames(frames: list[dict]) -> dict:
    out = {"frames": len(frames), "bands": list(BANDS_HZ)}
    for ch in ("l", "r"):
        out[ch] = [round(power_mean_db([f[ch][i] for f in frames]), 2) for i in range(len(BANDS_HZ))]
    return out


async def collect(url: str, seconds: float) -> list[dict]:
    import websockets
    frames, status = [], None
    async with websockets.connect(url, open_timeout=5) as ws:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") == "status":
                status = msg
            elif msg.get("type") == "levels":
                frames.append({"l": msg["l"], "r": msg["r"]})
    if not frames:
        reason = (status or {}).get("reason") or "aucune trame reçue"
        raise SystemExit(f"Aucune mesure : {reason}. L'analyseur est-il activé et l'agent lancé ?")
    return frames


# --- expectation and verdict -----------------------------------------------------------

def band_grid(fc: float, per_band: int = 64) -> list[float]:
    lo = fc / math.sqrt(2)
    return [lo * 2 ** (i / per_band) for i in range(per_band + 1)]


def expected_pk(f0: float, gain: float, q: float, fs: float = FS) -> list[float]:
    """Octave-band level change a post-loopback peaking filter gives on pink noise
    (equal power per octave: mean of |H|^2 on a log grid inside each band)."""
    return expected_chain([(f0, gain, q)], fs=fs)


def expected_chain(pks: list, preamp: float = 0.0, fs: float = FS) -> list[float]:
    """Same, for a preamp followed by several peaking filters in series."""
    flt = [peaking(fs, f0, gain, q) for f0, gain, q in pks]
    out = []
    for fc in BANDS_HZ:
        grid = [f for f in band_grid(fc) if f < fs / 2]
        mean = sum(10 ** (response_db(flt, f, fs) / 10) for f in grid) / len(grid)
        out.append(round(10 * math.log10(mean) + preamp, 2))
    return out


def compare(a: dict, b: dict, expected: list[float], tol_post: float = 2.0, tol_pre: float = 1.0) -> dict:
    delta = [round(((b["l"][i] - a["l"][i]) + (b["r"][i] - a["r"][i])) / 2, 2) for i in range(len(BANDS_HZ))]
    err_post = max(abs(d - e) for d, e in zip(delta, expected))
    err_pre = max(abs(d) for d in delta)
    if all(abs(e) < 0.05 for e in expected):             # no change expected: reproducibility check
        verdict = ("IDENTIQUE : écart ≤ 1 dB, mesure reproductible" if err_pre <= tol_pre
                   else "DIFFÉRENT : volume, source ou lecture modifiés ? refaire la mesure")
    elif err_post <= tol_post and err_pre > tol_pre:
        verdict = "APRÈS : la capture loopback voit le signal égalisé"
    elif err_pre <= tol_pre:
        verdict = "AVANT : la capture loopback ne voit pas l'égalisation (ou l'APO n'agit pas)"
    else:
        verdict = "INDÉTERMINÉ : refaire la mesure (même volume, même source, 20 s minimum)"
    return {"delta": delta, "expected": expected, "max_error_vs_post": round(err_post, 2), "verdict": verdict}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("signal")
    s.add_argument("out", type=Path)
    s.add_argument("--seconds", type=float, default=120)
    m = sub.add_parser("measure")
    m.add_argument("out", type=Path)
    m.add_argument("--seconds", type=float, default=20)
    m.add_argument("--url", default="ws://localhost:8000/api/spectrum/ws")
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    c.add_argument("--preamp", type=float, help="préampli testé (dB), ex. -6")
    c.add_argument("--pk", nargs=3, type=float, action="append", default=[], metavar=("FC", "GAIN", "Q"),
                   help="filtre en cloche testé ; répétable")
    args = p.parse_args(argv)
    if args.cmd == "compare" and args.preamp is None and not args.pk:
        p.error("compare : indiquer --preamp et/ou --pk")

    if args.cmd == "signal":
        write_wav(args.out, pink_noise(args.seconds))
        print(f"Écrit : {args.out}  ({args.seconds:g} s, bruit rose, -20 dBFS RMS, 44,1 kHz)")
    elif args.cmd == "measure":
        data = average_frames(asyncio.run(collect(args.url, args.seconds)))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, indent=1))
        print(f"{data['frames']} trames · L {data['l']} · R {data['r']} → {args.out}")
    else:
        a, b = json.loads(args.a.read_text()), json.loads(args.b.read_text())
        expected = expected_chain(args.pk, args.preamp or 0.0)
        res = compare(a, b, expected)
        print(f"{'bande':>6} {'mesuré':>8} {'attendu si APRÈS':>17}")
        for f, d, e in zip(BANDS_HZ, res["delta"], res["expected"]):
            print(f"{f:>6} {d:>+8.1f} {e:>+17.1f}")
        print(res["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
