"""Graphic equalizer through Equalizer APO (V2.4.3-B2). Temporary folders only:
nothing here can reach the real Equalizer APO configuration."""
import asyncio
import os
import stat

import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web import graphic_eq as ge
from devialet_web.app import create_app
from devialet_web.graphic_eq import EqSettings, GraphicEq, parse, render, safety_preamp_db
from devialet_web.spectrum import SpectrumHub
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

# devialet-eq.txt as installed by hand in B1 (validated V2-V5 on the Phantom)
B1_FILE = (
    "# Devialet Web Controller - egaliseur 10 bandes (V2.4.3-B)\r\n"
    "# Fichier ecrit par le controleur web : ne pas modifier a la main.\r\n"
    '# devialet-state: {"enabled": true, "preset": "flat", "gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}\r\n'
    "Preamp: 0 dB\r\n"
    + "".join(f"Filter: ON PK Fc {f} Hz Gain 0 dB Q 1.41\r\n"
              for f in (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000))
).encode()
CONFIG_TXT = b"Device: My Phantom System\r\nInclude: devialet-eq.txt\r\n"


@pytest.fixture
def eqdir(tmp_path):
    (tmp_path / "config.txt").write_bytes(CONFIG_TXT)
    (tmp_path / "devialet-eq.txt").write_bytes(B1_FILE)
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def text(d):
    return (d / "devialet-eq.txt").read_bytes()


def g(**bands):
    out = [0.0] * 10
    for k, v in bands.items():
        out[ge.BANDS_HZ.index(int(k[1:]))] = v
    return out


# --- format, metadata, preamp ---------------------------------------------------------------

def test_neutral_render_is_byte_identical_to_b1_file():
    assert render(EqSettings()).replace("\n", "\r\n").encode() == B1_FILE
    assert parse(B1_FILE.decode()) == EqSettings(True, (0.0,) * 10, "flat")


def test_filter_serialization():
    out = render(EqSettings(True, tuple(g(f1000=-12, f4000=2.5)), "custom"))
    assert "Filter: ON PK Fc 1000 Hz Gain -12 dB Q 1.41" in out
    assert "Filter: ON PK Fc 4000 Hz Gain 2.5 dB Q 1.41" in out
    assert out.count("Filter: ON PK") == 10
    assert '"gains": [0, 0, 0, 0, 0, -12, 0, 2.5, 0, 0]' in out
    assert parse(out).gains == tuple(g(f1000=-12, f4000=2.5))


def test_disabled_render_is_neutral_but_keeps_gains():
    out = render(EqSettings(False, tuple(g(f1000=-12)), "custom"))
    assert "Filter:" not in out and "Preamp: 0 dB" in out
    # a "custom" file written before B4 (no "custom" field) recalls its own curve as Custom
    assert parse(out) == EqSettings(False, tuple(g(f1000=-12)), "custom", tuple(g(f1000=-12)))


def test_preamp_flat_and_cuts_stay_at_zero():
    assert safety_preamp_db([0] * 10) == 0.0
    assert safety_preamp_db([-12] * 10) == 0.0
    assert "Preamp: 0 dB" in render(EqSettings(True, tuple(g(f1000=-12)), "custom"))


def test_preamp_follows_real_response_plus_1db():
    assert safety_preamp_db(g(f1000=12)) == pytest.approx(-13.0, abs=0.05)
    assert safety_preamp_db([12] * 10) == pytest.approx(-19.4, abs=0.1)     # overlap: +18.4 dB
    # two adjacent +6 bands add up above the largest slider
    assert safety_preamp_db(g(f1000=6, f2000=6)) < -7.5
    assert "Preamp: -13 dB" in render(EqSettings(True, tuple(g(f1000=12)), "custom"))


@pytest.mark.parametrize("bad", [
    "Preamp: 0 dB\n",                                                     # no metadata
    "# devialet-state: {not json}\nPreamp: 0 dB\n",
    '# devialet-state: {"enabled": "yes", "preset": "flat", "gains": [0,0,0,0,0,0,0,0,0,0]}\n',
    '# devialet-state: {"enabled": true, "preset": "flat", "gains": [0,0,0]}\n',
    '# devialet-state: {"enabled": true, "preset": "flat", "gains": [13,0,0,0,0,0,0,0,0,0]}\n',
    '# devialet-state: {"enabled": true, "preset": "Flat!", "gains": [0,0,0,0,0,0,0,0,0,0]}\n',
    B1_FILE.decode() + '# devialet-state: {"enabled": true, "preset": "flat", "gains": [0,0,0,0,0,0,0,0,0,0]}\n',
])
def test_invalid_metadata_is_unmanaged(bad):
    with pytest.raises(ge.EqUnmanaged):
        parse(bad)


