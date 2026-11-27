"""Unified orchestration pipeline combining RBAC RAG, read-only ERP tools, and LLM synthesis."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from ops_agent.erp_tools import ReadOnlyErpService, detect_erp_tool_intent
from ops_agent.knowledge import EnterpriseKnowledgeService
from ops_agent.llm_client import LLMClient, LLMClientError, MockLLMClient


@dataclass(frozen=True)
class PipelineResult:
    """End-to-end response returned by API and evaluation runner."""

    route: str
    tool_used: str | None
    answer: str
    table_rows: list[dict[str, Any]]
    summary: dict[str, Any]
    citations: list[dict[str, str]]
    refused: bool
    degraded: bool
    fallback_reason: str | None
    llm_usage: dict[str, Any] | None
    latency_ms: float
    audit: dict[str, Any]


class EnterpriseOpsPipeline:
    """Security-first pipeline: RBAC and guardrails always execute BEFORE calling any LLM."""

    def __init__(
        self,
        *,
        knowledge_service: EnterpriseKnowledgeService,
        erp_service: ReadOnlyErpService,
        llm_client: LLMClient | None = None,
        fallback_on_llm_error: bool = True,
    ) -> None:
        self._knowledge = knowledge_service
        self._erp = erp_service
        self._llm = MockLLMClient() if llm_client is None else llm_client
        self._fallback_on_llm_error = fallback_on_llm_error

    def ask(self, *, role: str, question: str) -> PipelineResult:
        started = time.perf_counter()
        erp_tool = detect_erp_tool_intent(question)

        if erp_tool is not None:
            erp_res = self._erp.execute_tool(role=role, tool_name=erp_tool, question=question)
            if erp_res.refused:
                return PipelineResult(
                    route="erp_tool",
                    tool_used=erp_res.tool_name,
                    answer=erp_res.answer,
                    table_rows=[],
                    summary={},
                    citations=[],
                    refused=True,
                    degraded=False,
                    fallback_reason=None,
                    llm_usage=None,
                    latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
                    audit=asdict(erp_res.audit),
                )

            answer, usage_dict, degraded, fallback_reason = self._synthesize(
                question=question,
                grounded_context=erp_res.answer,
            )
            return PipelineResult(
                route="erp_tool",
                tool_used=erp_res.tool_name,
                answer=answer,
                table_rows=erp_res.rows,
                summary=erp_res.summary,
                citations=[],
                refused=False,
                degraded=degraded,
                fallback_reason=fallback_reason,
                llm_usage=usage_dict,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
                audit=asdict(erp_res.audit),
            )

        rag_res = self._knowledge.query(role=role, question=question)
        if rag_res.refused:
            return PipelineResult(
                route="knowledge_base",
                tool_used=None,
                answer=rag_res.answer,
                table_rows=[],
                summary={},
                citations=[],
                refused=True,
                degraded=False,
                fallback_reason=None,
                llm_usage=None,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
                audit=asdict(rag_res.audit),
            )

        answer, usage_dict, degraded, fallback_reason = self._synthesize(
            question=question,
            grounded_context=rag_res.answer,
        )
        return PipelineResult(
            route="knowledge_base",
            tool_used=None,
            answer=answer,
            table_rows=[],
            summary={},
            citations=rag_res.citations,
            refused=False,
            degraded=degraded,
            fallback_reason=fallback_reason,
            llm_usage=usage_dict,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            audit=asdict(rag_res.audit),
        )

    def _synthesize(
        self,
        *,
        question: str,
        grounded_context: str,
    ) -> tuple[str, dict[str, Any] | None, bool, str | None]:
        try:
            llm_res = self._llm.generate(question=question, authorized_context=grounded_context)
            return llm_res.content, asdict(llm_res.usage), False, None
        except LLMClientError as exc:
            if not self._fallback_on_llm_error:
                raise
            fallback_answer = (
                f"（注：上游模型暂不可用，已自动降级返回授权原始依据）\n\n{grounded_context}"
            )
            return fallback_answer, None, True, str(exc)
