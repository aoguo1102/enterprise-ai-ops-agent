# Enterprise AI Ops Agent

[![CI](https://github.com/aoguo1102/enterprise-ai-ops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/aoguo1102/enterprise-ai-ops-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)

> **企业内网级权限隔离知识库与只读 ERP 报表问答智能体（PoC 与工程交付参考实现）**

面向制造、贸易与多部门协作型企业的私有化 AI 落地原型。针对企业真实痛点——**共享盘文档分散、跨部门权限越权风险、业务查 ERP 报表高频依赖 IT**——提供「部门级 RBAC 权限前置过滤 + 只读 ERP 报表白名单查询 + 可追溯引用 + 越权拒答 + 审计日志 + 一键容器化部署」的端到端交付参考。

---

## 一、方案架构（私有化 / 混合云部署视角）

```mermaid
flowchart LR
    U["业务用户 / 部门员工<br/>(Web Demo / 企业微信 / 飞书)"] --> GW["FastAPI 统一入口<br/>角色校验 + 提示词/SQL注入防护"]
    GW --> RT["意图路由<br/>(SOP 知识检索 / 只读 ERP 报表)"]
    RT --> RBAC["RBAC 权限前置过滤<br/>(映射 AD 域控 / 共享盘分组)"]
    RBAC --> RAG["企业知识库检索<br/>(MVP: 本地脱敏文档 / 生产: pgvector)"]
    RBAC --> ERP["只读 ERP 白名单工具<br/>(SQLite query_only / 生产: Oracle只读视图)"]
    RAG --> OUT["带引用回答 / 越权拒答"]
    ERP --> OUT["带结构化表格回答 / 越权拒答"]
    GW --> AUD["结构化审计日志<br/>(角色、路由、命中来源、是否拒答)"]
```

---

## 二、核心能力与安全边界

| 能力维度 | 当前 MVP 已实现 | 生产演进规划 |
|---|---|---|
| **权限隔离（RBAC）** | 知识检索与 ERP 工具调用前均强制按部门角色过滤（`public` / `finance` / `procurement` / `it_admin`），杜绝无权数据进入上下文 | 对接企业 AD 域控 / LDAP / 钉钉飞书组织架构 |
| **只读 ERP 报表工具** | 内置 3 个白名单参数化只读工具（低库存预警 / 逾期应付账款 / 采购订单汇总），SQLite 强制 `PRAGMA query_only = ON`，**永不执行用户原始 SQL** | 对接生产 Oracle / PostgreSQL 只读账号与受控报表视图 |
| **安全与防注入** | 输入长度限制、Prompt Injection 与高危 SQL（`DROP/DELETE/UPDATE`）规则拦截、越权自动拒答 | 接入语义级护栏模型与敏感字段脱敏网关 |
| **可追溯与审计** | 每次问答返回路由类型、引用文档/表来源与结构化审计记录 | 接入 OpenTelemetry / Langfuse 与数据库持久化审计 |
| **工程与现场演示** | 离线 `pytest` 单测、Ruff 静态检查、GitHub Actions CI、内置可视化演示页（支持角色切换与报表表格渲染） | 接入 `pgvector` 混合检索 + OpenAI 兼容大模型网关 + 30 题评测集 |

### 内置角色权限矩阵（SOP 文档 + ERP 报表）

| 角色 ID | 角色名称 | 可访问 SOP 文档 | 可调用只读 ERP 工具 |
|---|---|---|---|
| `employee` | 普通员工 | `public`（共享盘与办公网指南） | `query_inventory_alerts`（公共低库存预警） |
| `finance` | 财务组 | `public` + `finance`（报销与月结 SOP） | 库存预警 + `query_ap_overdue_summary`（应付账款 AP） |
| `procurement` | 采购组 | `public` + `procurement`（供应商与比价 SOP） | 库存预警 + `query_purchase_orders`（采购订单 PO） |
| `it_admin` | IT 管理员 | 全部演示 SOP 文档 | 全部 3 个只读 ERP 报表工具 |

---

## 三、5 分钟快速复现

### 方式 A：本地运行（Windows PowerShell）

> 要求：Python `>= 3.11`（默认离线 Mock 与内存只读 SQLite 模式，**无需填写真实 API Key 即可跑通全部单测与可视化演示页**）。

```powershell
# 1. 创建并激活虚拟环境
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装项目与开发依赖
pip install -e ".[dev]"

# 3. 复制环境变量模板
Copy-Item .env.example .env

# 4. 运行离线单元测试（覆盖配置校验、RBAC 知识库、只读 ERP 工具与注入拦截）
pytest tests/unit

# 5. 启动服务并打开演示页面
uvicorn ops_agent.main:app --reload --port 8000
```

启动后访问：
- 交互演示页：`http://127.0.0.1:8000/`（一键切换部门角色，现场演示 SOP 问答 + ERP 结构化报表 + 越权拦截）
- Swagger API 文档：`http://127.0.0.1:8000/docs`
- 只读 ERP 工具清单：`http://127.0.0.1:8000/api/v1/erp/tools`
- 健康检查：`http://127.0.0.1:8000/health`

### 方式 B：Docker Compose 一键启动（含 pgvector）

```powershell
docker compose up --build
```

---

## 四、目录结构

```text
enterprise-ai-ops-agent/
├── .github/workflows/ci.yml        # GitHub Actions：Ruff + 离线单测
├── data/
│   ├── README.md                   # 数据合规声明（100% 合成脱敏数据）
│   └── sample_docs/                # 演示用脱敏 SOP 文档
├── docs/
│   ├── solution_proposal.md        # 售前解决方案书（架构、权限、实施周期、ROI）
│   └── poc_eval_report.md          # PoC 评测报告模板
├── src/ops_agent/
│   ├── config.py                   # 环境变量与边界强校验
│   ├── rbac.py                     # 部门权限矩阵、提示词/SQL注入拦截与审计记录
│   ├── knowledge.py                # 权限前置过滤的企业知识检索器
│   ├── erp_tools.py                # 白名单只读 ERP 模拟库（PRAGMA query_only）与报表工具
│   └── main.py                     # FastAPI 统一路由与内置可视化演示页
├── tests/unit/                     # 离线单元测试（无 Key 依赖）
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

---

## 五、PoC 评测指标（持续更新）

> 说明：以下指标仅记录真实运行结果，未跑完整 LLM 评测集前保持 `待实测`，不虚构数据。

| 指标 | 定义 | 当前状态 |
|---|---|---|
| CI 静态检查与离线单测 | GitHub Actions 自动执行 `ruff check` + `pytest tests/unit` | ✅ CI 已验证通过 |
| ERP 工具白名单与只读隔离 | 跨部门调用 AP/PO 拒绝、SQLite `query_only` 写入拦截 | ✅ 离线单测已覆盖 |
| 越权访问拦截率（固定集） | 低权限角色提问高权限文档/报表时的正确拒答比例 | `待实测（30 题固定集）` |
| 检索 Recall@5 | Top-5 召回片段包含标准答案文档的比例 | `待接入 pgvector 后实测` |
| P95 端到端延迟与单次成本 | 接入真实大模型后的响应延迟与 Token 成本 | `待接入真实模型后实测` |

---

## 六、相关文档与已知限制

- 售前与实施架构方案：[docs/solution_proposal.md](docs/solution_proposal.md)
- PoC 验收评测报告模板：[docs/poc_eval_report.md](docs/poc_eval_report.md)
- **当前限制**：现版本为可独立演示的离线 MVP（知识检索采用本地脱敏文档、ERP 工具采用内存只读 SQLite 合成数据）；下一步将接入 OpenAI 兼容大模型客户端、`pgvector` 混合检索以及 30 题固定评测脚本。
