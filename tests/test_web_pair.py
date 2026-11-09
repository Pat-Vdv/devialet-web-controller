import asyncio

import httpx
import pytest

from devialet_web.ipcontrol import IpControlClient, IpControlError
from devialet_web.pair import StereoPair, VolumeThrottler, parse_source
from tests.fake_speakers import ACTIVE_SOURCE, LEFT, RIGHT, FakePair
from tests.fake_speakers import no_real_network  # noqa: F401 (autouse fixture)


async def with_pair(fake: FakePair, fn, **kw):
    async with httpx.AsyncClient(transport=fake.transport()) as http:
        pair = StereoPair([IpControlClient(LEFT, http), IpControlClient(RIGHT, http)], **kw)
        result = await fn(pair)
        await pair.volume_throttle.drain()
        return result


def run(coro):
    return asyncio.run(coro)


def test_discovery_finds_leader_roles_and_system():
    fake = FakePair()
    state = run(with_pair(fake, lambda p: p.state()))
    assert state["leader"] == RIGHT and state["leaderDetected"] is True
    assert state["system"]["name"] == "My Phantom System"
    roles = {s["host"]: (s["roleLabel"], s["isLeader"]) for s in state["speakers"]}
    assert roles == {LEFT: ("Gauche", False), RIGHT: ("Droite", True)}
    assert state["volume"] == 31
    assert state["source"]["name"] == "bluetooth" and state["source"]["title"] == "One More Time"
    assert fake.posts == []  # reading state never writes


def test_state_reads_volume_and_source_from_leader_only():
    fake = FakePair()
    run(with_pair(fake, lambda p: p.state()))
    non_device = {(h, path) for h, m, path, _ in fake.requests if path != "/devices/current"}
    assert {h for h, _ in non_device} == {RIGHT}


def test_no_current_source_error_code_is_not_an_error():
    fake = FakePair()
    fake.routes[RIGHT]["/groups/current/sources/current"] = (
        200, {"error": {"code": "NoCurrentSource", "message": "No current source"}})
    state = run(with_pair(fake, lambda p: p.state()))
    assert state["source"] == {"present": False, "reason": "NoCurrentSource"}
    assert state["errors"] == []


@pytest.mark.parametrize("body", ["", "{}", '{"source": null}'])
def test_empty_or_partial_source_bodies(body):
    fake = FakePair()
    fake.routes[RIGHT]["/groups/current/sources/current"] = (200, body)
    state = run(with_pair(fake, lambda p: p.state()))
    assert state["source"]["present"] is False and state["errors"] == []


def test_other_source_errors_are_reported():
    fake = FakePair()
    fake.routes[RIGHT]["/groups/current/sources/current"] = (200, {"error": {"code": "Busy"}})
    state = run(with_pair(fake, lambda p: p.state()))
    assert state["source"] == {"present": False}
    assert any("Busy" in e for e in state["errors"])


def test_leader_fallback_and_unreachable_speaker():
    fake = FakePair()
    fake.routes[RIGHT]["/devices/current"] = (200, httpx.ConnectTimeout("t"))
    state = run(with_pair(fake, lambda p: p.state()))
    right = next(s for s in state["speakers"] if s["host"] == RIGHT)
    assert right["reachable"] is False and "timeout" in right["error"]
    assert state["leader"] == LEFT and state["leaderDetected"] is False


def test_all_unreachable():
    fake = FakePair()
    for host in (LEFT, RIGHT):
        fake.routes[host]["/devices/current"] = (200, httpx.ConnectError("x"))
    state = run(with_pair(fake, lambda p: p.state()))
    assert state["leader"] is None and state["errors"] == ["Aucune enceinte joignable"]


def test_set_volume_is_capped_and_sent_to_leader():
    fake = FakePair()
    sent = run(with_pair(fake, lambda p: p.set_volume(80)))
    assert sent == 50
    assert fake.posts == [(RIGHT, "/systems/current/sources/current/soundControl/volume",
                           b'{"volume": 50}')]


def test_configured_cap_cannot_exceed_hard_cap():
    fake = FakePair()

    async def check(p):
        return p.max_volume, await p.set_volume(45)

    assert run(with_pair(fake, check, max_volume=99)) == (50, 45)
    assert run(with_pair(FakePair(), check, max_volume=40)) == (40, 40)


def test_step_volume_up_down_and_cap():
    fake = FakePair()
    assert run(with_pair(fake, lambda p: p.step_volume(-1))) == 29
    fake.volume = 49
    assert run(with_pair(fake, lambda p: p.step_volume(1))) == 50
    fake.volume = 50
    before = len(fake.posts)
    assert run(with_pair(fake, lambda p: p.step_volume(1))) == 50
    assert len(fake.posts) == before  # at the cap: no write at all


def test_step_down_from_above_cap_only_lowers():
    fake = FakePair()
    fake.volume = 70  # set from the phone, above our cap
    assert run(with_pair(fake, lambda p: p.step_volume(1))) == 70
    assert fake.posts == []
    assert run(with_pair(fake, lambda p: p.step_volume(-1))) == 50


def test_unreadable_volume_blocks_step():
    fake = FakePair()

    async def broken(p):
        p.clients[1].volume = lambda: asyncio.sleep(0, result=None)
        return await p.step_volume(1)

    with pytest.raises(IpControlError):
        run(with_pair(fake, broken))


def test_playback_targets_leader():
    fake = FakePair()
    run(with_pair(fake, lambda p: p.playback("pause")))
    assert fake.posts == [(RIGHT, "/groups/current/sources/current/playback/pause", b"")]


def test_throttler_coalesces_bursts():
    sent = []

    async def send(v):
        sent.append(v)

    async def scenario():
        t = VolumeThrottler(send, min_interval=0.05)
        for v in range(10, 30):
            t.submit(v)
            await asyncio.sleep(0)
        await t.drain()
        return t

    t = run(scenario())
    assert sent[-1] == 29 and len(sent) <= 3 and t.pending is None


def test_throttler_records_errors():
    async def send(v):
        raise IpControlError("Busy")

    async def scenario():
        t = VolumeThrottler(send, min_interval=0)
        t.submit(10)
        await t.drain()
        return t

    assert run(scenario()).last_error == "Busy"


def test_parse_source_minimal_fields():
    src = parse_source({"source": {"type": "spotify", "displayName": "Spotify Connect"}})
    assert src["present"] and src["name"] == "Spotify Connect" and src["title"] is None
    full = parse_source(ACTIVE_SOURCE)
    assert full["availableOperations"] == ["next", "pause", "play", "previous"]
