# 示例数据说明

本目录下 `sample_docs/` 中的所有文档，以及 `src/ops_agent/erp_tools.py` 内置的内存 SQLite 模拟 ERP 业务表（`erp_inventory_summary`、`erp_ap_invoices`、`erp_purchase_orders`），均为**100% 人工合成的脱敏演示数据**，仅用于展示企业部门级 RBAC 权限隔离与只读报表查询流程。

- 不包含任何真实企业内部文档、客户信息、价格数据、员工个人信息或真实 ERP 表结构。
- 请勿在公开仓库的 `data/` 目录下提交任何真实生产数据；本地私有测试数据请放入 `data/private/`（已在 `.gitignore` 中忽略）。
