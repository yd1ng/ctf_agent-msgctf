import asyncio
from types import SimpleNamespace

import httpx
import pytest

from backend.agents.coordinator_loop import _auto_spawn_unsolved, _rotate_msgctf_swarm
from backend.ctfd import SubmitResult
from backend.msgctf import MSGCTFClient
from backend.tools.core import do_submit_flag

CHALLENGE_ID = "12345678-1234-1234-1234-123456789abc"
FILE_ID = "abcdefab-1234-1234-1234-abcdefabcdef"


@pytest.mark.asyncio
async def test_login_refresh_download_and_flag_codes(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        path = request.url.path
        if path.endswith("/auth/login"):
            assert request.content == b'{"login_id":"player","password":"secret"}'
            return httpx.Response(200, json={"code": "SUCCESS", "data": {"access_token": "old", "refresh_token": "refresh"}})
        if path.endswith("/auth/refresh"):
            return httpx.Response(200, json={"code": "SUCCESS", "data": {"access_token": "new"}})
        if path.endswith("/board/opened_challenges") and request.headers.get("authorization") == "Bearer old":
            return httpx.Response(401, json={"code": "TOKEN_EXPIRED", "data": None})
        if path.endswith("/board/opened_challenges"):
            return httpx.Response(200, json={"code": "SUCCESS", "data": {"opened_challenges": [{"challenge_id": CHALLENGE_ID, "title": "Demo", "category": "MISC", "score": 100, "is_solved": False}]}})
        if path.endswith(f"/challenges/{CHALLENGE_ID}"):
            return httpx.Response(200, json={"code": "SUCCESS", "data": {"challenge_id": CHALLENGE_ID, "title": "Demo", "category": "MISC", "score": 100, "description": "<p>Read me</p>", "requires_instance": False, "files": [{"file_id": FILE_ID, "file_name": "../user-files.zip", "download_url": "https://evil.example/steal"}]}})
        if path.endswith(f"/files/{FILE_ID}/download"):
            assert request.headers.get("authorization") == "Bearer new"
            return httpx.Response(200, content=b"PK\x03\x04demo")
        if path.endswith("/submit"):
            return httpx.Response(429, json={"code": "TOO_MANY_ATTEMPTS", "message": "wait", "data": {"retry_after_seconds": 28}})
        raise AssertionError(path)

    adapter = MSGCTFClient("https://ctf.example", login_id="player", password="secret")
    adapter._client = httpx.AsyncClient(base_url=adapter.base_url + "/", transport=httpx.MockTransport(handler))
    try:
        stubs = await adapter.fetch_challenge_stubs()
        assert stubs[0]["name"] == f"Demo [{CHALLENGE_ID}]"
        challenges = await adapter.fetch_all_challenges()
        directory = await adapter.pull_challenge(challenges[0], str(tmp_path))
        assert (tmp_path / CHALLENGE_ID / "distfiles" / f"{FILE_ID}-user-files.zip").read_bytes() == b"PK\x03\x04demo"
        assert (tmp_path / CHALLENGE_ID / "metadata.yml").exists()
        assert directory == str(tmp_path / CHALLENGE_ID)
        submitted = await adapter.submit_flag(stubs[0]["name"], " FLAG{x} ")
        assert submitted.status == "rate_limited"
        assert "28s" in submitted.display
        assert all("evil.example" not in path for _, path, _ in calls)
        assert any(path.endswith("/auth/refresh") for _, path, _ in calls)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_opened_challenges_rotate_after_time_slice(monkeypatch):
    names = ["new", "old"]
    attempts = []

    async def record_spawn(_deps, name):
        attempts.append(name)

    monkeypatch.setattr("backend.agents.coordinator_loop._auto_spawn_one", record_spawn)
    monkeypatch.setattr("backend.agents.coordinator_loop.time.monotonic", lambda: 1000)
    swarm = SimpleNamespace(cancel_event=asyncio.Event())
    swarm.kill = swarm.cancel_event.set
    deps = SimpleNamespace(
        settings=SimpleNamespace(platform="msgctf", msgctf_swarm_timeslice_seconds=900),
        max_concurrent_challenges=1,
        ctfd=SimpleNamespace(opened_order=names),
        swarm_started_at={"old": 50},
        swarm_tasks={"old": SimpleNamespace(done=lambda: False)},
        swarms={"old": swarm},
        results={},
    )
    poller = SimpleNamespace(known_challenges=set(names), known_solved=set())
    _rotate_msgctf_swarm(deps, poller)
    assert swarm.cancel_event.is_set()
    await _auto_spawn_unsolved(deps, poller)
    assert attempts == ["new", "old"]


@pytest.mark.asyncio
async def test_already_solved_does_not_confirm_candidate_flag():
    class AlreadySolvedClient:
        async def submit_flag(self, _challenge_name, _flag):
            return SubmitResult("already_solved", "team member solved it", "ALREADY SOLVED")

    display, confirmed = await do_submit_flag(AlreadySolvedClient(), "Demo", "wrong candidate")
    assert display == "ALREADY SOLVED"
    assert confirmed is False


@pytest.mark.asyncio
async def test_instance_replacement_and_release_specific_file(tmp_path, monkeypatch):
    previous_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    new_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    state = {"created": False}

    async def instant_sleep(_seconds):
        return None

    monkeypatch.setattr("backend.msgctf.asyncio.sleep", instant_sleep)

    def handler(request):
        path = request.url.path
        if path.endswith("/teams/me/instances"):
            if state["created"]:
                return httpx.Response(200, json={"code": "SUCCESS", "data": {"instance_id": new_id, "challenge_id": CHALLENGE_ID, "status": "RUNNING", "endpoints": [{"service_url": "tls://challenge.example:443", "server_name": "challenge.example"}]}})
            return httpx.Response(200, json={"code": "SUCCESS", "data": {"instance_id": previous_id, "challenge_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "status": "RUNNING"}})
        if path.endswith("/instances"):
            body = __import__("json").loads(request.content)
            if "replace_instance_id" not in body:
                return httpx.Response(409, json={"code": "INSTANCE_REPLACEMENT_REQUIRED", "data": None})
            assert body["replace_instance_id"] == previous_id
            state["created"] = True
            return httpx.Response(202, json={"code": "SUCCESS", "data": {"instance_id": new_id, "status": "REQUESTED"}})
        if path.endswith(f"/challenges/{CHALLENGE_ID}"):
            return httpx.Response(200, json={"code": "SUCCESS", "data": {"files": [{"file_id": FILE_ID, "file_name": "user-files.zip"}]}})
        if path.endswith(f"/files/{FILE_ID}/download"):
            return httpx.Response(200, content=b"zip")
        raise AssertionError(path)

    adapter = MSGCTFClient("https://ctf.example", access_token="token")
    adapter._client = httpx.AsyncClient(base_url=adapter.base_url + "/", transport=httpx.MockTransport(handler))
    challenge = {"id": CHALLENGE_ID, "name": f"Pwn [{CHALLENGE_ID}]", "category": "PWN", "value": 500, "description": "test", "requires_instance": True, "files": []}
    try:
        await adapter.pull_challenge(challenge, str(tmp_path))
        meta = (tmp_path / CHALLENGE_ID / "metadata.yml").read_text()
        assert "openssl s_client -connect challenge.example:443" in meta
        assert (tmp_path / CHALLENGE_ID / "distfiles" / f"{FILE_ID}-user-files.zip").read_bytes() == b"zip"
    finally:
        await adapter.close()
