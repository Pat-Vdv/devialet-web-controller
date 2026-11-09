"""V2.4.3-B3 static checks: equalizer panel markup, wiring, theme tokens, served assets."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web.app import create_app
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

STATIC = Path(__file__).resolve().parents[1] / "devialet_web" / "static"


def eq_css() -> str:
    css = (STATIC / "app.css").read_text()
    start = css.index("/* E. Graphic equalizer")
    return css[start:css.index("/* Responsive", start)]


def test_panel_below_audio_deck_with_controls():
    html = (STATIC / "index.html").read_text()
    assert html.index('id="tone-card"') < html.index('id="eq-card"') < html.index('class="card bt"')
    card = re.search(r'<section class="card tone graphic-eq off" id="eq-card".*?</section>', html, re.S).group(0)
    for needle in ('aria-labelledby="eq-title"', ">Égaliseur graphique<", 'id="eq-pill"',
                   'id="eq-power"', 'aria-pressed="false"', 'id="eq-preamp"', 'id="eq-reset"',
                   'id="eq-board"', 'role="group"', 'aria-describedby="eq-help"',
                   'id="eq-message"', 'role="status"'):
        assert needle in card, needle
    assert "pas sur les autres sources" in card          # scope differs from BASS/TREBLE


def test_scripts_order():
    html = (STATIC / "index.html").read_text()
    assert html.index("tone-core.js") < html.index("eq-core.js") < html.index('src="/static/eq.js?v=')


def test_faders_are_native_accessible_ranges():
    js = (STATIC / "eq.js").read_text()
    assert 'type: "range"' in js and "min: E.MIN, max: E.MAX, step: E.STEP" in js
    assert "aria-label" in js and "aria-valuetext" in js
    for event in ("input", "pointerdown", "pointerup", "dblclick", "visibilitychange"):
        assert f'"{event}"' in js, event
    assert '"wheel"' not in js                          # scrolling the page never moves a fader


def test_wiring_only_uses_equalizer_routes():
    js = (STATIC / "eq.js").read_text()
    routes = set(re.findall(r'"(/api/[^"]*)"', js))
    assert routes == {"/api/eq", "/api/eq/bands", "/api/eq/enable", "/api/eq/disable", "/api/eq/reset",
                      "/api/eq/preset"}
    assert "createToneSync" in js and "bands: E.KEYS" in js and "normalize: E.normalizeGain" in js
    assert "await sync.idle()" in js                     # queued fader moves land before enable/reset


def test_readout_shows_file_values_except_while_sending():
    js = (STATIC / "eq.js").read_text()
    assert "const applied = ok ? s.gains[i] : null" in js
    line = next(l for l in js.splitlines() if "readout.textContent = ok" in l)
    assert "waiting ? target : applied" in line


def test_styles_use_theme_tokens_only():
    css = eq_css()
    for selector in (".eq-board", ".eq-fader", "::-webkit-slider-thumb", "::-moz-range-thumb",
                     ".eq-readout", ".eq-power[aria-pressed=\"true\"]", ".graphic-eq.bypassed", ".graphic-eq.off",
                     ":focus-visible"):
        assert selector in css, selector
    # colours come from the theme tokens; the only literals are the neutral metal / shadow greys
    for colour in re.findall(r"#[0-9a-fA-F]{3,8}\b", css):
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5)) if len(colour) >= 7 else \
            (int(colour[i] * 2, 16) for i in (1, 2, 3))
        assert max(r, g, b) - min(r, g, b) <= 16, f"{colour} n'est pas un gris neutre"
    for token in ("var(--tone-led)", "var(--tone-digit)", "var(--display-bg)", "var(--panel-border)"):
        assert token in css, token
    assert "writing-mode" not in css                    # rotated ranges render reliably


def test_scale_inset_matches_cap():
    css = eq_css()
    assert css.count("calc(var(--cap-h) / 2") >= 3      # marks, ticks and slit share the same inset


def test_responsive_and_motion_rules():
    css = (STATIC / "app.css").read_text()
    narrow = css[css.index("@media (max-width: 420px)"):]
    assert ".eq-board" in narrow and "--eq-h" in narrow
    assert "html.power-on .eq-readout" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_scripts_parse():
    for name in ("eq.js", "eq-core.js"):
        result = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_assets_served():
    with local_client(create_app([LEFT, RIGHT], transport=FakePair().transport())) as client:
        for name in ("eq.js", "eq-core.js"):
            assert client.get(f"/static/{name}").status_code == 200, name
        assert "eq-card" in client.get("/").text


# --- presets (V2.4.3-B4) ---------------------------------------------------------------------

def test_preset_row_and_confirmation_markup():
    html = (STATIC / "index.html").read_text()
    card = re.search(r'<section class="card tone graphic-eq off" id="eq-card".*?</section>', html, re.S).group(0)
    assert card.index('class="eq-controls"') < card.index('id="eq-presets"') < card.index('id="eq-board"')
    assert 'role="group" aria-labelledby="eq-presets-label"' in card and ">Préréglage<" in card
    confirm = re.search(r'<div class="eq-confirm".*?</div>\s*</div>', card, re.S).group(0)
    for needle in ('role="alertdialog"', "hidden", 'id="eq-confirm-ok"', 'id="eq-confirm-cancel"',
                   ">Annuler<", ">Appliquer quand même<"):
        assert needle in confirm, needle
    assert confirm.index("eq-confirm-cancel") < confirm.index("eq-confirm-ok")
    assert "Custom rappelle votre dernier réglage manuel" in card


def test_presets_come_from_the_api_and_never_touch_native_controls():
    js = (STATIC / "eq.js").read_text()
    assert "E.presetButtons(status" in js                 # labels and curves from /api/eq, not duplicated
    assert '"/api/eq/preset", { preset: p.id }' in js
    assert 'aria-pressed' in js and "b.disabled = p.disabled" in js
    for native in ("/api/tone", "/api/volume", "bass", "treble"):
        assert native not in js, native
    core = (STATIC / "eq-core.js").read_text()
    for name in ("Flat", "Rock", "Vocal", "Classical", "Bass Boost"):
        assert f'"{name}"' not in core and f'"{name}"' not in js, name     # the server owns the list


def test_confirmation_repeats_with_confirm_and_revision():
    js = (STATIC / "eq.js").read_text()
    assert 'err.detail.code === "confirm"' in js
    assert "confirm: true, revision: err.detail.revision" in js
    assert "els.confirmCancel.focus()" in js and '"Escape"' in js
    assert "busy: busy || !!pendingConfirm" in js         # no other preset while a warning is open


def test_preset_styles():
    css = eq_css()
    for needle in (".eq-presets", ".eq-preset[aria-pressed=\"true\"]", ".eq-preset:focus-visible",
                   ".eq-preset:disabled", ".eq-confirm", ".eq-confirm[hidden]", "flex-wrap: wrap"):
        assert needle in css, needle
