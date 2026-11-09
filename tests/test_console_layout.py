"""V2.4.3-B3.1 static checks: movable console blocks (markup, wiring, styles, served assets).

The behaviour itself is covered by tests/js/layout-core.test.cjs (order, storage,
migration) and by the Edge scenario of reports/console-layout-v243b31.md."""
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
HTML = (STATIC / "index.html").read_text()
JS = (STATIC / "layout.js").read_text()
BLOCKS = ["player", "spectrum", "audio", "eq", "bluetooth"]


def layout_css() -> str:
    css = (STATIC / "app.css").read_text()
    start = css.index("/* F. Movable blocks")
    return css[start:css.index("/* Responsive", start)]


def container() -> str:
    start = HTML.index('<div class="blocks" id="blocks">')
    end = HTML.index('<div class="layout-bar">', start)
    return HTML[start:end]


def test_blocks_in_container_default_order_and_unique_ids():
    found = re.findall(r'data-block="([a-z-]+)"', container())
    assert found == BLOCKS                                    # HTML order = default order
    assert re.findall(r'data-block="([a-z-]+)"', HTML) == BLOCKS   # no movable block elsewhere
    for block in BLOCKS:
        section = re.search(rf'<section[^>]*data-block="{block}"[^>]*>', HTML).group(0)
        assert 'data-block-label="' in section, block


def test_each_block_header_is_its_drag_zone():
    sections = re.findall(r"<section[^>]*data-block=.*?</section>", container(), re.S)
    assert len(sections) == len(BLOCKS)
    for section in sections:
        assert section.count("data-block-head") == 1, section[:80]
        head = section.index("data-block-head")
        assert head < section.index('class="eyebrow"') < head + 120, section[:80]
    # the full header rows (title + status), not a small handle
    for row in ('<div class="spectrum-head" data-block-head>', '<div class="tone-head" data-block-head>',
                '<div class="systems-head" data-block-head>'):
        assert row in container(), row
    assert container().count('<div class="tone-head" data-block-head>') == 2      # audio deck and equalizer
    # the player title is rewritten by app.js: the head wraps it instead of being it
    assert '<div class="block-head" data-block-head><p class="eyebrow" id="source-name">' in container()


def test_header_errors_and_system_panel_stay_fixed():
    assert HTML.index("</header>") < HTML.index('id="errors"') < HTML.index('id="blocks"')
    assert HTML.index('<div class="layout-bar">') < HTML.index('id="system-info"')
    for fixed in ('class="card faceplate"', 'id="system-info"', 'id="errors"'):
        assert fixed not in container(), fixed


def test_reset_button_and_accessible_texts():
    bar = HTML[HTML.index('<div class="layout-bar">'):HTML.index("</div>", HTML.index('<div class="layout-bar">'))]
    assert ">Réinitialiser la disposition<" in bar and 'id="layout-reset"' in bar and "disabled" in bar
    assert 'id="layout-help"' in bar and 'id="layout-status"' in bar and 'aria-live="polite"' in bar
    assert 'aria-describedby", "layout-help"' in JS and "glisser son en-tête" in bar
    for label in ("Monter « ", "Descendre « ", "Position du bloc « "):
        assert label in JS, label
    for gone in ("block-handle", "Déplacer « ", "⠿", "grip"):              # B3.1.2: no handle any more
        assert gone not in JS and gone not in HTML, gone


def test_layout_scripts_follow_the_blocks_and_run_before_the_others():
    core, layout = HTML.index('src="/static/layout-core.js?v='), HTML.index('src="/static/layout.js?v=')
    assert HTML.index('<div class="layout-bar">') < core < layout < HTML.index('src="/static/link-status.js?v=')
    assert HTML.count("layout-core.js") == 1 and HTML.count('src="/static/layout.js') == 1


def test_stylesheet_and_layout_scripts_are_versioned_together():
    # a browser kept a stale app.css while fetching the new layout.js: the Monter / Descendre
    # buttons showed as grey boxes. Every static file carries the same ?v= so they are fetched
    # together (since the GitHub preparation: all scripts send the header security.py requires).
    versions = re.findall(r'(?:href|src)="/static/([a-z-]+\.(?:css|js))\?v=([0-9a-z.-]+)"', HTML)
    referenced = re.findall(r'(?:href|src)="/static/([^"?]+)', HTML)
    assert sorted(name for name, _ in versions) == sorted(referenced)
    assert {"app.css", "app.js", "eq.js", "layout.js", "theme.js", "spectrum.js"} <= set(referenced)
    assert len({v for _, v in versions}) == 1
    with local_client(create_app([LEFT, RIGHT], transport=FakePair().transport())) as client:
        for name, version in versions:
            assert client.get(f"/static/{name}?v={version}").status_code == 200, name


