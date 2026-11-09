"""V2.4.3-B4: equalizer presets and the level-jump guard (temporary folders only).

Every test writes in pytest's tmp_path: the real Equalizer APO folder is never
reached (create_app has no equalizer unless one is given)."""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web import graphic_eq as ge
from devialet_web.app import create_app
from devialet_web.graphic_eq import (CUSTOM, FLAT, JUMP_CONFIRM_DB, PRESETS, EqSettings, GraphicEq,
                                     level_db, level_jump_db, parse, render, safety_preamp_db)
from prototypes.eq10 import GAIN_MAX, GAIN_MIN
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)
from tests.test_graphic_eq import B1_FILE, CONFIG_TXT, eqdir, g, run, text  # noqa: F401 (fixture)

NAMES = ["flat", "bass-boost", "vocal", "rock", "classical"]
# documented in reports/graphic-eq-presets.md (Courbes)
DOCUMENTED = {
    "flat":       ((0, 0, 0, 0, 0, 0, 0, 0, 0, 0), 0.0),
    "bass-boost": ((4, 3.5, 2.5, 1, 0, 0, 0, 0, 0, 0), -5.8),
    "vocal":      ((-1, -1, -0.5, 0, 1.5, 2.5, 2.5, 1, 0, -1), -4.3),
    "rock":       ((3, 2.5, 1, 0, -1, -1, 0, 1, 2, 2.5), -4.5),
    "classical":  ((2, 1.5, 1, 0, 0, 0, 0, 0.5, 1.5, 2), -3.3),
}
BIG = g(f1000=12)                       # preamp -13 dB: leaving it raises the rest by 13 dB


def settings(name, enabled=True):
    return EqSettings(enabled, PRESETS[name][1], name)


# --- the curves ----------------------------------------------------------------------------

def test_presets_match_the_documented_values():
    assert list(PRESETS) == NAMES
    assert [label for label, _ in PRESETS.values()] == ["Flat", "Bass Boost", "Vocal", "Rock", "Classical"]
    for name, (gains, preamp) in DOCUMENTED.items():
        assert PRESETS[name][1] == tuple(float(x) for x in gains), name
        assert safety_preamp_db(gains) == preamp, name


@pytest.mark.parametrize("name", NAMES)
def test_each_preset_is_valid_and_never_amplifies(name):
    gains = PRESETS[name][1]
    assert len(gains) == len(ge.BANDS_HZ) and all(GAIN_MIN <= x <= GAIN_MAX for x in gains)
    assert all((x * 2).is_integer() for x in gains)                       # 0.5 dB step
    assert max(level_db(settings(name))) <= (0.0 if name == "flat" else -1.0 + 0.05)   # 1 dB margin kept
    assert ge.preset_for(gains) == name


def test_switching_between_presets_never_needs_a_confirmation():
    states = [settings(n) for n in NAMES] + [EqSettings(False, FLAT)]
    for a in states:
        for b in states:
            assert level_jump_db(a, b) <= JUMP_CONFIRM_DB, (a.preset, b.preset, a.enabled, b.enabled)


def test_level_jump_measures_rises_only():
    assert level_jump_db(settings("flat"), settings("bass-boost")) == 0.0      # the preamp only lowers
    assert level_jump_db(settings("bass-boost"), settings("flat")) == 5.8
    big = EqSettings(True, tuple(BIG))
    assert level_jump_db(big, EqSettings(False, tuple(BIG))) == 13.0           # bypass removes -13 dB preamp
    assert level_jump_db(EqSettings(True, tuple(g(f1000=-12))), settings("flat")) == 12.0   # a cut lifted


# --- service ------------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_apply_each_preset(eqdir, name):
    s = run(GraphicEq(eqdir).set_preset(name))
    assert (s["preset"], s["gains"], s["enabled"]) == (name, list(PRESETS[name][1]), True)
    assert s["preamp"] == safety_preamp_db(PRESETS[name][1]) == s["safetyPreamp"]
    body = text(eqdir).decode()
    assert parse(body) == EqSettings(True, PRESETS[name][1], name)              # file = state
    assert f"Preamp: {ge._num(s['preamp'])} dB" in body and body.count("Filter: ON PK") == 10


def test_back_to_flat_from_every_preset(eqdir):
    eq = GraphicEq(eqdir)
    for name in NAMES[1:]:
        run(eq.set_preset(name))
        s = run(eq.set_preset("flat"))                     # no confirmation needed
        assert (s["preset"], s["gains"], s["preamp"]) == ("flat", [0.0] * 10, 0.0)
    assert ge._directives(text(eqdir).decode()) == ge._directives(B1_FILE.decode())


