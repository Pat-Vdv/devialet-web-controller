"""V2.4.2 Hi-Fi console: global themes, page layout, single theme control,
reused knobs, system panel, responsive rules, accessibility, regressions."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web.app import create_app
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

STATIC = Path(__file__).resolve().parents[1] / "devialet_web" / "static"
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "app.css").read_text()
THEMES = ("classic", "technics", "pioneer", "amber")
TOKENS = ("--bg", "--card", "--card-border", "--text", "--muted", "--line", "--accent",
          "--accent-text", "--ok", "--warn", "--bad", "--focus", "--field-bg", "--field-text",
          "--field-border", "--panel", "--panel-text", "--panel-dim", "--panel-border",
          "--display-bg", "--display-text", "--display-glow", "--led", "--led-glow")


def theme_block(theme: str) -> str:
    match = re.search(rf':root\[data-theme="{theme}"\]\s*\{{([^}}]*)\}}', CSS)
    assert match, theme
    return match.group(1)


def tokens(theme: str) -> dict:
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", theme_block(theme)))


def contrast(a: str, b: str) -> float:
    def lum(h):
        r, g, b_ = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- global themes --------------------------------------------------------------------

@pytest.mark.parametrize("theme", THEMES)
def test_every_global_theme_defines_every_token(theme):
    block = theme_block(theme)
    for token in TOKENS:
        assert re.search(re.escape(token) + r"\s*:", block), f"{theme}: {token}"


def test_theme_names_agree_between_js_and_css():
    js = (STATIC / "theme.js").read_text()
    for theme in THEMES:
        assert re.search(rf"\b{theme}:\s*\"", js), theme


@pytest.mark.parametrize("theme", THEMES)
def test_contrast_meets_wcag_aa(theme):
    t = tokens(theme)
    for fg, bg in (("--text", "--card"), ("--muted", "--card"), ("--muted", "--bg"),
                   ("--accent", "--card"), ("--bad", "--card"), ("--ok", "--card"),
                   ("--accent-text", "--accent"), ("--field-text", "--field-bg"),
                   ("--panel-text", "--panel"), ("--panel-dim", "--panel"),
                   ("--display-text", "--display-bg")):
        assert contrast(t[fg], t[bg]) >= 4.5, f"{theme}: {fg} on {bg} = {contrast(t[fg], t[bg]):.2f}"


def test_components_read_theme_tokens_not_fixed_colours():
    for selector, token in ((r"body", "--bg"), (r"\.card", "--card"), (r"\.text-btn", "--accent"),
                            (r"\.badge", "--accent"), (r"select", "--field-bg"),
                            (r"\.spectrum", "--panel"), (r"\.tone", "--led"),
                            (r"\.errors", "--bad"), (r"\.tone-readout, \.volume-display", "--display-bg")):
        block = re.search(selector + r"\s*\{([^}]*)\}", CSS)
        assert block and token in block.group(1), (selector, token)
    assert re.search(r":focus-visible\s*\{[^}]*var\(--focus\)", CSS)
    assert re.search(r"\.text-btn:disabled\s*\{[^}]*opacity", CSS)


def test_dark_themes_declare_dark_colour_scheme():
    for theme in ("technics", "pioneer", "amber"):
        assert "color-scheme: dark" in theme_block(theme), theme
    assert "color-scheme: light" in theme_block("classic")


# --- single source of truth, no flash ------------------------------------------------

def test_single_theme_selector():
    assert HTML.count('id="theme-select"') == 1
    assert 'id="spectrum-theme"' not in HTML
    assert len(re.findall(r"<select[^>]*theme", HTML)) == 1
    spectrum_js = (STATIC / "spectrum.js").read_text()
    assert "prefs.theme" not in spectrum_js
    assert not re.search(r"els\.theme\b", spectrum_js)      # themeControl / themeHome only


def test_theme_script_runs_in_head_before_paint():
    head = HTML.split("</head>")[0]
    tag = re.search(r'<script src="/static/theme.js\?v=[0-9a-z.-]+"></script>', head)
    assert tag, "theme.js must be a blocking script in <head>"
    assert "defer" not in tag.group(0) and "async" not in tag.group(0)
    assert HTML.count("theme.js") == 1


def test_analyzer_follows_global_theme_and_docks_selector_in_fullscreen():
    js = (STATIC / "spectrum.js").read_text()
    assert "G.spectrumPalette(G.currentTheme(document))" in js
    assert "document.addEventListener(G.EVENT, applyTheme)" in js
    assert "G.dock(els.themeControl, els.themeHome, els.controls, active)" in js
    assert 'id="theme-home"' in HTML and 'id="spectrum-controls"' in HTML
    # analyzer palettes from V2.3.1 are kept as they are
    for palette in ("technics", "pioneer", "amber"):
        assert f'.spectrum[data-theme="{palette}"]' in CSS


# --- page organisation -----------------------------------------------------------------

def test_sections_in_faceplate_order():
    markers = ['class="card faceplate"', 'class="card now-playing"', 'id="spectrum-card"',
               'id="tone-card"', 'class="card bt"', 'id="system-info"']
    positions = [HTML.index(m) for m in markers]
    assert positions == sorted(positions)


def test_header_brand_system_link_and_theme():
    header = HTML[HTML.index('class="card faceplate"'):HTML.index("</header>")]
    for needle in ("DEVIALET", "PHANTOM II", 'id="system-name"', 'id="link"', 'id="link-label"',
                   'id="theme-select"'):
        assert needle in header, needle


def test_deck_is_bass_volume_treble_reusing_v241_knobs():
    deck = HTML[HTML.index('id="tone-card"'):HTML.index("</section>", HTML.index('id="tone-card"'))]
    order = [deck.index(n) for n in ('id="tone-bass"', 'id="volume-value"', 'id="tone-treble"')]
    assert order == sorted(order)
    for needle in ('id="volume"', 'id="btn-vol-down"', 'id="btn-vol-up"', 'id="volume-note"',
                   'id="tone-neutral"', 'id="tone-pill"', 'id="tone-message"', 'data-band="bass"',
                   'data-band="treble"', 'class="volume-display"'):
        assert needle in deck, needle
    # the knob logic is the V2.4.1 one, loaded once, untouched by the layout
    tone_js = (STATIC / "tone.js").read_text()
    assert "card.querySelector(`[data-band=\"${band}\"]`)" in tone_js
    assert HTML.count("tone-core.js") == 1 and HTML.count('src="/static/tone.js?v=') == 1


def test_system_panel_is_collapsible_and_keeps_discovery_actions():
    start = HTML.index('<details class="card sysinfo" id="system-info">')
    panel = HTML[start:HTML.index("</details>", start)]
    for needle in ('id="speakers"', 'id="btn-scan"', 'id="scan-status"', 'id="systems-list"',
                   "Informations système", "<summary>"):
        assert needle in panel, needle


def test_player_and_bluetooth_ids_kept():
    for element_id in ("source-name", "track-title", "track-artist", "play-state", "btn-previous",
                       "btn-toggle", "btn-next", "icon-play", "icon-pause", "bt-pill", "bt-status",
                       "bt-stream", "bt-wizard", "btn-pair", "bt-countdown", "bt-message", "errors"):
        assert HTML.count(f'id="{element_id}"') == 1, element_id


def test_scripts_order():
    order = ["link-status.js", "app.js", "discovery.js", "bluetooth.js", "tone-core.js",
             'src="/static/tone.js?v=', "spectrum-ballistics.js", "spectrum-settings.js",
             'src="/static/spectrum.js?v=']
    positions = [HTML.rindex(n) for n in order]
    assert positions == sorted(positions)


# --- connection states ---------------------------------------------------------------------

def test_disconnection_is_shown_and_stale_values_are_not():
    app_js = (STATIC / "app.js").read_text()
    assert "LinkStatus.nextLink" in app_js and "LinkStatus.describe" in app_js
    # on backend loss the last volume is dropped instead of shown as current
    assert re.search(r"leader: null, volume: null", app_js)
    status_js = (STATIC / "link-status.js").read_text()
    for label in ("Connecté", "Connexion perdue", "Reconnexion en cours"):
        assert label in status_js
    assert re.search(r'html\[data-link="lost"\] \.volume-display', CSS)
    assert 'role="status"' in HTML[HTML.index('id="link"') - 80:HTML.index('id="link"') + 120]


# --- responsive, keyboard, motion --------------------------------------------------------------

def test_responsive_rules():
    assert re.search(r"main\s*\{[^}]*max-width:\s*1120px", CSS)
    assert re.search(r"\.deck\s*\{[^}]*grid-template-columns:\s*1fr minmax\(260px, 1\.35fr\) 1fr", CSS)
    m760 = re.search(r"@media \(max-width: 760px\)\s*\{(.*?)\n\}", CSS, re.S).group(1)
    assert '"vol vol" "bass treble"' in m760
    m420 = re.search(r"@media \(max-width: 420px\)\s*\{(.*?)\n\}", CSS, re.S).group(1)
    assert ".faceplate { grid-template-columns: 1fr; }" in m420
    assert "min-width: 0" in CSS                         # grid children may shrink: no sideways scroll
    assert "touch-action: none" in CSS                   # knobs usable by touch
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in HTML


def test_keyboard_access():
    for band in ("bass", "treble"):
        knob = re.search(rf'<div class="tone-knob" id="tone-{band}"[^>]*>', HTML).group(0)
        assert 'role="slider"' in knob
    assert 'aria-label="Thème de l\'interface"' in HTML
    assert 'aria-label="Volume"' in HTML
    assert re.search(r"summary:focus-visible", CSS)


def test_power_on_animation_is_first_load_only_and_respects_reduced_motion():
    theme_js = (STATIC / "theme.js").read_text()
    assert 'classList.add("power-on")' in theme_js
    apply_fn = re.search(r"function applyTheme\(doc, theme\) \{(.*?)\n  \}", theme_js, re.S).group(1)
    assert "power-on" not in apply_fn                    # not replayed on theme change
    assert "POWER_ON_MS = 1100" in theme_js
    assert re.search(r"@keyframes display-on", CSS) and re.search(r"display-on 900ms", CSS)
    reduced = re.findall(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", CSS, re.S)
    assert any("html.power-on" in block and "animation: none" in block for block in reduced)


# --- served, regressions ---------------------------------------------------------------------

def test_assets_served_and_api_unchanged():
    fake = FakePair()
    with local_client(create_app([LEFT, RIGHT], transport=fake.transport())) as client:
        for name in ("theme.js", "link-status.js", "app.css", "app.js", "tone.js", "spectrum.js"):
            assert client.get(f"/static/{name}").status_code == 200, name
        page = client.get("/").text
        assert "theme-select" in page and "system-info" in page
        state = client.get("/api/state").json()
        assert state["volume"] == 31 and state["source"]["title"] == "One More Time"
        assert client.get("/api/tone").json()["available"] is True
        assert client.post("/api/volume", json={"volume": 51}).status_code == 422   # cap unchanged
    assert fake.posts == []
