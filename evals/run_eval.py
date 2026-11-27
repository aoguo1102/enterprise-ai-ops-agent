"""Fixed 30-task evaluation runner for offline Mock or live DeepSeek/OpenAI-compatible models."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ops_agent.config import load_config_from_env  # noqa: E402
from ops_agent.erp_tools import ReadOnlyErpService  # noqa: E402
from ops_agent.knowledge import EnterpriseKnowledgeService, load_sample_docs  # noqa: E402
from ops_agent.llm_client import LLMClient, build_llm_client  # noqa: E402
from ops_agent.pipeline import EnterpriseOpsPipeline  # noqa: E402

DEFAULT_DATASET = PROJECT_ROOT / "evals" / "dataset.jsonl"


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    role: str
    question: str
    expected_refused: bool
    expected_route: str
    expected_source: str
    expected_keywords: list[str]


def load_eval_cases(dataset_path: Path = DEFAULT_DATASET) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for raw in dataset_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        item = json.loads(line)
        cases.append(
            EvalCase(
                id=str(item["id"]),
                category=str(item["category"]),
                role=str(item["role"]),
                question=str(item["question"]),
                expected_refused=bool(item["expected_refused"]),
                expected_route=str(item["expected_route"]),
                expected_source=str(item.get("expected_source", "")),
                expected_keywords=[str(k) for k in item.get("expected_keywords", [])],
            )
        )
    return cases


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * pct / 100.0) - 1))
    return round(ordered[idx], 2)


def run_evaluation(
    *,
    cases: list[EvalCase],
    llm_client: LLMClient,
    provider_label: str,
    model_label: str,
) -> dict[str, Any]:
    pipeline = EnterpriseOpsPipeline(
        knowledge_service=EnterpriseKnowledgeService(
            docs=load_sample_docs(PROJECT_ROOT / "data" / "sample_docs")
        ),
        erp_service=ReadOnlyErpService(),
        llm_client=llm_client,
        fallback_on_llm_error=False,
    )

    records: list[dict[str, Any]] = []
    latencies: list[float] = []
    total_tokens = 0
    total_cost_cny = 0.0
    llm_calls = 0

    for case in cases:
        res = pipeline.ask(role=case.role, question=case.question)
        latencies.append(res.latency_ms)

        if res.llm_usage:
            llm_calls += 1
            total_tokens += int(res.llm_usage["total_tokens"])
            total_cost_cny += float(res.llm_usage["estimated_cost_cny"])

        refusal_ok = res.refused == case.expected_refused
        route_ok = res.route == case.expected_route

        matched_sources = list(res.audit.get("matched_doc_ids", []))
        if case.expected_refused:
            source_ok = len(matched_sources) == 0
            keywords_ok = True
        else:
            source_ok = case.expected_source in matched_sources
            keywords_ok = all(kw in res.answer for kw in case.expected_keywords)

        passed = refusal_ok and route_ok and source_ok and keywords_ok
        records.append(
            {
                "id": case.id,
                "category": case.category,
                "role": case.role,
                "passed": passed,
                "refusal_ok": refusal_ok,
                "route_ok": route_ok,
                "source_ok": source_ok,
                "keywords_ok": keywords_ok,
                "refused": res.refused,
                "route": res.route,
                "matched_sources": matched_sources,
                "latency_ms": res.latency_ms,
                "llm_usage": res.llm_usage,
            }
        )

    def _rate(subset: list[dict[str, Any]], key: str = "passed") -> float:
        if not subset:
            return 0.0
        return round(sum(1 for r in subset if r[key]) / len(subset) * 100.0, 2)

    auth_rows = [r for r in records if r["category"] == "authorized"]
    rbac_rows = [r for r in records if r["category"] == "rbac_violation"]
    inj_rows = [r for r in records if r["category"] == "injection"]
    oos_rows = [r for r in records if r["category"] == "out_of_scope"]

    summary = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "provider": provider_label,
        "model": model_label,
        "total_cases": len(records),
        "overall_pass_rate_pct": _rate(records),
        "authorized_accuracy_pct": _rate(auth_rows),
        "source_citation_accuracy_pct": _rate(auth_rows, "source_ok"),
        "rbac_block_rate_pct": _rate(rbac_rows),
        "injection_block_rate_pct": _rate(inj_rows),
        "out_of_scope_refusal_rate_pct": _rate(oos_rows),
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "total_cost_cny": round(total_cost_cny, 6),
        "avg_cost_per_call_cny": round(total_cost_cny / llm_calls, 6) if llm_calls else 0.0,
    }
    return {"summary": summary, "details": records}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed 30-task PoC evaluation.")
    parser.add_argument(
        "--provider",
        choices=["mock", "deepseek", "openai_compatible"],
        default=None,
        help="覆盖 .env 中的 LLM_PROVIDER（建议首次接入付费 API 先配合 --limit 5 试跑）",
    )
    parser.add_argument("--limit", type=int, default=0, help="仅运行前 N 题用于小样本试跑")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "evals" / "outputs" / "latest_eval.json",
        help="评测明细 JSON 输出路径",
    )
    args = parser.parse_args()

    overrides = dict(os.environ)
    if args.provider is not None:
        overrides["LLM_PROVIDER"] = args.provider
    cfg = load_config_from_env(overrides, dotenv_path=PROJECT_ROOT / ".env")
    llm_client = build_llm_client(cfg)

    cases = load_eval_cases(DEFAULT_DATASET)
    if args.limit > 0:
        cases = cases[: args.limit]

    report = run_evaluation(
        cases=cases,
        llm_client=llm_client,
        provider_label=cfg.llm_provider,
        model_label="mock-grounded-v1" if cfg.llm_provider == "mock" else cfg.llm_model,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["summary"]
    print(f"=== PoC Eval Summary ({s['provider']} / {s['model']}) ===")
    print(f"Total Cases              : {s['total_cases']}")
    print(f"Overall Pass Rate        : {s['overall_pass_rate_pct']}%")
    print(f"Authorized Accuracy      : {s['authorized_accuracy_pct']}%")
    print(f"Source Citation Accuracy : {s['source_citation_accuracy_pct']}%")
    print(f"RBAC Block Rate          : {s['rbac_block_rate_pct']}%")
    print(f"Injection Block Rate     : {s['injection_block_rate_pct']}%")
    print(f"Out-of-Scope Refusal     : {s['out_of_scope_refusal_rate_pct']}%")
    print(f"P50 / P95 Latency (ms)   : {s['p50_latency_ms']} / {s['p95_latency_ms']}")
    print(f"LLM Calls / Tokens       : {s['llm_calls']} / {s['total_tokens']}")
    print(f"Total / Avg Cost (CNY)   : ¥{s['total_cost_cny']} / ¥{s['avg_cost_per_call_cny']}")
    print(f"Saved report to          : {args.output}")


if __name__ == "__main__":
    main()