@pytest.mark.parametrize("edit", [
    lambda t: t.replace("Fc 1000 Hz Gain 0 dB", "Fc 1000 Hz Gain -6 dB"),   # filter changed by hand
    lambda t: t.replace("Preamp: 0 dB", "Preamp: -3 dB"),
    lambda t: t + "Filter: ON LSC Fc 200 Hz Gain -12 dB Q 0.71\r\n",          # extra directive
    lambda t: t.replace("Filter: ON PK Fc 16000 Hz Gain 0 dB Q 1.41\r\n", ""),
])
def test_filters_differing_from_metadata_are_inconsistent(edit):
    with pytest.raises(ge.EqInconsistent):
        parse(edit(B1_FILE.decode()))


def test_comment_edits_and_spacing_are_tolerated():
    t = B1_FILE.decode().replace("Preamp: 0 dB", "Preamp:   0   dB") + "# note perso\r\n"
    assert parse(t) == EqSettings()


# --- service: reads and changes ---------------------------------------------------------------

def test_state_reads_existing_file_without_writing(eqdir):
    before = os.stat(eqdir / "devialet-eq.txt").st_mtime_ns
    s = run(GraphicEq(eqdir).state())
    assert (s["status"], s["enabled"], s["gains"], s["preamp"]) == ("ok", True, [0.0] * 10, 0.0)
    assert s["processing"] == "unverified"                     # a file never proves processing
    assert os.stat(eqdir / "devialet-eq.txt").st_mtime_ns == before


def test_set_one_band(eqdir):
    s = run(GraphicEq(eqdir).set_bands({1000: -12}))
    assert s["gains"] == g(f1000=-12) and s["preset"] == "custom" and s["preamp"] == 0.0
    assert b"Filter: ON PK Fc 1000 Hz Gain -12 dB Q 1.41\r\n" in text(eqdir)


def test_set_several_bands_keeps_the_others(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_bands({31: 3}))
    s = run(eq.set_bands({1000: -6, 4000: 2.5}))
    assert s["gains"] == g(f31=3, f1000=-6, f4000=2.5)
    assert s["preamp"] < 0                                     # positive gain compensated


def test_set_all_gains(eqdir):
    s = run(GraphicEq(eqdir).set_gains(g(f62=4, f8000=-3)))
    assert s["gains"] == g(f62=4, f8000=-3)
    assert parse(text(eqdir).decode()).gains == tuple(g(f62=4, f8000=-3))


@pytest.mark.parametrize("bands", [{1001: 0}, {1000: 12.5}, {1000: -13}, {1000: 0.3},
                                   {1000: float("nan")}, {}])
def test_invalid_bands_refused_and_file_untouched(eqdir, bands):
    with pytest.raises(ge.EqValueError):
        run(GraphicEq(eqdir).set_bands(bands))
    assert text(eqdir) == B1_FILE


def test_disable_is_neutral_and_enable_restores(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_bands({1000: -12, 4000: 6}))
    with pytest.raises(ge.EqConfirm):                          # preamp -7 dB: bypass would add 7 dB
        run(eq.set_enabled(False))
    assert b"Fc 4000 Hz Gain 6 dB" in text(eqdir)
    off = run(eq.set_enabled(False, confirm=True))
    assert off["enabled"] is False and off["preamp"] == 0.0 and off["gains"] == g(f1000=-12, f4000=6)
    assert b"Filter:" not in text(eqdir)
    on = run(eq.set_enabled(True))
    assert on["gains"] == g(f1000=-12, f4000=6) and on["preamp"] < 0
    assert b"Fc 4000 Hz Gain 6 dB" in text(eqdir)


def test_bands_changed_while_disabled_stay_neutral(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_enabled(False))
    s = run(eq.set_bands({125: 3}))
    assert s["enabled"] is False and b"Filter:" not in text(eqdir)


def test_reset_zeroes_and_enables(eqdir):
    eq = GraphicEq(eqdir)
    run(eq.set_bands({1000: -12}))
    run(eq.set_enabled(False, confirm=True))                  # lifting a -12 dB cut is a +12 dB rise
    s = run(eq.reset())
    assert (s["enabled"], s["gains"], s["preset"]) == (True, [0.0] * 10, "flat")
    # back to the validated neutral directives; the manual curve stays recallable as Custom
    assert ge._directives(text(eqdir).decode()) == ge._directives(B1_FILE.decode())
    assert s["custom"] == g(f1000=-12)