def test_only_the_free_area_of_the_header_starts_a_drag():
    # pointer listeners exist on the header only: the block body never moves it
    owners = set(re.findall(r'(\w+)\.addEventListener\("pointer(?:down|move|up|cancel)"', JS))
    assert owners == {"head"}
    assert 'querySelector("[data-block-head]")' in JS
    # controls inside the header are skipped: buttons, sliders, knobs, lists, fields, links, focusable
    interactive = re.search(r'const INTERACTIVE = (".*?");', JS, re.S).group(1)
    for needle in ("a[href]", "button", "input", "select", "textarea", "label", "[role=slider]",
                   "[role=button]", "[tabindex]", "contenteditable"):
        assert needle in interactive, needle
    assert "const control = e.target.closest(INTERACTIVE);" in JS
    assert "if (control && head.contains(control)) return;" in JS
    assert "draggable" not in JS and "draggable" not in HTML  # no HTML5 drag and drop (no touch support)
    assert "DRAG_START_PX" in JS                               # a click on the header moves nothing
    assert '"Escape"' in JS                                    # cancel a drag in progress
    assert "document.fullscreenElement" in JS                  # no move while the analyzer is fullscreen


def test_touch_swipe_scrolls_press_and_hold_moves():
    # a touch swipe on a header is a page scroll; the block follows only after a hold
    assert "HOLD_MS = 300" in JS and "HOLD_SLOP_PX = 8" in JS
    assert 'const mouse = e.pointerType === "mouse";' in JS
    assert "if (mouse) e.preventDefault();" in JS             # touch keeps its default scrolling
    assert "if (moved > HOLD_SLOP_PX) finish(false);" in JS
    assert '"touchmove", (e) => { if (drag && drag.armed) e.preventDefault(); }, { passive: false }' in JS
    assert '"pointercancel", () => finish(false)' in JS       # the browser took the gesture: no move
    css = layout_css()
    assert "touch-action" not in css                           # headers never block scrolling by CSS


def test_existing_nodes_are_moved_never_rebuilt():
    assert "moveBefore" in JS and "insertBefore" in JS
    for rebuild in ("innerHTML =", "outerHTML", "cloneNode", "replaceWith", ".remove()"):
        lines = [l for l in JS.splitlines() if rebuild in l]
        assert all("b.innerHTML = icon" in l for l in lines), (rebuild, lines)   # icons of new buttons only
    assert "focus({ preventScroll: true })" in JS             # focus kept when the browser drops it


def test_keyboard_alternative():
    for key in ('ArrowUp', 'ArrowDown', '"Home"', '"End"'):
        assert key in JS, key
    assert 'aria-disabled' in JS and 'data-step' not in HTML  # step buttons built by layout.js
    assert 'tools.addEventListener("keydown"' in JS            # arrows on the Monter / Descendre buttons
    css = layout_css()
    assert ".block-tools:not(:focus-within)" in css            # hidden until focused, never display:none
    hidden = re.search(r"\.block-tools:not\(:focus-within\)\s*\{([^}]*)\}", css).group(1)
    assert "display: none" not in hidden and "clip:" in hidden


def test_storage_through_layout_core_only():
    assert "localStorage" in JS and "setItem" not in JS and "getItem" not in JS
    core = (STATIC / "layout-core.js").read_text()
    assert 'STORAGE_KEY = "devialet.layout"' in core and "VERSION = 1" in core


def test_styles_use_theme_tokens_and_touch_rules():
    css = layout_css()
    for needle in ("[data-block-head] {", "cursor: grab", "cursor: grabbing", ".dragging", ".lifting", ".drop-marker",
                   ".block-tools:not(:focus-within)", "@media (any-pointer: coarse)",
                   ".spectrum:fullscreen .block-tools", ".sr-only", "html.layout-dragging"):
        assert needle in css, needle
    for token in ("var(--accent)", "var(--field-bg)", "var(--field-text)", "var(--field-border)"):
        assert token in css, token
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)        # colours come from the four themes
    assert "@media (pointer: coarse)" not in css               # hybrid PCs: any touch screen counts


def z_index(css: str, selector: str) -> int:
    rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
    return int(re.search(r"z-index:\s*(\d+)", rule).group(1))


def test_drop_marker_above_the_lifted_block():
    # dragging a tall block down puts the destination line inside the lifted card's area
    css = layout_css()
    assert z_index(css, ".drop-marker") > z_index(css, ".blocks > .dragging")


def test_header_controls_keep_their_cursor():
    css = layout_css()
    assert "[data-block-head] :is(a[href], button, summary, [role=slider], [role=button]) { cursor: pointer; }" in css
    assert "[data-block-head] :is(input, select, textarea) { cursor: auto; }" in css
    coarse = css[css.index("@media (any-pointer: coarse)"):]
    assert ".block-step { width: 40px; height: 36px; }" in coarse[:coarse.index("\n}")]


def test_audio_controls_untouched_by_the_layout():
    # the panels keep their ids and scripts; layout.js never reaches into them
    for element_id in ("tone-bass", "tone-treble", "volume", "btn-vol-down", "btn-vol-up", "eq-board", "eq-power",
                       "eq-reset", "btn-toggle", "btn-previous", "btn-next", "spectrum-canvas", "bt-status"):
        assert HTML.count(f'id="{element_id}"') == 1, element_id
        assert element_id not in JS, element_id
    assert "fetch(" not in JS and "/api/" not in JS           # no API call, no FastAPI change


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_scripts_parse():
    for name in ("layout.js", "layout-core.js"):
        result = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_assets_served():
    with local_client(create_app([LEFT, RIGHT], transport=FakePair().transport())) as client:
        for name in ("layout.js", "layout-core.js"):
            assert client.get(f"/static/{name}").status_code == 200, name
        page = client.get("/").text
        assert 'id="blocks"' in page and "Réinitialiser la disposition" in page
