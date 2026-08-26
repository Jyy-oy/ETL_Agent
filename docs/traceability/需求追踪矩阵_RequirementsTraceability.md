# ETL-Agent 需求追踪矩阵

本文把整理版需求映射到架构、实现模块、测试和验收证据。编码实现后，应将“模块/测试”列替换为真实文件、测试 ID 或 CI 链接。

| 需求 ID | 需求摘要 | MVP 模块 | 设计文档 | 测试/验收证据 |
| --- | --- | --- | --- | --- |
| REQ-SEC-01 | 用户、项目、成员和职责分离 | Identity/Access | 系统详细设计、威胁模型 | 自批/职责混用测试 |
| REQ-DATA-01 | MySQL/Doris 连接与只读 Profile | Connection/Profile | 系统架构、系统详细设计 | 连接测试、脱敏 Profile |
| REQ-DATA-02 | 文件/对象资产 | FileAsset + MinIO | 系统架构 | 文件头/格式/大小测试 |
| REQ-AGENT-01 | LangGraph 澄清、Checkpoint 恢复 | `src/etl_agent/workflows/graph.py`、`checkpoint.py`、`api/generation.py` | 系统详细设计、受管执行时序图 | `test_m3_generation.py` 缺参测试；VM PostgreSQL setup 和同 thread 恢复已手工验证 |
| REQ-AGENT-02 | EtlPlan/HOCON 结构化生成和校验 | `domain/generation.py`、`workflows/validation.py`、`infrastructure/llm.py` | 系统详细设计 | `tests/unit/test_m3_generation.py` 合法/非法/预算/HOCON/Provider 测试 |
| REQ-HARNESS-01 | PDP P0-P3 风险和三阶段协议 | Harness | 系统架构、系统详细设计 | PDP、Prepare/Approve/Commit 测试 |
| REQ-HARNESS-02 | Ed25519 单次 Capability 和防重放 | Capability + Redis Guard | 安全设计、UML 类图 | 伪造/过期/重放测试 |
| REQ-HARNESS-03 | Outbox 和 Evidence Ledger | PostgreSQL Outbox/Ledger | 系统详细设计 | 事务失败、幂等、哈希链测试 |
| REQ-EXEC-01 | Celery/SeaTunnel 受管执行 | Worker + Engine Adapter | 系统架构、受管执行时序图 | `test_m5_engine.py` 提交/状态/取消/清理/Swap/回滚测试 |
| REQ-QUALITY-01 | 影子表、错误表、QualityContract | Quality/Supervision | 系统详细设计 | `test_m5_quality.py` 分流测试；质量 API/Outbox |
| REQ-QUALITY-02 | 行数、字节、时长、放大比、拒绝率监督 | Runtime Supervision | 系统架构 | `test_m5_quality.py` 超预算硬中断判定 |
| REQ-UI-01 | Studio、审批、运行中心 | `frontend/src/App.vue`、项目级查询 API | MVP Scope、Development Handbook | `npm run build`；后续补充浏览器 E2E |
| REQ-BENCH-01 | L0/L1/L2 Benchmark | `src/etl_agent/benchmark.py`、`api/benchmarks.py`、`scripts/run_benchmark.py`、`benchmark_runs` | MVP 范围与路线图、测试与质量策略 | `test_m6_benchmark.py`；固定参数报告；M6.1 已持久化历史摘要；L2 仍为可选扩展 |
| REQ-OPS-01 | 本地 VM、部署、备份、回滚 | Compose/Operations | Ubuntu 虚拟机部署、系统生命周期 | 部署演练/恢复演练 |

## 1. 追踪规则

- 每个 P0 需求必须关联至少一个设计决策、一个自动化测试和一个验收证据。
- 需求变更先修改整理版需求，再更新矩阵、架构、测试和开发手册。
- 设计中明确的后续能力可以登记为 P1/P2，但不能伪造为 MVP 验收项。
- 测试失败或验收不通过时，保留失败记录和影响范围，不只更新“通过”状态。

## 2. 当前缺口

仓库已完成 M1.2 控制面基础、M2.3 连接/Profile/文件资产核心能力、M3.2 Agent 生成边界、M4.4 Harness Commit/Outbox/Ledger、M5.5 Worker/质量监督/真实合成数据面闭环和 M6/M6.1 控制台、Benchmark 与历史摘要持久化；质量契约、预算判定、取消/清理/Swap/回滚 API、SeaTunnel 2.3.10 REST/指标转换、合成 MySQL 数据脚本、真实 Doris 原子 DDL、项目级查询和确定性 L0/L1 报告均具备本地或 VM 验证证据。当前缺口是浏览器 E2E、生产端到端压测、实时推送、真实百炼调用、Benchmark L2 真实链路和企业 SSO；学习项目使用合成数据，不要求真实业务库。
