"""V2.4.1 static checks: tone panel markup, accessibility, wiring, served assets."""
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


def test_two_accessible_knobs():
    html = (STATIC / "index.html").read_text()
    assert 'id="tone-card"' in html and ">Tonalité<" in html
    for band, label in (("bass", "Basses"), ("treble", "Aigus")):
        knob = re.search(rf'<div class="tone-knob" id="tone-{band}"[^>]*>', html, re.S).group(0)
        for attr in ('role="slider"', f'aria-label="{label}"', 'aria-valuemin="-6"',
                     'aria-valuemax="6"', 'aria-valuenow="0"', 'aria-describedby="tone-help"'):
            assert attr in knob, (band, attr)
        assert f'<output class="tone-readout" for="tone-{band}">' in html
    assert 'id="tone-message"' in html and 'role="status"' in html
    assert 'id="tone-neutral"' in html


def test_scripts_order_and_panel_wiring():
    html = (STATIC / "index.html").read_text()
    assert html.index("tone-core.js") < html.index('src="/static/tone.js?v=')
    js = (STATIC / "tone.js").read_text()
    for event in ("keydown", "pointerdown", "pointermove", "pointerup", "dblclick", "wheel",
                  "visibilitychange"):
        assert f'"{event}"' in js, event
    assert "setPointerCapture" in js
    assert "document.activeElement !== el" in js        # wheel only on the focused knob
    assert '"/api/tone"' in js and "createToneSync" in js


def test_readout_shows_speaker_values_not_local_target():
    """The dB readout is filled from the server state (s[band]), never knob.value."""
    js = (STATIC / "tone.js").read_text()
    line = next(l for l in js.splitlines() if "readout.textContent" in l)
    assert "applied" in line and "knob.value" not in line
    assert re.search(r"const applied = s \? s\[band\] : null", js)


def test_tone_css_present_and_spectrum_untouched():
    css = (STATIC / "app.css").read_text()
    for selector in (".tone-knob", ".tone-pointer", ".tone-led", ".tone-readout", ".tone-scale .tick.lit",
                     ".tone-knob:focus-visible"):
        assert selector in css, selector
    assert "conic-gradient" in css                       # spun aluminium
    assert "prefers-reduced-motion" in css
    for name in ("spectrum.js", "spectrum-settings.js", "spectrum-ballistics.js"):
        assert "tone" not in (STATIC / name).read_text().lower(), name


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_tone_scripts_parse():
    for name in ("tone.js", "tone-core.js"):
        result = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_assets_served():
    with local_client(create_app([LEFT, RIGHT], transport=FakePair().transport())) as client:
        for name in ("tone.js", "tone-core.js"):
            assert client.get(f"/static/{name}").status_code == 200, name
        assert "tone-card" in client.get("/").text
