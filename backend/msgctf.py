"""MSG CTF participant API adapter for the existing CTFd solver pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from markdownify import markdownify as html2md

from backend.ctfd import SubmitResult

logger = logging.getLogger(__name__)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MAX_FILE_BYTES = 110 * 1024 * 1024


@dataclass
class MSGCTFClient:
    base_url: str
    login_id: str = ""
    password: str = ""
    access_token: str = ""
    refresh_token: str = ""
    manage_instances: bool = True
    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _auth_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _challenges: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _opened_order: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        base = self.base_url.rstrip("/")
        if not base.endswith("/api/v1"):
            base += "/api/v1"
        if not base.startswith(("https://", "http://")):
            raise ValueError("MSGCTF_URL must be an HTTP(S) URL")
        self.base_url = base

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url + "/", timeout=30, follow_redirects=False)
        return self._client

    @staticmethod
    def _envelope(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"MSG CTF returned non-JSON response ({response.status_code})") from exc
        if not isinstance(data, dict) or not isinstance(data.get("code"), str):
            raise RuntimeError("MSG CTF returned an invalid API envelope")
        return data

    async def _login(self) -> None:
        if not self.login_id or not self.password:
            raise RuntimeError("MSGCTF_LOGIN_ID and MSGCTF_PASSWORD are required when no valid token is available")
        response = await (await self._http()).post("auth/login", json={"login_id": self.login_id, "password": self.password})
        data = self._envelope(response)
        if data["code"] != "SUCCESS":
            raise RuntimeError(f"MSG CTF login failed: {data['code']}: {data.get('message', '')}")
        tokens = data.get("data") or {}
        self.access_token = tokens["access_token"]
        self.refresh_token = tokens["refresh_token"]

    async def _refresh(self) -> None:
        if not self.refresh_token:
            await self._login()
            return
        response = await (await self._http()).post("auth/refresh", json={"refresh_token": self.refresh_token})
        data = self._envelope(response)
        if data["code"] != "SUCCESS":
            if self.login_id and self.password:
                await self._login()
                return
            raise RuntimeError(f"MSG CTF refresh failed: {data['code']}")
        self.access_token = (data.get("data") or {})["access_token"]

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.access_token:
            async with self._auth_lock:
                if not self.access_token:
                    await (self._refresh() if self.refresh_token else self._login())
        token = self.access_token
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        client = await self._http()
        response = await client.request(method, path.lstrip("/"), headers=headers, **kwargs)
        envelope = self._envelope(response)
        if response.status_code == 401 and envelope["code"] in {"TOKEN_EXPIRED", "TOKEN_INVALID"}:
            async with self._auth_lock:
                if self.access_token == token:
                    await self._refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
            response = await client.request(method, path.lstrip("/"), headers=headers, **kwargs)
            envelope = self._envelope(response)
        return envelope

    async def _success(self, method: str, path: str, **kwargs: Any) -> Any:
        result = await self._request(method, path, **kwargs)
        if result["code"] != "SUCCESS":
            raise RuntimeError(f"MSG CTF {path}: {result['code']}: {result.get('message', '')}")
        return result.get("data")

    @staticmethod
    def _name(challenge: dict[str, Any]) -> str:
        return f"{challenge.get('title') or 'Challenge'} [{challenge['challenge_id']}]"

    async def _opened(self) -> list[dict[str, Any]]:
        data = await self._success("GET", "board/opened_challenges") or {}
        opened = data.get("opened_challenges") or []
        if not isinstance(opened, list):
            raise RuntimeError("Invalid opened_challenges payload")
        valid = [ch for ch in opened if isinstance(ch, dict) and UUID.fullmatch(str(ch.get("challenge_id", "")))]
        for ch in valid:
            self._challenges[self._name(ch)] = ch
        # The backend returns opened_at ascending. Newer opens have the best
        # chance of still receiving the time-limited extra dice reward.
        self._opened_order = [self._name(ch) for ch in reversed(valid)]
        return valid

    @property
    def opened_order(self) -> list[str]:
        return list(self._opened_order)

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        return [{"name": self._name(ch), "title": ch.get("title", ""), "id": ch["challenge_id"]} for ch in await self._opened()]

    async def fetch_solved_names(self) -> set[str]:
        return {self._name(ch) for ch in await self._opened() if ch.get("is_solved") is True}

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        result = []
        for ch in await self._opened():
            try:
                detail = await self._success("GET", f"challenges/{ch['challenge_id']}")
            except Exception as exc:
                logger.warning("Could not fetch challenge %s: %s", ch["challenge_id"], exc)
                continue
            if not isinstance(detail, dict):
                continue
            result.append({
                **detail, "id": ch["challenge_id"], "name": self._name(ch),
                "title": detail.get("title") or ch.get("title"),
                "category": detail.get("category") or ch.get("category", ""),
                "value": detail.get("score", ch.get("score", 0)),
                "solves": detail.get("solved_team_count", 0),
            })
        return result

    @staticmethod
    def _connection_info(instance: dict[str, Any] | None) -> str:
        if not instance or instance.get("status") != "RUNNING":
            return ""
        endpoints = instance.get("endpoints") or []
        if endpoints:
            commands = []
            for endpoint in endpoints:
                url = str(endpoint.get("service_url") or "")
                parsed = urlparse(url)
                if parsed.scheme == "tcp" and parsed.hostname and parsed.port:
                    commands.append(f"nc {shlex.quote(parsed.hostname)} {parsed.port}")
                elif parsed.scheme == "tls" and parsed.hostname and parsed.port:
                    server_name = endpoint.get("server_name") or parsed.hostname
                    address = shlex.quote(f"{parsed.hostname}:{parsed.port}")
                    commands.append(f"openssl s_client -connect {address} -servername {shlex.quote(str(server_name))}")
                elif parsed.scheme in {"http", "https"}:
                    commands.append(url)
            return "\n".join(commands)
        host = instance.get("host") or ""
        if not host:
            return ""
        parsed_host = urlparse(str(host))
        if parsed_host.scheme == "tcp" and parsed_host.hostname and parsed_host.port:
            return f"nc {shlex.quote(parsed_host.hostname)} {parsed_host.port}"
        if parsed_host.scheme == "tls" and parsed_host.hostname and parsed_host.port:
            address = shlex.quote(f"{parsed_host.hostname}:{parsed_host.port}")
            return f"openssl s_client -connect {address} -servername {shlex.quote(parsed_host.hostname)}"
        ports = instance.get("ports") or []
        if ports and not str(host).startswith(("http://", "https://")):
            port = ports[0]
            number = port.get("port") if isinstance(port, dict) else port
            if number:
                return f"nc {shlex.quote(str(host))} {shlex.quote(str(number))}"
        return str(host)

    async def _instance_for(self, challenge_id: str) -> dict[str, Any] | None:
        data = await self._success("GET", "teams/me/instances", params={"challenge_id": challenge_id})
        if isinstance(data, dict) and isinstance(data.get("instances"), list):
            instances = data["instances"]
        else:
            instances = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        return next((item for item in instances if item.get("challenge_id") == challenge_id), None)

    async def _ensure_instance(self, challenge_id: str) -> dict[str, Any] | None:
        existing = await self._instance_for(challenge_id)
        if existing and existing.get("status") == "RUNNING":
            return existing
        if not self.manage_instances:
            return None
        if not existing or existing.get("status") not in {"REQUESTED", "SCHEDULING", "PROVISIONING", "RESTARTING", "RESETTING"}:
            body = {"challenge_id": challenge_id}
            response = await self._request("POST", "instances", json=body, headers={"Idempotency-Key": f"instance-create-{uuid.uuid4()}"})
            if response["code"] == "INSTANCE_REPLACEMENT_REQUIRED":
                active = await self._success("GET", "teams/me/instances")
                if not isinstance(active, dict) or not UUID.fullmatch(str(active.get("instance_id", ""))):
                    raise RuntimeError("Instance replacement required but current instance ID is unavailable")
                body["replace_instance_id"] = active["instance_id"]
                response = await self._request("POST", "instances", json=body, headers={"Idempotency-Key": f"instance-create-{uuid.uuid4()}"})
            if response["code"] != "SUCCESS":
                raise RuntimeError(f"Instance creation failed: {response['code']}: {response.get('message', '')}")
        for _ in range(36):
            await asyncio.sleep(5)
            instance = await self._instance_for(challenge_id)
            if instance and instance.get("status") == "RUNNING":
                return instance
            if instance and instance.get("status") in {"FAILED", "EXPIRED", "STOPPED", "CLEANED"}:
                raise RuntimeError(f"Instance for {challenge_id} ended in {instance['status']}")
        raise TimeoutError(f"Instance for {challenge_id} did not reach RUNNING in 180 seconds")

    async def _download_file(self, challenge_id: str, file: dict[str, Any], dist_dir: Path) -> None:
        file_id = str(file.get("file_id", ""))
        if not UUID.fullmatch(file_id):
            raise ValueError(f"Invalid file ID for challenge {challenge_id}")
        raw_name = str(file.get("file_name") or file_id)
        safe_name = Path(raw_name.replace("\\", "/")).name or "file"
        name = f"{file_id}-{safe_name}"
        dest = dist_dir / name
        if dest.exists():
            return
        if not self.access_token:
            await self._opened()
        path = f"challenges/{challenge_id}/files/{file_id}/download"
        client = await self._http()
        for attempt in range(2):
            token = self.access_token
            async with client.stream("GET", path, headers={"Authorization": f"Bearer {token}"}) as response:
                if response.status_code == 401 and attempt == 0:
                    async with self._auth_lock:
                        if self.access_token == token:
                            await self._refresh()
                    continue
                if response.status_code != 200:
                    raise RuntimeError(f"File download failed: HTTP {response.status_code} for {file_id}")
                size = 0
                tmp = dest.with_name(dest.name + ".part")
                try:
                    with tmp.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_FILE_BYTES:
                                raise RuntimeError(f"File {file_id} exceeds {MAX_FILE_BYTES} bytes")
                            output.write(chunk)
                    tmp.replace(dest)
                finally:
                    tmp.unlink(missing_ok=True)
                return

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        challenge_id = str(challenge["id"])
        if not UUID.fullmatch(challenge_id):
            raise ValueError("Invalid MSG CTF challenge ID")
        ch_dir = Path(output_dir) / challenge_id
        dist_dir = ch_dir / "distfiles"
        dist_dir.mkdir(parents=True, exist_ok=True)
        instance = challenge.get("instance")
        if challenge.get("requires_instance") is not False and not challenge.get("remote_run"):
            instance = await self._ensure_instance(challenge_id)
            # File bundles are tied to the release of the running instance.
            detail = await self._success("GET", f"challenges/{challenge_id}")
            if isinstance(detail, dict):
                challenge = {**challenge, **detail}
        for file in challenge.get("files") or []:
            await self._download_file(challenge_id, file, dist_dir)
        meta = {
            "name": challenge["name"],
            "category": challenge.get("category", ""),
            "description": html2md(challenge.get("description") or "").strip(),
            "value": challenge.get("value", 0),
            "connection_info": self._connection_info(instance),
            "tags": [],
            "solves": challenge.get("solves", 0),
        }
        (ch_dir / "metadata.yml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False))
        return str(ch_dir)

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        challenge = self._challenges.get(challenge_name)
        if challenge is None:
            await self._opened()
            challenge = self._challenges.get(challenge_name)
        if challenge is None:
            raise ValueError(f"Unknown opened challenge: {challenge_name}")
        result = await self._request("POST", f"challenges/{challenge['challenge_id']}/submit", json={"flag": flag.strip()})
        code = result["code"]
        message = result.get("message", "")
        if code == "SUCCESS":
            return SubmitResult("correct", message, f"CORRECT — {message}")
        if code == "ALREADY_SOLVED":
            return SubmitResult("already_solved", message, f"ALREADY SOLVED — {message}")
        if code == "INCORRECT_FLAG":
            return SubmitResult("incorrect", message, f"INCORRECT — {message}")
        if code == "TOO_MANY_ATTEMPTS":
            retry = (result.get("data") or {}).get("retry_after_seconds", 30)
            return SubmitResult("rate_limited", message, f"RATE LIMITED — retry after {retry}s. {message}")
        raise RuntimeError(f"Flag submission failed: {code}: {message}")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
