"""V2.4.3-A isolated 10-band EQ prototype: model, filters, anti-clipping, APO text.
No audio device, no Windows change, no speaker command."""
import json
import math

import pytest

from prototypes.eq10 import (
    BANDS_HZ, PRESETS, EqState, EqValueError, apo_config, auto_preamp_db, cascade, max_boost_db,
    peak, process, response_db, sine, validate_gains,
)

FS = 48000


def test_ten_octave_bands():
    assert BANDS_HZ == (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


@pytest.mark.parametrize("bad", [[0] * 9, [0] * 11, [13] + [0] * 9, [-12.5] + [0] * 9,
                                 [0.3] + [0] * 9, [float("nan")] + [0] * 9, [True] + [0] * 9,
                                 ["1"] + [0] * 9, None])
def test_validation_rejects(bad):
    with pytest.raises(EqValueError):
        validate_gains(bad)


def test_validation_accepts_bounds_and_half_steps():
    assert validate_gains([-12, 12, 0.5, -0.5, 0, 0, 0, 0, 0, 0])[:2] == (-12.0, 12.0)


@pytest.mark.parametrize("band", range(10))
def test_each_band_applies_its_gain_at_its_centre(band):
    gains = [0] * 10
    gains[band] = 6
    f = BANDS_HZ[band]
    assert response_db(cascade(gains, FS), f, FS) == pytest.approx(6, abs=0.01)


def test_bands_are_independent_far_from_their_centre():
    gains = [0] * 10
    gains[0] = 12                                   # 31 Hz
    assert abs(response_db(cascade(gains, FS), 1000, FS)) < 0.05


def test_flat_and_zero_bands_are_exact_bypass():
    assert cascade(PRESETS["flat"], FS) == []
    x = sine(440, FS, 0.05, 0.7)
    assert process(x, FS, PRESETS["flat"]) == x


def test_offline_processing_matches_the_model():
    gains = [0] * 10
    gains[5] = 12                                   # 1 kHz +12 dB
    x = sine(1000, FS, 0.2, 0.1)
    y = process(x, FS, gains)
    steady = y[FS // 10:]
    assert 20 * math.log10(peak(steady) / 0.1) == pytest.approx(12, abs=0.05)


def test_overlapping_boosts_add_up_so_preamp_uses_real_response():
    all_up = [12] * 10
    assert max_boost_db(all_up, FS) > 18             # more than any single slider
    assert auto_preamp_db(all_up, FS) <= -18.4


@pytest.mark.parametrize("name", list(PRESETS))
def test_auto_preamp_prevents_clipping_full_scale_sweep(name):
    gains = PRESETS[name]
    pre = auto_preamp_db(gains, FS)
    for f in (31, 62, 125, 1000, 4000, 8000, 16000):  # full-scale sine at each band centre
        y = process(sine(f, FS, 0.25, 1.0), FS, gains, pre)
        assert peak(y[FS // 8:]) <= 1.0, (name, f)


def test_cuts_need_no_preamp():
    assert auto_preamp_db([-6] * 10, FS) == 0.0


def test_presets_exist_and_are_valid():
    assert set(PRESETS) == {"flat", "rock", "jazz", "classical", "vocal"}
    for gains in PRESETS.values():
        validate_gains(list(gains))


def test_state_presets_band_change_reset_and_persistence():
    s = EqState()
    assert s.enabled is False and s.preset == "flat"     # bypassed by default
    s.apply_preset("rock")
    s.set_band(5, 2.5)
    assert s.preset == "custom" and s.gains[5] == 2.5
    restored = EqState.from_json(s.to_json())
    assert restored == s
    s.reset()
    assert s.gains == tuple(map(float, PRESETS["flat"])) and s.preset == "flat"
    with pytest.raises(EqValueError):
        s.set_band(0, 20)
    with pytest.raises(EqValueError):
        s.apply_preset("metal")


@pytest.mark.parametrize("text", ["{not json", "[]", json.dumps({"gains": [99] * 10}), "null"])
def test_corrupt_saved_state_gives_safe_default(text):
    assert EqState.from_json(text) == EqState()


def test_apo_config_targets_the_device_with_preamp_and_peaking_filters():
    s = EqState(enabled=True)
    s.apply_preset("vocal")
    text = apo_config(s)
    lines = text.splitlines()
    assert "Device: My Phantom System" in lines
    pre = next(l for l in lines if l.startswith("Preamp:"))
    assert float(pre.split()[1]) == auto_preamp_db(s.gains)
    filters = [l for l in lines if l.startswith("Filter:")]
    assert len(filters) == 10 - s.gains.count(0)
    assert "Filter: ON PK Fc 1000 Hz Gain 4.0 dB Q 1.41" in filters


def test_apo_config_bypass_writes_no_filter():
    s = EqState(enabled=False)
    s.apply_preset("rock")
    text = apo_config(s)
    assert "Filter" not in text and "Preamp" not in text and "Device: My Phantom System" in text
    assert "Filter" not in apo_config(EqState(enabled=True))      # flat = bypass


def test_16k_band_valid_at_44_1_khz():
    gains = [0] * 9 + [6]
    assert response_db(cascade(gains, 44100), 16000, 44100) == pytest.approx(6, abs=0.01)