def test_manual_change_becomes_custom_unless_it_matches_a_preset(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_preset("rock"))
    s = run(eq.set_bands({1000: 0}))
    assert s["preset"] == CUSTOM and s["custom"] == s["gains"]
    s = run(eq.set_bands({1000: -1}))                      # exactly Rock again
    assert s["preset"] == "rock" and s["custom"] != s["gains"]
    s = run(eq.set_gains(list(PRESETS["classical"][1])))
    assert s["preset"] == "classical"


def test_custom_recalls_the_last_manual_curve(eqdir):
    eq = GraphicEq(eqdir)
    with pytest.raises(ge.EqValueError):
        run(eq.set_preset(CUSTOM))                         # nothing recorded yet
    run(eq.set_bands({125: 3, 4000: -2}))
    run(eq.set_preset("vocal"))
    run(eq.reset())
    s = run(eq.set_preset(CUSTOM))
    assert (s["preset"], s["gains"]) == (CUSTOM, g(f125=3, f4000=-2))


def test_unknown_preset_refused_and_file_untouched(eqdir):
    for name in ("loudness", "", "../config"):
        with pytest.raises(ge.EqValueError):
            run(GraphicEq(eqdir).set_preset(name))
    assert text(eqdir) == B1_FILE


def test_preset_and_custom_persist_after_restart(eqdir):
    run(GraphicEq(eqdir).set_bands({62: 2}))
    run(GraphicEq(eqdir).set_preset("rock"))
    s = run(GraphicEq(eqdir).state())                      # a new service = server restarted
    assert (s["preset"], s["gains"], s["custom"]) == ("rock", list(PRESETS["rock"][1]), g(f62=2))


def test_file_written_before_b4_is_read_without_writing(eqdir):
    old = render(EqSettings(True, tuple(g(f31=-0.5)), CUSTOM))      # custom=None: B3 metadata
    assert '"custom": [' not in old
    (eqdir / "devialet-eq.txt").write_bytes(old.replace("\n", "\r\n").encode())
    before = os.stat(eqdir / "devialet-eq.txt").st_mtime_ns
    s = run(GraphicEq(eqdir).state())
    assert (s["status"], s["preset"], s["custom"]) == ("ok", CUSTOM, g(f31=-0.5))
    assert os.stat(eqdir / "devialet-eq.txt").st_mtime_ns == before


def test_preset_while_bypassed_stays_neutral_until_enabled(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_enabled(False))
    s = run(eq.set_preset("bass-boost"))
    assert (s["enabled"], s["preset"], s["preamp"]) == (False, "bass-boost", 0.0)
    assert b"Filter:" not in text(eqdir)
    s = run(eq.set_enabled(True))
    assert s["preamp"] == -5.8 and b"Fc 31 Hz Gain 4 dB" in text(eqdir)


def test_rapid_preset_changes_are_serialized(eqdir):
    eq = GraphicEq(eqdir)

    async def burst():
        return await asyncio.gather(*(eq.set_preset(n) for n in NAMES * 4))
    results = run(burst())
    assert [r["preset"] for r in results] == NAMES * 4      # applied in order, none lost
    assert parse(text(eqdir).decode()) == EqSettings(True, PRESETS["classical"][1], "classical")
    assert not [p for p in os.listdir(eqdir) if p.endswith(".tmp")]


def test_stale_revision_is_a_conflict(eqdir):
    eq = GraphicEq(eqdir)
    rev = run(eq.state())["revision"]
    run(eq.set_preset("rock"))
    with pytest.raises(ge.EqConflict):
        run(eq.set_preset("vocal", revision=rev))
    assert run(eq.state())["preset"] == "rock"


def test_large_rise_asks_for_confirmation(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_gains(BIG))
    for action in (lambda **k: eq.set_preset("flat", **k), lambda **k: eq.reset(**k),
                   lambda **k: eq.set_enabled(False, **k)):
        with pytest.raises(ge.EqConfirm) as err:
            run(action())
        assert err.value.extra["jumpDb"] == 13.0 and err.value.extra["limitDb"] == JUMP_CONFIRM_DB
        assert err.value.extra["revision"] == run(eq.state())["revision"]
        assert run(eq.state())["gains"] == BIG                                  # nothing written
    s = run(eq.set_preset("flat", confirm=True, revision=run(eq.state())["revision"]))
    assert s["preset"] == "flat"