def test_no_write_when_nothing_changes(eqdir):
    before = os.stat(eqdir / "devialet-eq.txt").st_mtime_ns
    run(GraphicEq(eqdir).set_bands({1000: 0}))
    run(GraphicEq(eqdir).reset())
    assert os.stat(eqdir / "devialet-eq.txt").st_mtime_ns == before


def test_persistence_after_simulated_restart(eqdir):
    run(GraphicEq(eqdir).set_bands({250: -4.5}))
    run(GraphicEq(eqdir).set_enabled(False))
    s = run(GraphicEq(eqdir).state())                          # new instance = restarted backend
    assert (s["enabled"], s["gains"]) == (False, g(f250=-4.5))


# --- file problems ---------------------------------------------------------------------------

def test_missing_file_reported_and_only_reset_creates_it(eqdir):
    (eqdir / "devialet-eq.txt").unlink()
    eq = GraphicEq(eqdir)
    s = run(eq.state())
    assert s["status"] == "missing" and s["available"] is False and s["gains"] is None
    with pytest.raises(ge.EqMissing):
        run(eq.set_bands({1000: -3}))
    assert not (eqdir / "devialet-eq.txt").exists()
    run(eq.reset())
    assert text(eqdir) == B1_FILE


def test_missing_folder_is_unavailable(tmp_path):
    eq = GraphicEq(tmp_path / "absent")
    assert run(eq.state())["status"] == "unavailable"
    with pytest.raises(ge.EqUnavailable):
        run(eq.reset())


def test_unmanaged_file_never_overwritten_without_force(eqdir):
    foreign = b"Preamp: -3 dB\r\nFilter: ON LSC Fc 200 Hz Gain -6 dB Q 0.71\r\n"
    (eqdir / "devialet-eq.txt").write_bytes(foreign)
    eq = GraphicEq(eqdir)
    assert run(eq.state())["status"] == "unmanaged"
    for op in (lambda: eq.set_bands({1000: -3}), lambda: eq.set_enabled(False), lambda: eq.reset()):
        with pytest.raises(ge.EqUnmanaged):
            run(op())
    assert text(eqdir) == foreign
    s = run(eq.reset(force=True))                              # explicit, with a backup
    assert s["backup"] == "devialet-eq.unmanaged.bak"
    assert (eqdir / "devialet-eq.unmanaged.bak").read_bytes() == foreign
    assert text(eqdir) == B1_FILE


def test_inconsistent_file_refused(eqdir):
    edited = B1_FILE.replace(b"Fc 1000 Hz Gain 0 dB", b"Fc 1000 Hz Gain -6 dB")
    (eqdir / "devialet-eq.txt").write_bytes(edited)
    eq = GraphicEq(eqdir)
    s = run(eq.state())
    assert s["status"] == "inconsistent" and "1000" in s["message"]
    with pytest.raises(ge.EqInconsistent):
        run(eq.set_bands({31: 1}))
    assert text(eqdir) == edited


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_read_only_folder_gives_permission_error(eqdir):
    eq = GraphicEq(eqdir)
    eqdir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(ge.EqPermission):
            run(eq.set_bands({1000: -3}))
        assert run(eq.state())["gains"] == [0.0] * 10          # nothing announced as saved
    finally:
        eqdir.chmod(stat.S_IRWXU)
    assert text(eqdir) == B1_FILE


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_unreadable_file_gives_permission_status(eqdir):
    (eqdir / "devialet-eq.txt").chmod(0)
    try:
        assert run(GraphicEq(eqdir).state())["status"] == "permission"
    finally:
        (eqdir / "devialet-eq.txt").chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_failed_replace_cleans_temp_and_keeps_file(eqdir, monkeypatch):
    def boom(src, dst):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(ge.os, "replace", boom)
    with pytest.raises(ge.EqWriteFailed):
        run(GraphicEq(eqdir).set_bands({1000: -3}))
    assert text(eqdir) == B1_FILE
    assert sorted(p.name for p in eqdir.iterdir()) == ["config.txt", "devialet-eq.txt"]


def test_read_back_mismatch_is_a_write_failure(eqdir, monkeypatch):
    real = ge.os.replace

    def corrupt(src, dst):
        real(src, dst)
        with open(dst, "ab") as f:
            f.write(b"x")
    monkeypatch.setattr(ge.os, "replace", corrupt)
    with pytest.raises(ge.EqWriteFailed, match="Relecture"):
        run(GraphicEq(eqdir).set_bands({1000: -3}))


