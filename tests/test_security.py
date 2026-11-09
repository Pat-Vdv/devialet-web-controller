"""Request guard (devialet_web/security.py): Host / DNS rebinding, cross-site writes, LAN mode.

Malicious requests are built without the helper's header; every test uses fake
speakers and, for the equalizer, a temporary folder (tests.test_graphic_eq)."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from devialet_web import __main__ as cli
from devialet_web.app import create_app
from devialet_web.graphic_eq import GraphicEq
from devialet_web.security import ACCESS_COOKIE, SecurityPolicy, host_name
from devialet_web.spectrum import SpectrumHub
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)
from tests.local_client import LocalClient, local_client
from tests.test_graphic_eq import B1_FILE, eqdir, text  # noqa: F401 (fixture)

TOKEN = "t" * 40
LAN = SecurityPolicy.lan(["controller.lan"], TOKEN)
# state-changing routes that need no body: a plain HTML form could reach them
BODYLESS = ["/api/playback/play", "/api/bluetooth/pairing", "/api/discovery/scan", "/api/discover",
            "/api/eq/enable", "/api/eq/disable", "/api/eq/reset"]


def app_for(eqdir=None, pair=None, **kw):
    pair = pair or FakePair()
    return create_app([LEFT, RIGHT], transport=pair.transport(),
                      graphic_eq=GraphicEq(eqdir) if eqdir else None, **kw)


def raw(app, base="http://localhost", **kw):
    """A client that adds nothing: what a hostile page or form would send."""
    return LocalClient(app, base_url=base, **kw)


# --- Host header / DNS rebinding -----------------------------------------------------------

@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8000", "192.168.1.66", "localhost.evil"])
def test_foreign_host_is_refused_even_for_reads(host):
    with raw(app_for()) as c:
        for path in ("/", "/api/state", "/api/eq", "/static/app.js"):
            r = c.get(path, headers={"Host": host})
            assert r.status_code == 400 and "Hôte" in r.text, (host, path)


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000", "LOCALHOST"])
def test_this_machine_is_accepted(host):
    with raw(app_for()) as c:
        assert c.get("/api/state", headers={"Host": host}).status_code == 200


def test_host_name_parsing():
    assert host_name("Localhost:8000") == "localhost" and host_name("[::1]:8000") == "::1"
    assert host_name("::1") == "::1" and host_name("") is None and host_name(None) is None


# --- cross-site writes ---------------------------------------------------------------------

@pytest.mark.parametrize("path", BODYLESS)
def test_plain_form_post_is_refused(eqdir, path):
    pair = FakePair()
    with raw(app_for(eqdir, pair)) as c:
        before = len(pair.posts)
        r = c.post(path, headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "Origin": "https://evil.example"}, content=b"")
        assert r.status_code == 403
        r = c.post(path, content=b"")                          # even same machine, no Origin
        assert r.status_code == 403 and "X-Devialet-Request" in r.text
        assert len(pair.posts) == before                       # nothing reached the speakers
    assert text(eqdir) == B1_FILE                              # nor the equalizer file


def test_text_plain_and_json_writes_without_header_are_refused(eqdir):
    with raw(app_for(eqdir)) as c:
        assert c.post("/api/eq/preset", headers={"Content-Type": "text/plain"},
                      content=b'{"preset":"rock"}').status_code == 403
        assert c.post("/api/volume", json={"volume": 10}).status_code == 403
        assert c.put("/api/eq", json={"gains": [0] * 10}).status_code == 403
        assert c.patch("/api/eq/bands", json={"bands": {"1000": 3}}).status_code == 403
    assert text(eqdir) == B1_FILE


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://localhost:3000"},                       # another local site, other port
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_write_with_header_but_foreign_origin_is_refused(eqdir, headers):
    with raw(app_for(eqdir)) as c:
        r = c.post("/api/eq/preset", json={"preset": "rock"},
                   headers={"Host": "localhost:8000", "X-Devialet-Request": "1", **headers})
        assert r.status_code == 403
    assert text(eqdir) == B1_FILE


def test_legitimate_writes_still_work(eqdir):
    pair = FakePair()
    with raw(app_for(eqdir, pair)) as c:
        page = {"Host": "localhost:8000", "Origin": "http://localhost:8000",
                "Sec-Fetch-Site": "same-origin", "X-Devialet-Request": "1"}
        assert c.post("/api/eq/preset", json={"preset": "rock"}, headers=page).json()["preset"] == "rock"
        assert c.post("/api/volume", json={"volume": 12}, headers=page).status_code == 200
        assert c.post("/api/playback/pause", headers=page).status_code == 200
        # a command line tool on this machine: no Origin, header given
        assert c.post("/api/eq/preset", json={"preset": "flat"},
                      headers={"X-Devialet-Request": "1"}).json()["preset"] == "flat"
    assert pair.volume == 12


def test_no_cors_preflight_is_ever_granted():
    with raw(app_for()) as c:
        r = c.options("/api/volume", headers={"Origin": "https://evil.example",
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "x-devialet-request"})
        assert r.status_code == 403 and "access-control-allow-origin" not in r.headers


def test_responses_forbid_framing_and_sniffing():
    with local_client(app_for()) as c:
        for r in (c.get("/"), c.get("/api/state"), c.get("/static/app.js"), c.get("/", headers={"Host": "x"})):
            assert r.headers["x-frame-options"] == "DENY"
            assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
            assert r.headers["x-content-type-options"] == "nosniff"


def test_frontend_sends_the_header_on_every_write():
    from pathlib import Path
    static = Path(__file__).parent.parent / "devialet_web" / "static"
    for name in ("app.js", "discovery.js", "tone.js", "eq.js", "bluetooth.js", "spectrum.js"):
        js = (static / name).read_text()
        assert '"X-Devialet-Request": "1"' in js, name


# --- WebSockets ----------------------------------------------------------------------------

def test_spectrum_viewer_refuses_foreign_origin_and_host():
    with raw(app_for(spectrum=SpectrumHub())) as c:
        for headers in ({"Origin": "https://evil.example"}, {"Host": "attacker.example"}):
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/api/spectrum/ws", headers=headers) as ws:
                    ws.receive_json()
        with c.websocket_connect("/api/spectrum/ws", headers={"Origin": "http://localhost:8000"}) as ws:
            assert ws.receive_json()["type"] == "status"
        with c.websocket_connect("/api/spectrum/ws") as ws:        # scripts/eq_validation.py: no Origin
            assert ws.receive_json()["type"] == "status"


# --- LAN mode ------------------------------------------------------------------------------

def test_lan_policy_needs_a_long_token_and_a_host():
    with pytest.raises(ValueError):
        SecurityPolicy.lan(["controller.lan"], "short")
    with pytest.raises(ValueError):
        SecurityPolicy.lan([], TOKEN)
    assert "controller.lan" in LAN.allowed_hosts and "localhost" in LAN.allowed_hosts


def test_lan_mode_requires_the_access_cookie():
    with raw(app_for(security=LAN), base="http://controller.lan:8000") as c:
        assert c.get("/api/state").status_code == 401
        assert c.get("/?access=" + "x" * 40, follow_redirects=False).status_code == 401
        r = c.get("/?access=" + TOKEN, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert c.get("/api/state").status_code == 200              # cookie kept by the client
        assert c.post("/api/volume", json={"volume": 5}).status_code == 403   # header still required
        assert c.post("/api/volume", json={"volume": 5}, headers={"X-Devialet-Request": "1"}).status_code == 200
        c.cookies.set(ACCESS_COOKIE, "wrong")
        assert c.get("/api/state").status_code == 401


def test_lan_mode_keeps_the_local_agent_route_and_refuses_other_hosts():
    hub = SpectrumHub(agent_hosts=frozenset({"testclient"}))
    with raw(app_for(spectrum=hub, security=LAN)) as c:
        with c.websocket_connect("/api/spectrum/agent") as agent:      # no cookie: loopback-only route
            agent.send_text('{"type": "hello", "version": "2.3.0"}')
            assert agent.receive_json()["type"] == "control"
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/spectrum/ws") as ws:        # viewer needs the cookie
                ws.receive_json()
        assert c.get("/api/state", headers={"Host": "other.lan"}).status_code == 400


# --- command line --------------------------------------------------------------------------

def test_default_bind_is_loopback(monkeypatch):
    monkeypatch.delenv("DEVIALET_BIND", raising=False)
    monkeypatch.delenv("DEVIALET_ALLOW_LAN", raising=False)
    args = cli.parse_args(["--hosts", "192.0.2.10"])
    assert args.bind == "127.0.0.1" and args.allow_lan is False
    assert cli.security_policy(args) == SecurityPolicy()


def test_network_bind_needs_every_safeguard(monkeypatch):
    monkeypatch.delenv("DEVIALET_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DEVIALET_ALLOW_LAN", raising=False)
    monkeypatch.delenv("DEVIALET_ALLOWED_HOSTS", raising=False)
    for argv in (["--bind", "0.0.0.0"],
                 ["--bind", "0.0.0.0", "--allow-lan", "--allowed-host", "pc.lan"]):        # no token
        with pytest.raises(ValueError):
            cli.security_policy(cli.parse_args(argv))
    monkeypatch.setenv("DEVIALET_ACCESS_TOKEN", TOKEN)
    with pytest.raises(ValueError):
        cli.security_policy(cli.parse_args(["--bind", "0.0.0.0", "--allow-lan"]))         # no host
    p = cli.security_policy(cli.parse_args(["--bind", "0.0.0.0", "--allow-lan", "--allowed-host", "pc.lan"]))
    assert p.access_token == TOKEN and "pc.lan" in p.allowed_hosts


def test_main_refuses_to_start_exposed(monkeypatch, capsys):
    monkeypatch.delenv("DEVIALET_ALLOW_LAN", raising=False)
    started = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: started.append(k))
    assert cli.main(["--hosts", "192.0.2.10", "--bind", "0.0.0.0", "--eapo-config", "",
                     "--cache", "/nonexistent/k.json"]) == 2
    assert started == [] and "Accès réseau refusé" in capsys.readouterr().err