def test_lowering_actions_and_faders_are_never_guarded(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_enabled(False))
    run(eq.set_gains(BIG))                                  # bypassed: inaudible
    assert run(eq.set_enabled(True))["preamp"] == -13.0     # enabling only lowers the level
    assert run(eq.set_bands({1000: 0}))["gains"] == [0.0] * 10    # a fader move: the listener drives it


def test_write_error_keeps_the_previous_file(eqdir, monkeypatch):
    def broken(*a, **k):
        raise OSError("disque plein")
    monkeypatch.setattr(ge.os, "replace", broken)
    with pytest.raises(ge.EqWriteFailed):
        run(GraphicEq(eqdir).set_preset("rock"))
    assert text(eqdir) == B1_FILE
    assert not [p for p in os.listdir(eqdir) if p.endswith(".tmp")]


def test_config_txt_untouched_by_presets(eqdir):
    eq = GraphicEq(eqdir)
    for name in NAMES + [CUSTOM]:
        if name == CUSTOM:
            run(eq.set_bands({500: 1}))
        run(eq.set_preset(name))
    assert (eqdir / "config.txt").read_bytes() == CONFIG_TXT


# --- HTTP API ------------------------------------------------------------------------------

def make_client(eqdir=None, pair=None):
    pair = pair or FakePair()
    return local_client(create_app([LEFT, RIGHT], transport=pair.transport(),
                                 graphic_eq=GraphicEq(eqdir) if eqdir else None))


def test_api_lists_presets_even_without_equalizer(eqdir):
    for d in (None, eqdir):
        with make_client(d) as c:
            s = c.get("/api/eq").json()
            assert [p["id"] for p in s["presets"]] == NAMES and s["jumpLimitDb"] == JUMP_CONFIRM_DB
            assert s["presets"][1] == {"id": "bass-boost", "label": "Bass Boost",
                                       "gains": list(PRESETS["bass-boost"][1]), "preamp": -5.8}


def test_api_preset_cycle_never_touches_the_phantoms(eqdir):
    pair = FakePair()
    with make_client(eqdir, pair) as c:
        before = len(pair.posts)
        for name in NAMES:
            assert c.post("/api/eq/preset", json={"preset": name}).json()["preset"] == name
        mine = c.patch("/api/eq/bands/250", json={"gain": 2}).json()["gains"]   # Classical + 250 Hz
        c.post("/api/eq/preset", json={"preset": "flat"})
        assert c.post("/api/eq/preset", json={"preset": "custom"}).json()["gains"] == mine
        assert len(pair.posts) == before                    # no BASS / TREBLE / VOLUME request
        assert (pair.eq_preset, pair.eq, pair.volume) == ("flat", {"low": 0, "high": 0}, 31)


def test_api_confirm_flow(eqdir):
    with make_client(eqdir) as c:
        c.put("/api/eq", json={"gains": BIG})
        r = c.post("/api/eq/preset", json={"preset": "flat"})
        assert r.status_code == 409
        d = r.json()["detail"]
        assert (d["code"], d["jumpDb"], d["limitDb"]) == ("confirm", 13.0, JUMP_CONFIRM_DB) and "+13 dB" in d["message"]
        ok = c.post("/api/eq/preset", json={"preset": "flat", "confirm": True, "revision": d["revision"]})
        assert ok.status_code == 200 and ok.json()["preset"] == "flat"
        # confirming an outdated warning is a conflict, not a blind write
        c.put("/api/eq", json={"gains": g(f1000=11.5)})        # changed since the warning
        stale = c.post("/api/eq/reset", json={"confirm": True, "revision": d["revision"]})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "conflict"


@pytest.mark.parametrize("body", [
    {}, {"preset": ""}, {"preset": "Rock"}, {"preset": "../config.txt"}, {"preset": "a" * 40},
    {"preset": "rock", "confirm": "true"}, {"preset": 3},
])
def test_api_preset_invalid_bodies_are_422(eqdir, body):
    with make_client(eqdir) as c:
        assert c.post("/api/eq/preset", json=body).status_code == 422
    assert text(eqdir) == B1_FILE


def test_api_unknown_preset_is_422_and_without_equalizer_503(eqdir):
    with make_client(eqdir) as c:
        r = c.post("/api/eq/preset", json={"preset": "loudness"})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid"
    with make_client() as c:
        assert c.post("/api/eq/preset", json={"preset": "rock"}).status_code == 503
