from fastapi.testclient import TestClient

from tests.local_client import local_client

from devialet_web.app import create_app
from tests.fake_speakers import LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)

VOLUME_PATH = "/systems/current/sources/current/soundControl/volume"


def make(fake: FakePair, **kw) -> TestClient:
    return local_client(create_app([LEFT, RIGHT], transport=fake.transport(), **kw))


def test_startup_only_reads():
    fake = FakePair()
    with make(fake):
        pass
    assert fake.requests and all(m == "GET" for _, m, _, _ in fake.requests)
    assert fake.volume == 31  # untouched


def test_state_endpoint():
    fake = FakePair()
    with make(fake) as client:
        state = client.get("/api/state").json()
    assert state["system"]["name"] == "My Phantom System"
    assert state["leader"] == RIGHT
    assert state["maxVolume"] == 50 and state["volume"] == 31
    assert state["source"]["artist"] == "Daft Punk"
    assert fake.posts == []


def test_set_volume_and_validation():
    fake = FakePair()
    with make(fake) as client:
        assert client.post("/api/volume", json={"volume": 51}).status_code == 422
        assert client.post("/api/volume", json={"volume": -1}).status_code == 422
        assert fake.posts == []
        r = client.post("/api/volume", json={"volume": 28})
        assert r.status_code == 200 and r.json() == {"volume": 28}
    assert fake.posts == [(RIGHT, VOLUME_PATH, b'{"volume": 28}')]


def test_lower_configured_cap_is_enforced():
    fake = FakePair()
    with make(fake, max_volume=30) as client:
        assert client.post("/api/volume", json={"volume": 35}).status_code == 422
        assert client.get("/api/state").json()["maxVolume"] == 30
    assert fake.posts == []


def test_volume_step_endpoint():
    fake = FakePair()
    with make(fake) as client:
        assert client.post("/api/volume/step", json={"direction": 1}).json() == {"volume": 33}
        assert client.post("/api/volume/step", json={"direction": 5}).status_code == 422


def test_playback_endpoints():
    fake = FakePair()
    with make(fake) as client:
        for action in ("play", "pause", "next", "previous"):
            assert client.post(f"/api/playback/{action}").status_code == 200
        assert client.post("/api/playback/stop").status_code == 404
    assert [p for _, p, _ in fake.posts] == [
        f"/groups/current/sources/current/playback/{a}" for a in ("play", "pause", "next", "previous")]


def test_speaker_error_is_surfaced_as_502():
    fake = FakePair()
    original = fake.handler

    def handler(request):
        if request.method == "POST":
            fake.requests.append((request.url.host, "POST", request.url.path, request.content))
            import httpx
            return httpx.Response(200, json={"error": {"code": "NotAllowed", "message": "nope"}})
        return original(request)

    fake.handler = handler
    with make(fake) as client:
        r = client.post("/api/playback/next")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "NotAllowed"


def test_offline_pair():
    import httpx
    fake = FakePair()
    for host in (LEFT, RIGHT):
        fake.routes[host]["/devices/current"] = (200, httpx.ConnectError("x"))
    with make(fake) as client:
        assert client.get("/api/state").json()["leader"] is None
        assert client.post("/api/volume", json={"volume": 20}).status_code == 503
        assert client.post("/api/playback/play").status_code == 503
    assert fake.posts == []


def test_static_frontend_served():
    with make(FakePair()) as client:
        r = client.get("/")
        assert r.status_code == 200 and "app.js" in r.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/app.css").status_code == 200