def test_atomic_write_uses_temp_in_same_folder(eqdir, monkeypatch):
    seen, real = [], ge.os.replace

    def spy(src, dst):
        seen.append((os.path.dirname(src), os.path.basename(src), open(src, "rb").read()))
        real(src, dst)
    monkeypatch.setattr(ge.os, "replace", spy)
    run(GraphicEq(eqdir).set_bands({1000: -3}))
    (folder, name, data), = seen
    assert folder == str(eqdir) and name.startswith(".devialet-eq.") and name.endswith(".tmp")
    assert data == text(eqdir) and b"\r\n" in data             # complete content before the swap
    assert sorted(p.name for p in eqdir.iterdir()) == ["config.txt", "devialet-eq.txt"]


def test_config_txt_is_never_written(eqdir):
    eq = GraphicEq(eqdir)
    for op in (eq.set_bands({1000: -3}), eq.set_enabled(False), eq.set_enabled(True),
               eq.set_gains([6] * 10), eq.reset(confirm=True), eq.reset(force=True),
               eq.set_preset("rock"), eq.set_preset("custom", confirm=True)):
        run(op)
    assert (eqdir / "config.txt").read_bytes() == CONFIG_TXT


def test_relative_path_refused():
    with pytest.raises(ValueError):
        GraphicEq("relative/config")


# --- concurrency ---------------------------------------------------------------------------

def test_concurrent_band_changes_all_kept(eqdir):
    eq = GraphicEq(eqdir)

    async def go():
        await asyncio.gather(*(eq.set_bands({f: -1.5 - i}) for i, f in enumerate(ge.BANDS_HZ)))
        return await eq.state()
    s = run(go())
    assert s["gains"] == [-1.5 - i for i in range(10)]


def test_stale_revision_is_a_conflict(eqdir):
    eq = GraphicEq(eqdir)
    rev = run(eq.state())["revision"]
    run(eq.set_bands({31: 2}))                                 # someone else changed it
    with pytest.raises(ge.EqConflict):
        run(eq.set_bands({62: 2}, revision=rev))
    fresh = run(eq.state())
    assert run(eq.set_bands({62: 2}, revision=fresh["revision"]))["gains"] == g(f31=2, f62=2)


# --- diagnostics ---------------------------------------------------------------------------

def test_diagnostics_items(eqdir):
    d = run(GraphicEq(eqdir).diagnostics())
    assert d["configDir"] == {"path": str(eqdir), "present": True, "writable": True}
    assert d["include"] == {"status": "present", "device": "My Phantom System"}
    assert d["file"]["status"] == "ok"
    assert d["processing"]["status"] == "unverified"


def test_diagnostics_include_absent_or_missing(eqdir):
    (eqdir / "config.txt").write_bytes(b"Device: My Phantom System\r\nPreamp: 0 dB\r\n")
    assert run(GraphicEq(eqdir).diagnostics())["include"]["status"] == "absent"
    (eqdir / "config.txt").unlink()
    (eqdir / "devialet-eq.txt").unlink()
    d = run(GraphicEq(eqdir).diagnostics())
    assert d["include"]["status"] == "missing" and d["file"]["status"] == "missing"
    assert d["processing"]["status"] == "unverified"


# --- HTTP API ------------------------------------------------------------------------------

def client(eqdir=None, hub=None):
    return local_client(create_app([LEFT, RIGHT], transport=FakePair().transport(), spectrum=hub,
                                 graphic_eq=GraphicEq(eqdir) if eqdir else None))


def test_default_app_has_no_equalizer():
    with client() as c:
        assert c.get("/api/eq").json()["status"] == "disabled"
        r = c.post("/api/eq/reset")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "disabled"
        assert c.get("/api/eq/status").json()["configured"] is False


def test_api_full_cycle(eqdir):
    with client(eqdir) as c:
        s = c.get("/api/eq").json()
        assert (s["status"], s["gains"], s["min"], s["max"], s["step"]) == ("ok", [0.0] * 10, -12, 12, 0.5)
        assert c.patch("/api/eq/bands/1000", json={"gain": -12}).json()["gains"] == g(f1000=-12)
        assert c.patch("/api/eq/bands", json={"bands": {"4000": 6, "31": 2}}).json()["gains"] == \
            g(f1000=-12, f4000=6, f31=2)
        refused = c.post("/api/eq/disable")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "confirm"
        off = c.post("/api/eq/disable", json={"confirm": True}).json()
        assert off["enabled"] is False and off["preamp"] == 0.0
        on = c.post("/api/eq/enable").json()
        assert on["enabled"] is True and on["gains"] == g(f1000=-12, f4000=6, f31=2)
        assert c.put("/api/eq", json={"gains": [1] * 10, "enabled": False}).json()["enabled"] is False
        r = c.post("/api/eq/reset").json()
        assert (r["gains"], r["enabled"]) == ([0.0] * 10, True)
    assert ge._directives(text(eqdir).decode()) == ge._directives(B1_FILE.decode())


