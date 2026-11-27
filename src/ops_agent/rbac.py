"""Role-based access control (RBAC), prompt injection guardrails, and audit records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

ROLE_ALLOWED_DEPARTMENTS: dict[str, frozenset[str]] = {
    "employee": frozenset({"public"}),
    "finance": frozenset({"public", "finance"}),
    "procurement": frozenset({"public", "procurement"}),
    "it_admin": frozenset({"public", "finance", "procurement"}),
}

ROLE_LABELS: dict[str, str] = {
    "employee": "普通员工（仅公共区）",
    "finance": "财务组（公共 + 财务）",
    "procurement": "采购组（公共 + 采购）",
    "it_admin": "IT 管理员（全部演示文档）",
}

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"忽略.{0,8}(规则|指令|权限|限制)", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?(previous|system)\s+instructions", re.IGNORECASE),
    re.compile(r"(输出|打印|泄露).{0,8}(所有|全部|系统提示词|prompt)", re.IGNORECASE),
    re.compile(r"(绕过|跳过).{0,6}权限", re.IGNORECASE),
)


def get_allowed_departments(role: str) -> frozenset[str]:
    """Return allowed document departments for a given role."""
    normalized = role.strip().lower()
    if normalized not in ROLE_ALLOWED_DEPARTMENTS:
        raise ValueError(f"不支持的角色：{role!r}，可选角色：{sorted(ROLE_ALLOWED_DEPARTMENTS)}")
    return ROLE_ALLOWED_DEPARTMENTS[normalized]


def detect_prompt_injection(question: str) -> str | None:
    """Return matched guardrail reason if the query looks like a prompt injection attempt."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(question):
            return "检测到疑似越权或提示词注入指令，已按安全策略拦截。"
    return None


@dataclass(frozen=True)
class AuditRecord:
    """Structured audit log entry for every query."""

    timestamp_utc: str
    role: str
    question_preview: str
    allowed_departments: list[str]
    matched_doc_ids: list[str]
    refused: bool
    refusal_reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        role: str,
        question: str,
        allowed_departments: frozenset[str],
        matched_doc_ids: list[str],
        refused: bool,
        refusal_reason: str | None = None,
    ) -> AuditRecord:
        preview = question.strip().replace("\n", " ")[:120]
        return cls(
            timestamp_utc=datetime.now(UTC).isoformat(),
            role=role,
            question_preview=preview,
            allowed_departments=sorted(allowed_departments),
            matched_doc_ids=matched_doc_ids,
            refused=refused,
            refusal_reason=refusal_reason,
        )
