"""OpenAI-compatible / DeepSeek LLM client with timeout, retries, and cost tracking."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ops_agent.config import AppConfig

# Reference pricing in CNY per 1M tokens (input, output). Configurable per model.
MODEL_PRICING_CNY_PER_1M: dict[str, tuple[float, float]] = {
    "deepseek-chat": (2.0, 8.0),
    "deepseek-reasoner": (4.0, 16.0),
    "gpt-4o-mini": (1.1, 4.4),
}

_RETRYABLE_HTTP_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

SYSTEM_PROMPT = (
    "你是企业内部运营与知识助手。请严格遵循以下安全与事实边界：\n"
    "1. 仅根据下方【已授权上下文】回答用户问题，严禁编造未出现的数字、政策或记录。\n"
    "2. 回答应简洁清晰，并在结尾标明引用的文档 ID 或 ERP 表来源。\n"
    "3. 如果上下文不足以回答，请直接说明未检索到授权依据。"
)


class LLMClientError(RuntimeError):
    """Raised when an upstream LLM API request fails after retries."""


@dataclass(frozen=True)
class LLMUsage:
    """Latency, token count, and estimated CNY cost for one LLM generation call."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_cny: float
    latency_ms: float


@dataclass(frozen=True)
class LLMResponse:
    """Generated text paired with observability metrics."""

    content: str
    usage: LLMUsage


class LLMClient(Protocol):
    """Minimal protocol shared by Mock and OpenAI-compatible/DeepSeek clients."""

    def generate(self, *, question: str, authorized_context: str) -> LLMResponse:
        ...


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate query cost in CNY based on reference per-1M-token pricing."""
    in_price, out_price = MODEL_PRICING_CNY_PER_1M.get(model, (2.0, 8.0))
    cost = (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000.0
    return round(cost, 6)


class MockLLMClient:
    """Deterministic zero-cost client for offline tests and keyless local demos."""

    def __init__(self, model: str = "mock-grounded-v1") -> None:
        self._model = model

    def generate(self, *, question: str, authorized_context: str) -> LLMResponse:
        started = time.perf_counter()
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return LLMResponse(
            content=authorized_context,
            usage=LLMUsage(
                provider="mock",
                model=self._model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_cny=0.0,
                latency_ms=latency_ms,
            ),
        )


HttpSender = Callable[[str, dict[str, str], dict[str, Any], int], tuple[int, dict[str, Any]]]


def _default_http_sender(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status = int(resp.status)
            data = json.loads(resp.read().decode("utf-8"))
            return status, data
    except urllib.error.HTTPError as exc:
        raw_err = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(raw_err)
        except json.JSONDecodeError:
            err_json = {"error": raw_err[:300]}
        return int(exc.code), err_json


class OpenAICompatibleLLMClient:
    """Zero-dependency OpenAI-compatible client supporting DeepSeek, Qwen, and vLLM."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 30,
        max_retries: int = 2,
        http_sender: HttpSender | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or api_key == "your-api-key-here":
            raise ValueError("启用真实大模型前请在环境变量或 .env 中配置有效的 LLM_API_KEY。")
        self._provider = provider
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._http_sender = _default_http_sender if http_sender is None else http_sender
        self._sleep = sleep_fn

    def generate(self, *, question: str, authorized_context: str) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        user_prompt = (
            f"【已授权上下文】\n{authorized_context}\n\n"
            f"【用户问题】\n{question}\n\n"
            "请严格基于上述已授权上下文用中文作答，并保留引用标识。"
        )
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }

        started = time.perf_counter()
        last_error = ""

        for attempt in range(self._max_retries + 1):
            try:
                status, data = self._http_sender(
                    self._endpoint, headers, payload, self._timeout_seconds
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"网络或超时异常：{exc}"
                if attempt < self._max_retries:
                    self._sleep(0.5 * (2**attempt))
                    continue
                raise LLMClientError(last_error) from exc

            if status == 200:
                choices = data.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    raise LLMClientError("上游模型返回结构异常：缺少 choices 字段。")
                message = choices[0].get("message") or {}
                content = str(message.get("content", "")).strip()
                if not content:
                    raise LLMClientError("上游模型返回空回答。")

                usage_raw = data.get("usage") or {}
                prompt_tokens = int(usage_raw.get("prompt_tokens", 0))
                completion_tokens = int(usage_raw.get("completion_tokens", 0))
                total_tokens = int(
                    usage_raw.get("total_tokens", prompt_tokens + completion_tokens)
                )
                latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
                return LLMResponse(
                    content=content,
                    usage=LLMUsage(
                        provider=self._provider,
                        model=self._model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        estimated_cost_cny=estimate_cost_cny(
                            self._model, prompt_tokens, completion_tokens
                        ),
                        latency_ms=latency_ms,
                    ),
                )

            last_error = f"HTTP {status}: {str(data)[:240]}"
            if status in _RETRYABLE_HTTP_CODES and attempt < self._max_retries:
                self._sleep(0.5 * (2**attempt))
                continue
            raise LLMClientError(last_error)

        raise LLMClientError(last_error or "未知模型调用错误")


def build_llm_client(config: AppConfig) -> LLMClient:
    """Create the configured LLM client (defaults to offline MockLLMClient)."""
    if config.llm_provider == "mock":
        return MockLLMClient()
    return OpenAICompatibleLLMClient(
        provider=config.llm_provider,
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        timeout_seconds=config.llm_timeout_seconds,
        max_retries=config.llm_max_retries,
    )
