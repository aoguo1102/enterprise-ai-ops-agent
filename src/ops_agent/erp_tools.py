"""Whitelisted read-only ERP report query tools backed by synthetic SQLite data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ops_agent.rbac import AuditRecord, detect_prompt_injection, get_allowed_departments


@dataclass(frozen=True)
class ErpToolSpec:
    """Metadata describing a whitelisted read-only ERP query tool."""

    tool_name: str
    title: str
    required_department: str
    description: str


@dataclass(frozen=True)
class ErpQueryResult:
    """Structured response from executing a whitelisted read-only ERP tool."""

    tool_name: str
    answer: str
    rows: list[dict[str, Any]]
    summary: dict[str, Any]
    refused: bool
    audit: AuditRecord


ERP_TOOL_SPECS: dict[str, ErpToolSpec] = {
    "query_inventory_alerts": ErpToolSpec(
        tool_name="query_inventory_alerts",
        title="库存与安全库存预警报表",
        required_department="public",
        description="查询低于安全库存的物料清单与仓库分布（所有登录角色只读可见）。",
    ),
    "query_ap_overdue_summary": ErpToolSpec(
        tool_name="query_ap_overdue_summary",
        title="应付账款（AP）逾期与待付报表",
        required_department="finance",
        description="查询供应商应付账款状态与逾期金额汇总（仅财务组与 IT 管理员可见）。",
    ),
    "query_purchase_orders": ErpToolSpec(
        tool_name="query_purchase_orders",
        title="采购订单（PO）状态与金额汇总",
        required_department="procurement",
        description="查询采购订单执行进度与待比价订单（仅采购组与 IT 管理员可见）。",
    ),
}

_SEED_SQL = """
CREATE TABLE erp_inventory_summary (
    sku_code TEXT PRIMARY KEY,
    item_name TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    qty_on_hand INTEGER NOT NULL,
    safety_stock INTEGER NOT NULL
);

