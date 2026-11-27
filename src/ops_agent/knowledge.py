"""RBAC-filtered local knowledge loader and offline retriever for PoC demos."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ops_agent.rbac import AuditRecord, detect_prompt_injection, get_allowed_departments


@dataclass(frozen=True)
class KnowledgeDoc:
    """Single knowledge base document with department permission metadata."""

    doc_id: str
    title: str
    department: str
    content: str
    source_file: str


@dataclass(frozen=True)
class QueryResponse:
    """Structured response returned by the knowledge agent."""

    answer: str
    citations: list[dict[str, str]]
    refused: bool
    audit: AuditRecord


def _parse_frontmatter(raw_text: str, filename: str) -> KnowledgeDoc:
    lines = raw_text.splitlines()
    meta: dict[str, str] = {}
    body_start = 0

    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                body_start = idx + 1
                break
            if ":" in lines[idx]:
                key, val = lines[idx].split(":", 1)
                meta[key.strip()] = val.strip()

    body = "\n".join(lines[body_start:]).strip()
    return KnowledgeDoc(
        doc_id=meta.get("doc_id", filename),
        title=meta.get("title", filename),
        department=meta.get("department", "public").lower(),
        content=body,
        source_file=filename,
    )


def load_sample_docs(docs_dir: Path) -> list[KnowledgeDoc]:
    """Load markdown documents from a directory."""
    if not docs_dir.exists():
        return []
    docs: list[KnowledgeDoc] = []
    for path in sorted(docs_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        docs.append(_parse_frontmatter(raw, path.name))
    return docs


def _tokenize(text: str) -> set[str]:
    """Extract simple Chinese bigrams and alphanumeric words for offline matching."""
    lowered = text.lower()
    ascii_tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]+", lowered)
    cjk_tokens: set[str] = set()
    for chunk in cjk_chunks:
        if len(chunk) == 1:
            cjk_tokens.add(chunk)
        else:
            for i in range(len(chunk) - 1):
                cjk_tokens.add(chunk[i : i + 2])
    return ascii_tokens | cjk_tokens


class EnterpriseKnowledgeService:
    """Offline-capable RBAC knowledge service."""

    def __init__(self, docs: list[KnowledgeDoc], max_query_length: int = 1000) -> None:
        self._docs = docs
        self._max_query_length = max_query_length

    def query(self, *, role: str, question: str) -> QueryResponse:
        allowed_departments = get_allowed_departments(role)
        cleaned_question = question.strip()

        if not cleaned_question:
            reason = "问题不能为空。"
            audit = AuditRecord.create(
                role=role,
                question=question,
                allowed_departments=allowed_departments,
                matched_doc_ids=[],
                refused=True,
                refusal_reason=reason,
            )
            return QueryResponse(answer=reason, citations=[], refused=True, audit=audit)

        if len(cleaned_question) > self._max_query_length:
            reason = f"问题长度超出限制（最大 {self._max_query_length} 字符）。"
            audit = AuditRecord.create(
                role=role,
                question=cleaned_question,
                allowed_departments=allowed_departments,
                matched_doc_ids=[],
                refused=True,
                refusal_reason=reason,
            )
            return QueryResponse(answer=reason, citations=[], refused=True, audit=audit)

        injection_reason = detect_prompt_injection(cleaned_question)
        if injection_reason:
            audit = AuditRecord.create(
                role=role,
                question=cleaned_question,
                allowed_departments=allowed_departments,
                matched_doc_ids=[],
                refused=True,
                refusal_reason=injection_reason,
            )
            return QueryResponse(answer=injection_reason, citations=[], refused=True, audit=audit)

        # Security-first: filter by RBAC BEFORE scoring or passing anything to an LLM.
        authorized_docs = [doc for doc in self._docs if doc.department in allowed_departments]
        query_tokens = _tokenize(cleaned_question)

        scored: list[tuple[int, KnowledgeDoc]] = []
        for doc in authorized_docs:
            doc_tokens = _tokenize(f"{doc.title}\n{doc.content}")
            overlap = len(query_tokens & doc_tokens)
            if overlap >= 2:
                scored.append((overlap, doc))

        scored.sort(key=lambda item: item[0], reverse=True)

        if not scored:
            reason = (
                "未在当前角色有权限访问的知识库中找到相关依据（可能是知识库未覆盖，"
                "或涉及更高权限部门的受控资料）。如需访问受限目录，请按流程提交权限审批。"
            )
            audit = AuditRecord.create(
                role=role,
                question=cleaned_question,
                allowed_departments=allowed_departments,
                matched_doc_ids=[],
                refused=True,
                refusal_reason="无授权命中文档",
            )
            return QueryResponse(answer=reason, citations=[], refused=True, audit=audit)

        top_docs = [doc for _, doc in scored[:2]]
        citations = [
            {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "department": doc.department,
                "source_file": doc.source_file,
            }
            for doc in top_docs
        ]

        excerpts = [f"【{doc.doc_id}｜{doc.title}】\n{doc.content}" for doc in top_docs]
        answer = (
            "已根据您当前角色有权限访问的企业文档检索到以下依据：\n\n"
            + "\n\n---\n\n".join(excerpts)
        )
        audit = AuditRecord.create(
            role=role,
            question=cleaned_question,
            allowed_departments=allowed_departments,
            matched_doc_ids=[doc.doc_id for doc in top_docs],
            refused=False,
        )
        return QueryResponse(answer=answer, citations=citations, refused=False, audit=audit)
