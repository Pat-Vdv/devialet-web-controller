"""Native tone control (BASS / TREBLE, V2.4.1), simulated speakers only."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web.app import create_app
from devialet_web.ipcontrol import ALLOWED_POSTS, EQUALIZER, IpControlClient
from devialet_web.pair import StereoPair
from devialet_web.tone import parse_equalizer
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

REAL_GET = {"availablePresets": ["custom", "flat", "voice"],
            "currentEqualization": {"high": {"gain": 0}, "low": {"gain": 0}},
            "customEqualization": {"high": {"gain": 0}, "low": {"gain": 0}}, "enabled": True,
            "gainRange": {"max": 6, "min": -6, "stepPrecision": 1}, "preset": "flat"}


def make(fake: FakePair) -> TestClient:
    return local_client(create_app([LEFT, RIGHT], transport=fake.transport()))


def run_pair(fake, fn):
    async def go():
        async with httpx.AsyncClient(transport=fake.transport()) as http:
            pair = StereoPair([IpControlClient(LEFT, http), IpControlClient(RIGHT, http)])
            pair.tone._interval = 0.01
            pair.tone._confirm_delay = 0.01
            return await fn(pair)
    return asyncio.run(go())


def eq_posts(fake):
    return [(h, json.loads(b)) for h, p, b in fake.posts if p == EQUALIZER]


# --- protocol ------------------------------------------------------------------

def test_equalizer_is_the_only_new_write_path():
    assert EQUALIZER == "/systems/current/settings/audio/equalizer"
    assert EQUALIZER in ALLOWED_POSTS and len(ALLOWED_POSTS) == 7


def test_parse_real_speaker_answer():
    eq = parse_equalizer(REAL_GET)
    assert eq == {"bass": 0, "treble": 0, "preset": "flat", "enabled": True,
                  "min": -6, "max": 6, "step": 1}


def test_parse_uses_current_values_and_clamps_announced_range():
    data = {**REAL_GET, "preset": "voice",
            "currentEqualization": {"low": {"gain": -2.0}, "high": {"gain": 1.5}},
            "gainRange": {"min": -12, "max": 3, "stepPrecision": 0.5}}
    eq = parse_equalizer(data)
    assert (eq["bass"], eq["treble"], eq["preset"]) == (-2, 1.5, "voice")
    assert (eq["min"], eq["max"]) == (-6, 3)      # never wider than the -6..+6 hard bounds


@pytest.mark.parametrize("data", [{}, None, {"currentEqualization": {"low": {"gain": "x"}}},
                                  {"currentEqualization": {"low": {"gain": True}}}])
def test_parse_unsupported_or_malformed(data):
    eq = parse_equalizer(data)
    assert eq["bass"] is None and eq["treble"] is None


def test_client_write_body_matches_the_official_app():
    sent = []

    def handler(request):
        sent.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, text="")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await IpControlClient(RIGHT, http).set_equalizer(3, -2)
    asyncio.run(go())
    assert sent == [("POST", "/ipcontrol/v1" + EQUALIZER,
                     {"preset": "custom", "customEqualization": {"low": {"gain": 3},
                                                                 "high": {"gain": -2}}})]


@pytest.mark.parametrize("bad", [7, -7, float("nan"), float("inf"), "3", True, None])
def test_client_refuses_out_of_range_gain_before_sending(bad):
    def handler(request):
        raise AssertionError("nothing must be sent")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await IpControlClient(RIGHT, http).set_equalizer(bad, 0)
    with pytest.raises(ValueError):
        asyncio.run(go())


# --- reads -----------------------------------------------------------------------

def test_status_reads_both_speakers_without_writing():
    fake = FakePair()
    fake.eq = {"low": 2, "high": -1}
    fake.eq_preset = "custom"
    st = run_pair(fake, lambda p: p.tone.status())
    assert st["available"] and (st["bass"], st["treble"]) == (2, -1)
    assert st["leader"] == RIGHT and st["consistent"] is True and st["errors"] == []
    assert {s["host"] for s in st["speakers"]} == {LEFT, RIGHT}
    assert fake.posts == []


def test_status_reports_inconsistent_pair():
    fake = FakePair()
    fake.routes[LEFT]["/systems/current/settings/audio/equalizer"] = (200, {
        **REAL_GET, "currentEqualization": {"low": {"gain": 4}, "high": {"gain": 0}}})
    fake.eq_hosts = (RIGHT,)
    st = run_pair(fake, lambda p: p.tone.status())
    assert st["available"] and st["bass"] == 0       # the leader is authoritative
    assert st["consistent"] is False
    assert "différentes" in " ".join(st["errors"])


def test_status_follower_down_still_available():
    fake = FakePair()
    fake.routes[LEFT]["/devices/current"] = (200, httpx.ConnectError("down"))
    st = run_pair(fake, lambda p: p.tone.status())
    assert st["available"] and st["consistent"] is True
    assert [s["host"] for s in st["speakers"]] == [RIGHT]


def test_status_unsupported_system():
    fake = FakePair()
    fake.routes[RIGHT]["/systems/current"] = (200, {"systemName": "My Phantom System",
                                                    "availableFeatures": ["balance"]})
    st = run_pair(fake, lambda p: p.tone.status())
    assert st["available"] is False and "non prise en charge" in st["errors"][0]


def test_status_leader_read_error():
    fake = FakePair()
    fake.eq_hosts = (LEFT,)
    fake.routes[RIGHT]["/systems/current/settings/audio/equalizer"] = (
        200, {"error": {"code": "InternalError", "message": "busy"}})
    st = run_pair(fake, lambda p: p.tone.status())
    assert st["available"] is False and "InternalError" in st["errors"][0]


def test_status_no_speaker_reachable():
    fake = FakePair()
    for host in (LEFT, RIGHT):
        fake.routes[host]["/devices/current"] = (200, httpx.ConnectTimeout("t"))
    st = run_pair(fake, lambda p: p.tone.status())
    assert st["available"] is False and st["errors"] == ["Aucune enceinte joignable"]


# --- writes ------------------------------------------------------------------------

def test_set_bass_merges_current_treble_and_writes_to_leader_only():
    fake = FakePair()
    fake.eq, fake.eq_preset = {"low": 0, "high": -3}, "custom"
    st = run_pair(fake, lambda p: p.tone.apply(bass=4))
    assert eq_posts(fake) == [(RIGHT, {"preset": "custom", "customEqualization": {
        "low": {"gain": 4}, "high": {"gain": -3}}})]
    assert st["confirmed"] and st["written"] and (st["bass"], st["treble"]) == (4, -3)
    assert st["requested"] == {"bass": 4, "treble": -3}


def test_set_treble_then_both():
    fake = FakePair()
    run_pair(fake, lambda p: p.tone.apply(treble=-6))
    assert fake.eq == {"low": 0, "high": -6}
    run_pair(fake, lambda p: p.tone.apply(bass=6, treble=6))
    assert fake.eq == {"low": 6, "high": 6}
    assert len(eq_posts(fake)) == 2


def test_reset_to_zero():
    fake = FakePair()
    fake.eq, fake.eq_preset = {"low": 5, "high": -2}, "custom"
    st = run_pair(fake, lambda p: p.tone.apply(bass=0))
    assert fake.eq == {"low": 0, "high": -2} and st["confirmed"]
    st = run_pair(fake, lambda p: p.tone.apply(bass=0, treble=0))
    assert fake.eq == {"low": 0, "high": 0} and st["bass"] == st["treble"] == 0


def test_unchanged_value_is_not_written():
    fake = FakePair()
    st = run_pair(fake, lambda p: p.tone.apply(bass=0, treble=0))   # already flat 0/0
    assert eq_posts(fake) == [] and st["written"] is False and st["confirmed"]


def test_voice_preset_is_replaced_even_with_same_values():
    fake = FakePair()
    fake.eq_preset = "voice"
    st = run_pair(fake, lambda p: p.tone.apply(bass=0))
    assert len(eq_posts(fake)) == 1 and fake.eq_preset == "custom" and st["written"]


@pytest.mark.parametrize("kwargs", [{"bass": 7}, {"treble": -7}, {"bass": 1.5}, {"bass": "2"},
                                    {"treble": True}, {}])
def test_invalid_values_are_rejected_without_writing(kwargs):
    fake = FakePair()
    with pytest.raises(ValueError):
        run_pair(fake, lambda p: p.tone.apply(**kwargs))
    assert eq_posts(fake) == []


def test_announced_range_narrower_than_hard_bounds_is_enforced():
    fake = FakePair()
    narrow = {**REAL_GET, "gainRange": {"min": -3, "max": 3, "stepPrecision": 1}}
    fake.equalizer_body = lambda: narrow
    with pytest.raises(ValueError, match="hors plage"):
        run_pair(fake, lambda p: p.tone.apply(bass=4))
    assert eq_posts(fake) == []


def test_write_refused_without_identified_leader():
    fake = FakePair()
    fake.routes[RIGHT]["/devices/current"] = (200, httpx.ConnectError("down"))
    with pytest.raises(Exception, match="leader du système introuvable"):
        run_pair(fake, lambda p: p.tone.apply(bass=2))
    assert eq_posts(fake) == []


def test_unconfirmed_write_is_reported():
    fake = FakePair()
    fake.eq_apply = False
    st = run_pair(fake, lambda p: p.tone.apply(bass=3))
    assert st["written"] and st["confirmed"] is False and st["bass"] == 0
    assert any("non confirmé" in e for e in st["errors"])


def test_concurrent_bass_and_treble_never_lose_a_band():
    """Two requests at once (bass from one tab, treble from another): the lock
    serialises read-merge-write so both changes end up applied."""
    fake = FakePair()

    async def both(pair):
        return await asyncio.gather(pair.tone.apply(bass=3), pair.tone.apply(treble=-2))
    run_pair(fake, both)
    assert fake.eq == {"low": 3, "high": -2}
    bodies = [b["customEqualization"] for _, b in eq_posts(fake)]
    assert bodies[-1] == {"low": {"gain": 3}, "high": {"gain": -2}}


def test_writes_are_spaced():
    fake = FakePair()
    times = []
    original = fake.handler

    def timed(request):
        if request.method == "POST":
            times.append(asyncio.get_event_loop().time())
        return original(request)
    fake.handler = timed

    async def burst(pair):
        pair.tone._interval = 0.1
        for value in (1, 2, 3):
            await pair.tone.apply(bass=value)
    run_pair(fake, burst)
    assert len(times) == 3
    assert all(b - a >= 0.09 for a, b in zip(times, times[1:]))


# --- HTTP API ----------------------------------------------------------------------

def test_api_get_and_post():
    fake = FakePair()
    with make(fake) as client:
        st = client.get("/api/tone").json()
        assert st["available"] and st["bass"] == 0 and fake.posts == []
        res = client.post("/api/tone", json={"treble": 2})
        assert res.status_code == 200 and res.json()["treble"] == 2 and res.json()["confirmed"]
        assert fake.eq == {"low": 0, "high": 2}


@pytest.mark.parametrize("body", [{"bass": 7}, {"bass": -7}, {"bass": 2.0}, {"bass": "2"},
                                  {"treble": True}, {}, {"bass": None}])
def test_api_rejects_invalid_bodies(body):
    fake = FakePair()
    with make(fake) as client:
        assert client.post("/api/tone", json=body).status_code == 422
    assert eq_posts(fake) == []


def test_api_network_errors():
    fake = FakePair()
    with make(fake) as client:
        fake.eq_hosts = ()
        fake.routes[RIGHT]["/systems/current/settings/audio/equalizer"] = (
            200, httpx.ConnectTimeout("t"))
        res = client.post("/api/tone", json={"bass": 1})
        assert res.status_code == 503 and "injoignable" in res.json()["detail"]
        fake.routes[RIGHT]["/systems/current/settings/audio/equalizer"] = (
            200, {"error": {"code": "InvalidValue"}})
        res = client.post("/api/tone", json={"bass": 1})
        assert res.status_code == 502 and res.json()["detail"]["code"] == "InvalidValue"
        assert client.get("/api/tone").json()["available"] is False
    assert eq_posts(fake) == []


def test_api_reflects_changes_made_from_the_devialet_app():
    fake = FakePair()
    with make(fake) as client:
        assert client.get("/api/tone").json()["bass"] == 0
        fake.eq, fake.eq_preset = {"low": -4, "high": 1}, "custom"   # changed elsewhere
        st = client.get("/api/tone").json()
        assert (st["bass"], st["treble"], st["preset"]) == (-4, 1, "custom")
    assert fake.posts == []


def test_api_without_selected_system():
    from devialet_web.discovery import DiscoveryService, KnownSpeakers
    fake = FakePair()
    app = create_app(transport=fake.transport(),
                     discovery=DiscoveryService(KnownSpeakers(None), native=False,
                                                transport=fake.transport()))
    with local_client(app) as client:
        st = client.get("/api/tone").json()
        assert st["available"] is False and st["errors"] == ["Aucun système sélectionné"]
        assert client.post("/api/tone", json={"bass": 1}).status_code == 503


def test_startup_and_state_never_write_tone():
    fake = FakePair()
    with make(fake) as client:
        client.get("/api/state")
        client.get("/api/tone")
    assert fake.posts == []