CREATE TABLE erp_ap_invoices (
    invoice_no TEXT PRIMARY KEY,
    vendor_name TEXT NOT NULL,
    amount_cny REAL NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE erp_purchase_orders (
    po_no TEXT PRIMARY KEY,
    vendor_name TEXT NOT NULL,
    item_category TEXT NOT NULL,
    amount_cny REAL NOT NULL,
    order_date TEXT NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO erp_inventory_summary VALUES
    ('SKU-M001', '不锈钢精密紧固件 M6', '上海一号仓', 180, 500),
    ('SKU-M002', '工业防静电包装箱', '上海一号仓', 620, 400),
    ('SKU-M003', '耐高温密封垫片 20mm', '苏州二号仓', 95, 300),
    ('SKU-M004', '标准条码碳带 110mm', '苏州二号仓', 45, 100);

INSERT INTO erp_ap_invoices VALUES
    ('AP-2026-001', '华东精密配件厂', 48500.00, '2026-11-10', 'OVERDUE'),
    ('AP-2026-002', '长三角包装材料有限公司', 19200.00, '2026-11-18', 'OVERDUE'),
    ('AP-2026-003', '江南工业物流服务商', 12800.00, '2026-12-05', 'PENDING'),
    ('AP-2026-004', '新宇自动化耗材公司', 31000.00, '2026-11-01', 'PAID');

INSERT INTO erp_purchase_orders VALUES
    ('PO-2026-101', '华东精密配件厂', '精密五金件', 62000.00, '2026-11-12', 'IN_TRANSIT'),
    ('PO-2026-102', '长三角包装材料有限公司', '包装耗材', 24500.00, '2026-11-20', 'APPROVED'),
    ('PO-2026-103', '苏南特种密封件厂', '密封组件', 41800.00, '2026-11-25', 'PENDING_QUOTE');
"""


def detect_erp_tool_intent(question: str) -> str | None:
    """Match a natural-language question to a whitelisted ERP tool name, if applicable."""
    q = question.strip().lower()
    if any(k in q for k in ("库存", "安全库存", "缺货", "物料", "仓库", "sku")):
        return "query_inventory_alerts"
    if any(k in q for k in ("应付", "逾期", "账款", "未付", "发票金额", "ap")):
        return "query_ap_overdue_summary"
    if any(k in q for k in ("采购订单", "订单总额", "在途订单", "待比价", "po")):
        return "query_purchase_orders"
    return None


class ReadOnlyErpService:
    """Executes strictly whitelisted, parameterized read-only SQLite ERP reports."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SEED_SQL)
        # Enforce read-only mode after synthetic seed data is populated.
        self._conn.execute("PRAGMA query_only = ON;")

    def list_tools(self) -> list[dict[str, str]]:
        return [
            {
                "tool_name": spec.tool_name,
                "title": spec.title,
                "required_department": spec.required_department,
                "description": spec.description,
            }
            for spec in ERP_TOOL_SPECS.values()
        ]

    def execute_tool(
        self,
        *,
        role: str,
        tool_name: str,
        question: str = "",
        only_alerts: bool = True,
    ) -> ErpQueryResult:
        allowed_departments = get_allowed_departments(role)
        audit_question = question.strip() or f"tool:{tool_name}"

        injection_reason = detect_prompt_injection(audit_question)
        if injection_reason:
            audit = AuditRecord.create(
                role=role,
                question=audit_question,
                allowed_departments=allowed_departments,
                matched_doc_ids=[],
                refused=True,
                refusal_reason=injection_reason,
            )
            return ErpQueryResult(
                tool_name=tool_name,
                answer=injection_reason,
                rows=[],
                summary={},
                refused=True,
                audit=audit,
            )

        if tool_name not in ERP_TOOL_SPECS:
            raise ValueError(f"工具不在白名单内：{tool_name!r}")

        spec = ERP_TOOL_SPECS[tool_name]
        if spec.required_department not in allowed_departments:
            reason = (
                f"权限不足：当前角色（{role}）无权调用【{spec.title}】"
                f"（需要 {spec.required_department} 部门权限）。已拦截并记录审计日志。"
            )
            audit = AuditRecord.create(
                role=role,
                question=audit_question,
                allowed_departments=allowed_departments,
                matched_doc_ids=[],
                refused=True,
                refusal_reason=f"越权调用 ERP 工具:{tool_name}",
            )
            return ErpQueryResult(
                tool_name=tool_name,
                answer=reason,
                rows=[],
                summary={},
                refused=True,
                audit=audit,
            )

        if tool_name == "query_inventory_alerts":
            return self._run_inventory_query(role, audit_question, allowed_departments, only_alerts)
        if tool_name == "query_ap_overdue_summary":
            return self._run_ap_query(role, audit_question, allowed_departments)
        return self._run_po_query(role, audit_question, allowed_departments)

    def _run_inventory_query(
        self,
        role: str,
        question: str,
        allowed_departments: frozenset[str],
        only_alerts: bool,
    ) -> ErpQueryResult:
        if only_alerts:
            sql = (
                "SELECT sku_code, item_name, warehouse, qty_on_hand, safety_stock, "
                "(safety_stock - qty_on_hand) AS shortage_qty "
                "FROM erp_inventory_summary "
                "WHERE qty_on_hand < safety_stock "
                "ORDER BY shortage_qty DESC"
            )
        else:
            sql = (
                "SELECT sku_code, item_name, warehouse, qty_on_hand, safety_stock, "
                "MAX(safety_stock - qty_on_hand, 0) AS shortage_qty "
                "FROM erp_inventory_summary ORDER BY sku_code"
            )
        rows = [dict(r) for r in self._conn.execute(sql).fetchall()]
        total_shortage = sum(int(r["shortage_qty"]) for r in rows)
        lines = [
            f"- {r['sku_code']} {r['item_name']}（{r['warehouse']}）："
            f"现存 {r['qty_on_hand']} / 安全库存 {r['safety_stock']}，缺口 {r['shortage_qty']}"
            for r in rows
        ]
        answer = (
            f"【只读 ERP 报表：库存预警】共发现 {len(rows)} 项低于安全库存的物料，"
            f"合计缺口 {total_shortage} 件：\n" + "\n".join(lines)
        )
        audit = AuditRecord.create(
            role=role,
            question=question,
            allowed_departments=allowed_departments,
            matched_doc_ids=["ERP:erp_inventory_summary"],
            refused=False,
        )
        return ErpQueryResult(
            tool_name="query_inventory_alerts",
            answer=answer,
            rows=rows,
            summary={"alert_count": len(rows), "total_shortage_qty": total_shortage},
            refused=False,
            audit=audit,
        )

    def _run_ap_query(
        self,
        role: str,
        question: str,
        allowed_departments: frozenset[str],
    ) -> ErpQueryResult:
        sql = (
            "SELECT invoice_no, vendor_name, amount_cny, due_date, status "
            "FROM erp_ap_invoices "
            "WHERE status IN ('OVERDUE', 'PENDING') "
            "ORDER BY due_date ASC"
        )
        rows = [dict(r) for r in self._conn.execute(sql).fetchall()]
        overdue_rows = [r for r in rows if r["status"] == "OVERDUE"]
        overdue_total = round(sum(float(r["amount_cny"]) for r in overdue_rows), 2)
        unpaid_total = round(sum(float(r["amount_cny"]) for r in rows), 2)
        lines = [
            f"- {r['invoice_no']}｜{r['vendor_name']}｜¥{r['amount_cny']:,.2f}｜"
            f"到期日 {r['due_date']}｜状态 {r['status']}"
            for r in rows
        ]
        answer = (
            f"【只读 ERP 报表：应付账款（AP）】当前未付共 {len(rows)} 笔"
            f"（合计 ¥{unpaid_total:,.2f}），其中逾期 {len(overdue_rows)} 笔"
            f"（合计 ¥{overdue_total:,.2f}）：\n" + "\n".join(lines)
        )
        audit = AuditRecord.create(
            role=role,
            question=question,
            allowed_departments=allowed_departments,
            matched_doc_ids=["ERP:erp_ap_invoices"],
            refused=False,
        )
        return ErpQueryResult(
            tool_name="query_ap_overdue_summary",
            answer=answer,
            rows=rows,
            summary={
                "unpaid_count": len(rows),
                "overdue_count": len(overdue_rows),
                "overdue_total_cny": overdue_total,
                "unpaid_total_cny": unpaid_total,
            },
            refused=False,
            audit=audit,
        )

    def _run_po_query(
        self,
        role: str,
        question: str,
        allowed_departments: frozenset[str],
    ) -> ErpQueryResult:
        sql = (
            "SELECT po_no, vendor_name, item_category, amount_cny, order_date, status "
            "FROM erp_purchase_orders ORDER BY order_date DESC"
        )
        rows = [dict(r) for r in self._conn.execute(sql).fetchall()]
        total_amount = round(sum(float(r["amount_cny"]) for r in rows), 2)
        pending_quote = [r for r in rows if r["status"] == "PENDING_QUOTE"]
        lines = [
            f"- {r['po_no']}｜{r['vendor_name']}（{r['item_category']}）｜"
            f"¥{r['amount_cny']:,.2f}｜状态 {r['status']}"
            for r in rows
        ]
        answer = (
            f"【只读 ERP 报表：采购订单（PO）】当前共 {len(rows)} 笔订单"
            f"（总额 ¥{total_amount:,.2f}），其中待比价 {len(pending_quote)} 笔：\n"
            + "\n".join(lines)
        )
        audit = AuditRecord.create(
            role=role,
            question=question,
            allowed_departments=allowed_departments,
            matched_doc_ids=["ERP:erp_purchase_orders"],
            refused=False,
        )
        return ErpQueryResult(
            tool_name="query_purchase_orders",
            answer=answer,
            rows=rows,
            summary={
                "order_count": len(rows),
                "pending_quote_count": len(pending_quote),
                "total_amount_cny": total_amount,
            },
            refused=False,
            audit=audit,
        )
