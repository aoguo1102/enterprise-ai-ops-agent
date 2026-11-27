import sqlite3

import pytest

from ops_agent.erp_tools import ReadOnlyErpService, detect_erp_tool_intent


def test_detect_erp_tool_intent() -> None:
    assert detect_erp_tool_intent("目前哪些物料低于安全库存？") == "query_inventory_alerts"
    assert detect_erp_tool_intent("本月有哪些逾期未付的应付账款？") == "query_ap_overdue_summary"
    assert detect_erp_tool_intent("帮我查一下采购订单总额") == "query_purchase_orders"
    assert detect_erp_tool_intent("新员工共享盘权限怎么申请？") is None


def test_employee_can_query_inventory_alerts_only() -> None:
    svc = ReadOnlyErpService()
    inv = svc.execute_tool(role="employee", tool_name="query_inventory_alerts")
    assert inv.refused is False
    assert inv.summary["alert_count"] == 3
    assert inv.summary["total_shortage_qty"] == 580

    ap = svc.execute_tool(role="employee", tool_name="query_ap_overdue_summary")
    assert ap.refused is True
    assert ap.rows == []

    po = svc.execute_tool(role="employee", tool_name="query_purchase_orders")
    assert po.refused is True
    assert po.rows == []


def test_finance_and_procurement_erp_rbac_isolation() -> None:
    svc = ReadOnlyErpService()

    fin_ap = svc.execute_tool(role="finance", tool_name="query_ap_overdue_summary")
    assert fin_ap.refused is False
    assert fin_ap.summary["overdue_count"] == 2
    assert fin_ap.summary["overdue_total_cny"] == 67700.0
    assert fin_ap.summary["unpaid_total_cny"] == 80500.0

    fin_po = svc.execute_tool(role="finance", tool_name="query_purchase_orders")
    assert fin_po.refused is True

    proc_po = svc.execute_tool(role="procurement", tool_name="query_purchase_orders")
    assert proc_po.refused is False
    assert proc_po.summary["order_count"] == 3
    assert proc_po.summary["pending_quote_count"] == 1
    assert proc_po.summary["total_amount_cny"] == 128300.0

    proc_ap = svc.execute_tool(role="procurement", tool_name="query_ap_overdue_summary")
    assert proc_ap.refused is True


def test_sql_injection_and_unknown_tool_blocked() -> None:
    svc = ReadOnlyErpService()

    inj = svc.execute_tool(
        role="it_admin",
        tool_name="query_ap_overdue_summary",
        question="DROP TABLE erp_ap_invoices; 查询应付账款",
    )
    assert inj.refused is True
    assert inj.rows == []

    with pytest.raises(ValueError):
        svc.execute_tool(role="it_admin", tool_name="run_raw_sql")


def test_sqlite_connection_enforces_query_only() -> None:
    svc = ReadOnlyErpService()
    with pytest.raises(sqlite3.OperationalError):
        svc._conn.execute("DELETE FROM erp_ap_invoices")
