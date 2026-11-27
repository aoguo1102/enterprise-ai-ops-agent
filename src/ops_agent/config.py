"""Configuration parsing, .env loading, and boundary validation helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"mock", "deepseek", "openai_compatible"})


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


def load_dotenv_file(dotenv_path: Path) -> dict[str, str]:
    """Read key=value pairs from a local .env file without mutating os.environ."""
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        cleaned_key = key.strip()
        cleaned_val = val.strip().strip('"').strip("'")
        if cleaned_key:
            values[cleaned_key] = cleaned_val
    return values


@dataclass(frozen=True)
class AppConfig:
    """Validated runtime configuration."""

    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    llm_provider: str = "mock"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2
    max_query_length: int = 1000


def load_config_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path | None = None,
) -> AppConfig:
    """Load and validate application settings from .env (optional) and environment variables."""
    merged: dict[str, str] = {}
    if dotenv_path is not None:
        merged.update(load_dotenv_file(dotenv_path))
    source_env = os.environ if environ is None else environ
    merged.update(source_env)

    max_query_length_raw = merged.get("MAX_QUERY_LENGTH", "1000")
    try:
        max_query_length = int(max_query_length_raw)
    except ValueError as exc:
        raise ValueError(f"MAX_QUERY_LENGTH 必须是整数，收到：{max_query_length_raw!r}") from exc
    if not 10 <= max_query_length <= 5000:
        raise ValueError(f"MAX_QUERY_LENGTH 必须在 10～5000 之间，收到：{max_query_length}")

    provider = merged.get("LLM_PROVIDER", "mock").strip().lower() or "mock"
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"不支持的 LLM_PROVIDER：{provider!r}，可选：{sorted(SUPPORTED_PROVIDERS)}"
        )

    default_base_url = (
        "https://api.deepseek.com/v1"
        if provider in {"mock", "deepseek"}
        else "https://api.openai.com/v1"
    )
    default_model = "deepseek-chat" if provider in {"mock", "deepseek"} else "gpt-4o-mini"

    return AppConfig(
        app_env=merged.get("APP_ENV", "dev").strip() or "dev",
        app_host=merged.get("APP_HOST", "0.0.0.0").strip() or "0.0.0.0",
        app_port=parse_port(merged.get("APP_PORT", "8000")),
        llm_provider=provider,
        llm_base_url=(merged.get("LLM_BASE_URL", default_base_url).strip() or default_base_url),
        llm_api_key=merged.get("LLM_API_KEY", "").strip(),
        llm_model=merged.get("LLM_MODEL", default_model).strip() or default_model,
        llm_timeout_seconds=parse_timeout(merged.get("LLM_TIMEOUT_SECONDS", "30")),
        llm_max_retries=parse_max_retries(merged.get("LLM_MAX_RETRIES", "2")),
        max_query_length=max_query_length,
    )
