from pathlib import Path
from typing import Any

import pytest

from ops_agent.config import load_config_from_env
from ops_agent.erp_tools import ReadOnlyErpService
from ops_agent.knowledge import EnterpriseKnowledgeService, load_sample_docs
from ops_agent.llm_client import (
    LLMClientError,
    MockLLMClient,
    OpenAICompatibleLLMClient,
    estimate_cost_cny,
)
from ops_agent.pipeline import EnterpriseOpsPipeline

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_deepseek_client_requires_real_api_key() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleLLMClient(
            provider="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key="your-api-key-here",
            model="deepseek-chat",
        )


def test_deepseek_client_success_and_cost_calculation() -> None:
    captured: dict[str, Any] = {}

    def fake_sender(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
    ) -> tuple[int, dict[str, Any]]:
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        captured["timeout"] = timeout
        return 200, {
            "choices": [
                {"message": {"content": "差旅报销超过 5,000 元需财务经理复核 [DOC-FIN-001]"}}
            ],
            "usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
        }

    client = OpenAICompatibleLLMClient(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key="test-key-12345",
        model="deepseek-chat",
        timeout_seconds=15,
        http_sender=fake_sender,
    )
    res = client.generate(question="报销审批门槛？", authorized_context="【DOC-FIN-001】超过 5,000")
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key-12345"
    assert captured["payload"]["model"] == "deepseek-chat"
    assert res.usage.total_tokens == 600
    assert res.usage.estimated_cost_cny == estimate_cost_cny("deepseek-chat", 500, 100)


def test_deepseek_client_retries_on_429_then_succeeds() -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    def flaky_sender(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
    ) -> tuple[int, dict[str, Any]]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return 429, {"error": "rate limit"}
        return 200, {
            "choices": [{"message": {"content": "重试后成功回答"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

    client = OpenAICompatibleLLMClient(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key="test-key-12345",
        model="deepseek-chat",
        max_retries=2,
        http_sender=flaky_sender,
        sleep_fn=sleeps.append,
    )
    res = client.generate(question="测试重试", authorized_context="测试上下文")
    assert attempts["count"] == 2
    assert sleeps == [0.5]
    assert res.content == "重试后成功回答"


def test_deepseek_client_raises_after_exhausted_retries() -> None:
    def always_503(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
    ) -> tuple[int, dict[str, Any]]:
        return 503, {"error": "service unavailable"}

    client = OpenAICompatibleLLMClient(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key="test-key-12345",
        model="deepseek-chat",
        max_retries=1,
        http_sender=always_503,
        sleep_fn=lambda _: None,
    )
    with pytest.raises(LLMClientError):
        client.generate(question="测试失败", authorized_context="测试上下文")


def test_pipeline_never_calls_llm_when_rbac_refuses_and_degrades_on_llm_error() -> None:
    calls = {"count": 0}

    class FailingClient:
        def generate(self, *, question: str, authorized_context: str) -> Any:
            calls["count"] += 1
            raise LLMClientError("simulated timeout")

    pipeline = EnterpriseOpsPipeline(
        knowledge_service=EnterpriseKnowledgeService(
            docs=load_sample_docs(PROJECT_ROOT / "data" / "sample_docs")
        ),
        erp_service=ReadOnlyErpService(),
        llm_client=FailingClient(),
        fallback_on_llm_error=True,
    )

    # Unauthorized query MUST be refused BEFORE calling LLM (0 LLM calls).
    refused = pipeline.ask(role="employee", question="本月有哪些逾期未付的应付账款？")
    assert refused.refused is True
    assert calls["count"] == 0

    # Authorized query degrades gracefully if upstream LLM fails.
    degraded = pipeline.ask(role="finance", question="本月有哪些逾期未付的应付账款？")
    assert degraded.refused is False
    assert degraded.degraded is True
    assert calls["count"] == 1
    assert "67,700.00" in degraded.answer


def test_load_config_reads_dotenv_and_validates_provider(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=deepseek\nLLM_MODEL=deepseek-chat\n", encoding="utf-8")
    cfg = load_config_from_env({}, dotenv_path=env_file)
    assert cfg.llm_provider == "deepseek"
    assert cfg.llm_base_url == "https://api.deepseek.com/v1"
    assert cfg.llm_model == "deepseek-chat"

    with pytest.raises(ValueError):
        load_config_from_env({"LLM_PROVIDER": "unknown_vendor"})


def test_fixed_30_task_eval_dataset_passes_mock_baseline() -> None:
    import sys

    evals_dir = str(PROJECT_ROOT / "evals")
    if evals_dir not in sys.path:
        sys.path.insert(0, evals_dir)

    from run_eval import load_eval_cases, run_evaluation

    cases = load_eval_cases(PROJECT_ROOT / "evals" / "dataset.jsonl")
    assert len(cases) == 30
    report = run_evaluation(
        cases=cases,
        llm_client=MockLLMClient(),
        provider_label="mock",
        model_label="mock-grounded-v1",
    )
    summary = report["summary"]
    assert summary["overall_pass_rate_pct"] == 100.0
    assert summary["rbac_block_rate_pct"] == 100.0
    assert summary["injection_block_rate_pct"] == 100.0
    assert summary["out_of_scope_refusal_rate_pct"] == 100.0
    assert summary["llm_calls"] == 15
