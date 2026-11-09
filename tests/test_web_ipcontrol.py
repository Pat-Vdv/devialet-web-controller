import asyncio

import httpx
import pytest

from devialet_web.ipcontrol import (
    ALLOWED_POSTS,
    ForbiddenCommand,
    IpControlClient,
    IpControlError,
    SpeakerUnreachable,
    parse_body,
)
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)


def resp(status=200, **kw) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "http://x/"), **kw)


def test_parse_body_ok_and_empty():
    assert parse_body(resp(json={"volume": 28})) == {"volume": 28}
    assert parse_body(resp(text="")) is None
    assert parse_body(resp(text="   \n")) is None


def test_parse_body_error_key_with_http_200():
    with pytest.raises(IpControlError) as exc:
        parse_body(resp(json={"error": {"code": "NoCurrentSource", "message": "none"}}))
    assert exc.value.code == "NoCurrentSource" and exc.value.status == 200


def test_parse_body_error_variants():
    with pytest.raises(IpControlError) as exc:
        parse_body(resp(json={"error": "Busy"}))
    assert exc.value.code == "Busy"
    with pytest.raises(IpControlError) as exc:
        parse_body(resp(500, text="boom"))
    assert exc.value.code == "HTTP500"
    with pytest.raises(IpControlError) as exc:
        parse_body(resp(200, text="<html>"))
    assert exc.value.code == "InvalidJson"


def run(coro):
    return asyncio.run(coro)


async def _with_client(handler, fn):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await fn(IpControlClient("10.0.0.1", http))


def test_post_allow_list_blocks_destructive_endpoints():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text="")

    for path in ("/systems/create", "/devices/current/leaveSystem", "/systems/current",
                 "/groups/current/add", "/devices/current/powerOff",
                 "/systems/current/settings/audio/balance", "/devices/current/update",
                 "/groups/current/sources/current/soundControl/volumeUp",
                 "/devices/current/bluetooth/startAdvertising", "/systems/current/bluetooth"):
        with pytest.raises(ForbiddenCommand):
            run(_with_client(handler, lambda c, p=path: c.post(p)))
    with pytest.raises(ForbiddenCommand):
        run(_with_client(handler, lambda c: c.playback("stop")))
    assert seen == []  # nothing left the client
    assert all(p.startswith(("/systems/current/sources/current/soundControl/volume",
                             "/groups/current/sources/current/playback/"))
               or p in ("/systems/current/bluetooth/startAdvertising",
                        "/systems/current/settings/audio/equalizer") for p in ALLOWED_POSTS)


def test_set_volume_sends_json_post():
    captured = {}

    def handler(request):
        captured.update(method=request.method, path=request.url.path,
                        body=request.content, ctype=request.headers.get("content-type"))
        return httpx.Response(200, text="")

    run(_with_client(handler, lambda c: c.set_volume(28)))
    assert captured == {"method": "POST",
                        "path": "/ipcontrol/v1/systems/current/sources/current/soundControl/volume",
                        "body": b'{"volume": 28}', "ctype": "application/json"}


def test_playback_posts_empty_body():
    captured = {}

    def handler(request):
        captured.update(path=request.url.path, body=request.content)
        return httpx.Response(200, text="")

    run(_with_client(handler, lambda c: c.playback("next")))
    assert captured == {"path": "/ipcontrol/v1/groups/current/sources/current/playback/next",
                        "body": b""}


def test_timeout_and_connection_errors_become_unreachable():
    def timeout(request):
        raise httpx.ConnectTimeout("t", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (timeout, refused):
        with pytest.raises(SpeakerUnreachable):
            run(_with_client(handler, lambda c: c.volume()))


def test_volume_returns_none_on_unexpected_shape():
    assert run(_with_client(lambda r: httpx.Response(200, json={}), lambda c: c.volume())) is None
