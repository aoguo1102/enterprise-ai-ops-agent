"""Configuration parsing and validation helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


def parse_port(value: str) -> int:
    """Convert a port string to an integer in the valid TCP port range (1-65535)."""
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError(f"端口必须是整数，收到：{value!r}") from exc

    if not 1 <= port <= 65535:
        raise ValueError(f"端口必须在 1～65535 之间，收到：{port}")

    return port


def parse_timeout(value: str) -> int:
    """Convert a timeout string to an integer from 1 to 300 seconds."""
    try:
        timeout = int(value)
    except ValueError as exc:
        raise ValueError(f"超时时间必须是整数，收到：{value!r}") from exc

    if not 1 <= timeout <= 300:
        raise ValueError(f"超时时间必须在 1～300 秒之间，收到：{timeout}")

    return timeout


def parse_max_retries(value: str) -> int:
    """Convert a max retries string to an integer from 0 to 5."""
    try:
        retries = int(value)
    except ValueError as exc:
        raise ValueError(f"重试次数必须是整数，收到：{value!r}") from exc

    if not 0 <= retries <= 5:
        raise ValueError(f"重试次数必须在 0～5 之间，收到：{retries}")

    return retries


@dataclass(frozen=True)
class AppConfig:
    """Validated runtime configuration."""

    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    llm_provider: str = "mock"
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2
    max_query_length: int = 1000


def load_config_from_env(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Load and validate application settings from environment variables."""
    env = os.environ if environ is None else environ
    max_query_length_raw = env.get("MAX_QUERY_LENGTH", "1000")
    try:
        max_query_length = int(max_query_length_raw)
    except ValueError as exc:
        raise ValueError(f"MAX_QUERY_LENGTH 必须是整数，收到：{max_query_length_raw!r}") from exc
    if not 10 <= max_query_length <= 5000:
        raise ValueError(f"MAX_QUERY_LENGTH 必须在 10～5000 之间，收到：{max_query_length}")

    return AppConfig(
        app_env=env.get("APP_ENV", "dev").strip() or "dev",
        app_host=env.get("APP_HOST", "0.0.0.0").strip() or "0.0.0.0",
        app_port=parse_port(env.get("APP_PORT", "8000")),
        llm_provider=env.get("LLM_PROVIDER", "mock").strip().lower() or "mock",
        llm_timeout_seconds=parse_timeout(env.get("LLM_TIMEOUT_SECONDS", "30")),
        llm_max_retries=parse_max_retries(env.get("LLM_MAX_RETRIES", "2")),
        max_query_length=max_query_length,
    )
