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
| REQ-EXEC-01 | Celery/SeaTunnel 受管执行 | Worker + Engine Adapter | 系统架构、受管执行时序图 | 作业提交/状态/取消测试 |
| REQ-QUALITY-01 | 影子表、错误表、QualityContract | Quality/Supervision | 系统详细设计 | 分流/Swap/回滚测试 |
| REQ-QUALITY-02 | 行数、字节、时长、放大比、拒绝率监督 | Runtime Supervision | 系统架构 | 超预算中断测试 |
| REQ-UI-01 | Studio、审批、运行中心 | Vue Console | MVP Scope、Development Handbook | 前端 E2E |
| REQ-BENCH-01 | L0/L1/L2 Benchmark | Benchmark | MVP 范围与路线图、测试与质量策略 | 固定数据集评测报告 |
| REQ-OPS-01 | 本地 VM、部署、备份、回滚 | Compose/Operations | Ubuntu 虚拟机部署、系统生命周期 | 部署演练/恢复演练 |

## 1. 追踪规则

- 每个 P0 需求必须关联至少一个设计决策、一个自动化测试和一个验收证据。
- 需求变更先修改整理版需求，再更新矩阵、架构、测试和开发手册。
- 设计中明确的后续能力可以登记为 P1/P2，但不能伪造为 MVP 验收项。
- 测试失败或验收不通过时，保留失败记录和影响范围，不只更新“通过”状态。

## 2. 当前缺口

仓库已完成 M1.2 控制面基础、M2.3 连接/Profile/文件资产核心能力、M3.2 Agent 生成边界、M4.4 Commit/Outbox/Ledger 和 M5.1 Worker/SeaTunnel Adapter 边界；SecretProvider、MySQL/Doris 连接测试、脱敏 Profile、文件校验、EtlPlan/HOCON 门禁、fake Provider、Prompt 上限、Commit 指纹/幂等、账本哈希链和 Adapter Mock 测试已具备验证证据。真实 SeaTunnel Zeta 作业、影子表/质量分流/Swap/回滚、真实业务数据和百炼调用仍待补齐矩阵字段。
