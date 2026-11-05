"""Pydantic Settings — credentials from .env file + environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    platform: str = "ctfd"

    # CTFd
    ctfd_url: str = "http://localhost:8000"
    ctfd_user: str = "admin"
    ctfd_pass: str = "admin"
    ctfd_token: str = ""

    # MSG CTF (participant account; no administrator API)
    msgctf_url: str = ""
    msgctf_login_id: str = ""
    msgctf_password: str = ""
    msgctf_access_token: str = ""
    msgctf_refresh_token: str = ""
    msgctf_manage_instances: bool = True
    msgctf_swarm_timeslice_seconds: int = 900

    # Optional API-backed providers. Codex defaults use the local CLI login instead.
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # Provider-specific settings, only needed for explicit Bedrock/Azure/Zen specs.
    aws_region: str = "us-east-1"
    aws_bearer_token: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    opencode_zen_api_key: str = ""

    # Infra
    sandbox_image: str = "ctf-sandbox"
    max_concurrent_challenges: int = 10
    max_attempts_per_challenge: int = 3
    container_memory_limit: str = "16g"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