@pytest.mark.parametrize("method,url,body", [
    ("patch", "/api/eq/bands/1000", {"gain": 13}),
    ("patch", "/api/eq/bands/1000", {"gain": "3"}),
    ("patch", "/api/eq/bands/1000", {"gain": True}),
    ("patch", "/api/eq/bands/1000", {"gain": 0.3}),
    ("patch", "/api/eq/bands/999", {"gain": 1}),
    ("patch", "/api/eq/bands/abc", {"gain": 1}),
    ("patch", "/api/eq/bands", {"bands": {}}),
    ("patch", "/api/eq/bands", {"bands": {"1000": "x"}}),
    ("put", "/api/eq", {"gains": [0] * 9}),
    ("put", "/api/eq", {"gains": [0] * 10, "enabled": "yes"}),
    ("post", "/api/eq/reset", {"force": "true"}),
])
def test_api_invalid_requests_are_422(eqdir, method, url, body):
    with client(eqdir) as c:
        assert getattr(c, method)(url, json=body).status_code == 422
    assert text(eqdir) == B1_FILE


def test_api_error_codes(eqdir):
    with client(eqdir) as c:
        rev = c.get("/api/eq").json()["revision"]
        c.patch("/api/eq/bands/31", json={"gain": 1})
        r = c.patch("/api/eq/bands/62", json={"gain": 1, "revision": rev})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "conflict"
        (eqdir / "devialet-eq.txt").write_bytes(b"Preamp: 0 dB\r\n")
        r = c.post("/api/eq/disable")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "unmanaged"
        (eqdir / "devialet-eq.txt").unlink()
        r = c.patch("/api/eq/bands/62", json={"gain": 1})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "missing"
        assert c.get("/api/eq").json()["status"] == "missing"


def test_api_has_no_path_or_raw_command_input(eqdir):
    with client(eqdir) as c:
        r = c.put("/api/eq", json={"gains": [0] * 10, "file": "/etc/passwd", "config": "Preamp: 20 dB"})
        assert r.status_code == 200                           # unknown fields ignored, never used
    assert (eqdir / "config.txt").read_bytes() == CONFIG_TXT
    assert text(eqdir) == B1_FILE


def test_api_status_combines_file_agent_and_device(eqdir):
    hub = SpectrumHub()
    with client(eqdir, hub) as c:
        st = c.get("/api/eq/status").json()
        assert st["configured"] is True and st["file"]["status"] == "ok"
        assert st["include"]["status"] == "present"
        assert st["agent"]["connected"] is False and st["device"]["status"] == "unknown"
        assert st["processing"]["status"] == "unverified"


def test_api_status_device_follows_the_eapo_device_line(eqdir):
    # the expected output is the "Device:" line of config.txt, not a name written in the code
    import json
    import time
    hub = SpectrumHub(agent_hosts=frozenset({"testclient"}))

    def device_status():
        for _ in range(100):
            st = c.get("/api/eq/status").json()["device"]
            if st["status"] != "unknown" and st["name"]:
                return st["status"]
            time.sleep(0.02)
        return st["status"]

    with client(eqdir, hub) as c:
        c.post("/api/spectrum", json={"enabled": True})
        with c.websocket_connect("/api/spectrum/ws") as browser:       # a viewer: capture runs
            browser.receive_json()
            with c.websocket_connect("/api/spectrum/agent") as agent:
                agent.send_text(json.dumps({"type": "hello", "version": "2.3.0"}))
                agent.receive_json()
                agent.send_text(json.dumps({"type": "status", "state": "capturing",
                                            "device": "Haut-parleurs (My Phantom System)", "isDefault": True,
                                            "message": "", "discontinuities": 0, "format": "44100 Hz"}))
                assert device_status() == "available"
                (eqdir / "config.txt").write_bytes(b"Device: Other Speakers\r\nInclude: devialet-eq.txt\r\n")
                assert device_status() == "unavailable"
