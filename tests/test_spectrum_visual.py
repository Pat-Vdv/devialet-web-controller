"""V2.3.1 static checks: page controls, theme CSS completeness, served assets."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web.app import create_app
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

STATIC = Path(__file__).resolve().parents[1] / "devialet_web" / "static"
THEMES = ("technics", "pioneer", "amber")
THEME_VARS = ("--spec-bg", "--spec-seg-low", "--spec-seg-high", "--spec-seg-off", "--spec-peak",
              "--spec-peak-glow", "--spec-grid", "--spec-label", "--spec-accent")


def css_block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def test_every_theme_defines_every_variable():
    css = (STATIC / "app.css").read_text()
    for theme in THEMES:
        block = css_block(css, f'.spectrum[data-theme="{theme}"]')
        for var in THEME_VARS:
            assert re.search(re.escape(var) + r"\s*:", block), f"{theme}: {var}"


def test_js_and_css_agree_on_theme_names_and_variables():
    js = (STATIC / "spectrum-settings.js").read_text()
    for theme in THEMES:
        assert re.search(rf"\b{theme}:\s*\"", js), theme
    for var in THEME_VARS:
        assert f'"{var}"' in js, var


def test_analyzer_is_350px_and_responsive():
    css = (STATIC / "app.css").read_text()
    assert re.search(r"\.spectrum-screen\s*\{[^}]*height:\s*350px", css)
    assert re.search(r"@media \(max-width: 720px\)\s*\{\s*\.spectrum-screen\s*\{[^}]*clamp\(", css)
    assert ".spectrum:fullscreen" in css and ".spectrum:-webkit-full-screen" in css


def test_page_has_all_v231_controls():
    html = (STATIC / "index.html").read_text()
    # V2.4.2: the analyzer theme select became the single global one (theme-select)
    for element_id in ("theme-select", "spectrum-sensitivity", "spectrum-sensitivity-value",
                       "spectrum-peak", "spectrum-hold", "spectrum-stereo", "spectrum-mono",
                       "spectrum-fullscreen", "spectrum-exit", "spectrum-track", "spectrum-toggle"):
        assert f'id="{element_id}"' in html, element_id
    assert re.search(r'id="spectrum-sensitivity"[^>]*min="-12"[^>]*max="12"', html)
    for ms in ("500", "1000", "2000"):
        assert f'<option value="{ms}">' in html
    # settings before the panel script that uses them
    assert html.index("spectrum-ballistics.js") < html.index("spectrum-settings.js") < \
        html.index('src="/static/spectrum.js?v=')


def test_assets_served_and_existing_features_still_present():
    with local_client(create_app([LEFT, RIGHT], transport=FakePair().transport())) as client:
        for name in ("spectrum.js", "spectrum-settings.js", "spectrum-ballistics.js", "app.css"):
            assert client.get(f"/static/{name}").status_code == 200, name
        html = client.get("/").text
        for script in ("app.js", "discovery.js", "bluetooth.js"):
            assert script in html, script            # V1 / V2.1 / V2.2 untouched
        assert client.get("/api/state").status_code == 200
