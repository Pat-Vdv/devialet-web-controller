"""V2.4.3-A2 validation helpers: test signal, averaging, expectation, verdict.
Offline only; the PowerShell check is guarded to stay read-only."""
import json
import math
import re
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import eq_validation as ev  # noqa: E402


def test_pink_noise_level_and_determinism():
    a = ev.pink_noise(1.0)
    assert a == ev.pink_noise(1.0)                       # seeded: same file every time
    rms = math.sqrt(sum(s * s for s in a) / len(a))
    assert 20 * math.log10(rms) == pytest.approx(-20, abs=0.2)
    assert max(abs(s) for s in a) < 1.0                  # no clipping in the test file


def test_wav_format(tmp_path):
    path = tmp_path / "n.wav"
    ev.write_wav(path, ev.pink_noise(0.2))
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 44100)
        assert w.getnframes() == int(0.2 * 44100)


def test_power_average_ignores_floor_frames():
    assert ev.power_mean_db([-20, -20, -90]) == pytest.approx(-20)
    assert ev.power_mean_db([-90, -90]) == -90
    assert ev.power_mean_db([-10, -20]) == pytest.approx(10 * math.log10((0.1 + 0.01) / 2))


def test_expected_peaking_cut_on_octave_bands():
    exp = ev.expected_pk(125, -12, 1.41)
    assert exp[2] == pytest.approx(-8.8, abs=0.2)        # 125 Hz band
    assert -3.5 < exp[1] < -2 and -3.5 < exp[3] < -2     # neighbours
    assert all(abs(e) < 0.2 for e in exp[6:])            # 2 kHz and above untouched


def frames(l, r=None):
    return {"l": list(l), "r": list(r if r is not None else l)}


def test_verdict_after_eq():
    a = frames([-30.0] * 10)
    exp = ev.expected_pk(125, -12, 1.41)
    b = frames([-30 + e + 0.3 for e in exp])
    assert ev.compare(a, b, exp)["verdict"].startswith("APRÈS")


def test_verdict_before_eq():
    a = frames([-30.0] * 10)
    b = frames([-30.2] * 10)
    assert ev.compare(a, b, ev.expected_pk(125, -12, 1.41))["verdict"].startswith("AVANT")


def test_verdict_preamp_uniform_shift():
    a = frames([-30.0] * 10)
    assert ev.compare(a, frames([-36.1] * 10), [-6.0] * 10)["verdict"].startswith("APRÈS")


def test_verdict_inconclusive():
    a = frames([-30.0] * 10)
    b = frames([-30, -25, -40, -30, -33, -30, -30, -30, -30, -30])
    assert ev.compare(a, b, ev.expected_pk(125, -12, 1.41))["verdict"].startswith("INDÉTERMINÉ")


def test_compare_cli(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(frames([-30.0] * 10)))
    b.write_text(json.dumps(frames([-36.0] * 10)))
    assert ev.main(["compare", str(a), str(b), "--preamp", "-6"]) == 0
    assert "APRÈS" in capsys.readouterr().out


def test_expected_chain_adds_preamp_and_filters():
    exp = ev.expected_chain([(4000, 6, 1.41)], preamp=-6)
    assert exp[7] == pytest.approx(-1.3, abs=0.2)         # 4 kHz band: +4.7 boost under -6 preamp
    assert max(exp) <= 0                                   # preamp covers the boost: no gain overall
    assert ev.expected_chain([(125, -12, 1.41)]) == ev.expected_pk(125, -12, 1.41)


def test_verdict_identity_check():
    a = frames([-30.0] * 10)
    assert ev.compare(a, frames([-30.4] * 10), [0.0] * 10)["verdict"].startswith("IDENTIQUE")
    assert ev.compare(a, frames([-33.0] * 10), [0.0] * 10)["verdict"].startswith("DIFFÉRENT")


def test_compare_cli_preamp_and_pk(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    exp = ev.expected_chain([(4000, 6, 1.41)], preamp=-6)
    a.write_text(json.dumps(frames([-30.0] * 10)))
    b.write_text(json.dumps(frames([-30 + e for e in exp])))
    assert ev.main(["compare", str(a), str(b), "--preamp", "-6", "--pk", "4000", "6", "1.41"]) == 0
    assert "APRÈS" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ev.main(["compare", str(a), str(b)])


def test_powershell_check_is_read_only():
    text = (ROOT / "scripts" / "eapo-check.ps1").read_text(encoding="utf-8-sig")
    for forbidden in (r"Set-Item", r"Set-ItemProperty", r"New-Item", r"Remove-Item", r"New-ItemProperty",
                      r"Out-File", r"Set-Content", r"Add-Content", r"reg(\.exe)? (add|delete)", r"icacls",
                      r"Start-Process", r"-Verb RunAs", r"Copy-Item", r"Move-Item"):
        assert not re.search(forbidden, text, re.I), forbidden
    # no endpoint of a particular PC in the published script: chosen by parameter or name
    assert not re.search(r'\$Guid\s*=\s*"\{', text)
    assert "[string]$EndpointGuid = $env:DEVIALET_EAPO_ENDPOINT" in text and "-DeviceName" in text
