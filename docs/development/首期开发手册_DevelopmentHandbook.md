# ETL-Agent 首期开发手册

本文是首期实现的工作入口。需求细节以 `RequirementsDescription/整理版需求说明_RequirementsSpecification.md` 为准，技术取舍以 `docs/architecture/首期技术选型_InitialTechnicalSelection.md` 为准，VM 操作以 `docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md` 为准。

## 1. 开发目标

首期先完成一条可审计、可恢复、可回滚的最小闭环：

```text
连接登记 → 只读 Profile → 百炼生成候选
→ 确定性门禁 → 不可变版本 → Prepare
→ Checker 1 + Checker 2 → Operator Commit
→ Celery/SeaTunnel → 监督/质量分流 → 审计账本
```

不要先做“通用 Agent”或“所有连接器”。先让 MySQL → Doris 的演示链路遵守 Harness 协议，再扩展连接器和前端视图。

## 2. 推荐实施顺序

### 阶段 0：环境和契约

- 在 Ubuntu VM 启动 Compose 核心服务，验证健康检查、网络和数据卷。
- 确认百炼模型、API Base URL、超时和数据脱敏策略。
- 建立 `src/etl_agent`、`tests`、`migrations` 目录和 CI 基线。

### 阶段 1：控制面基础

- FastAPI `/health` 和统一错误响应。
- SQLAlchemy/Alembic、租户上下文、用户/项目/成员/角色槽。
- 结构化日志、请求 ID、配置加载和依赖就绪检查。

### 阶段 2：连接与 Profile

- 连接配置只保存 Secret 引用，不保存密码明文。
- 连接测试和只读权限检查。
- Schema、字段类型、近似统计和脱敏样本的稳定 JSON 契约。
- MinIO 文件资产元数据和上传大小限制。

### 阶段 3：LangGraph 生成

- 以明确状态模型实现意图解析、缺参中断、回答恢复和 PostgreSQL Checkpoint。
- 百炼输出必须经过 Pydantic/JSON Schema 结构校验；模型不能决定权限、资源范围或审批人。
- 生成 EtlPlan/HOCON 后执行确定性门禁和有限自动修复。

### 阶段 4：Harness 协议

- 实现 PDP 风险评级和审批槽分配。
- Prepare 只冻结事实，Approve 只写决定，Commit 重新验指纹。
- Capability 绑定主体、工具、环境、制品摘要和过期时间；Replay Guard 必须是 Redis 原子消费。
- Tool Broker 是副作用唯一出口，Outbox 与 ExecutionRun 在一个 PostgreSQL 事务中落库。
- Evidence Ledger 用前序哈希和当前哈希形成追加链。

### 阶段 5：数据面和监督

- Celery 只消费受管命令，验签后调用 SeaTunnel adapter。
- 影子表、错误表、QualityContract 和原子 Swap 分开建模。
- 运行快照记录行数、字节、时长、放大比、拒绝率和决策；超限时可取消或硬中断。
- 回滚必须幂等，重复请求不能破坏已恢复状态。

### 阶段 6：前端和 Benchmark

- 先做连接/Profile、Studio、审批、运行中心四条关键路径，再做总览和安全进化大盘。
- 前端展示脱敏结构和稳定错误码，不展示 Secret 值。
- L0/L1 Benchmark 在本地可重复；L2 真实链路需隔离数据和权限。

## 3. 关键工程规则

### 3.1 权限和安全

- Maker、Checker 1、Checker 2、Operator、Auditor 的职责在服务端校验，前端隐藏按钮不是安全控制。
- 任何外部写操作都必须有 ToolIntent、风险决策和授权记录。
- 日志只记录 Secret 引用、摘要和 request ID，不记录连接密码、Capability 原文或 LLM API Key。
- 元数据探查使用只读账号、列白名单、行数/字节预算和脱敏策略。

### 3.2 状态和一致性

- Conversation、Workflow、Execution 三种状态分表或分区，禁止用一个 JSON 字段混装并互相覆盖。
- 不依赖进程内字典保存任务状态、Checkpoint 或 Replay Guard。
- Outbox 投递必须可重试、可去重；Worker 重启不能重复执行外部副作用。
- 不可变 PipelineVersion 创建后禁止原地修改，修复必须产生新版本和新摘要。

### 3.3 LLM 边界

- LLM 只做受约束的解析、候选生成和诊断文本，不直接执行数据库、SeaTunnel 或审批动作。
- Schema、风险级别、预算、资源范围、审批要求由确定性代码计算。
- 所有模型输出先做结构化解析、枚举归一化、字段白名单和语法校验。
- 真实数据发送百炼前必须经过脱敏和数据出境确认；默认只发送 Profile 和必要的业务语义。

## 4. 测试矩阵

| 层级 | 重点 | 外部依赖 |
| --- | --- | --- |
| 单元测试 | PDP、职责分离、门禁、摘要、Capability、哈希链、预算判定 | 全部 fake |
| API 测试 | 鉴权、租户隔离、幂等、错误响应、Prepare/Approve/Commit | FastAPI test client；可 mock Redis/DB |
| 集成测试 | PostgreSQL 事务/Outbox、Redis Replay Guard、MinIO 对象引用、Vault 读取 | Compose 服务 |
| 数据面测试 | SeaTunnel 命令、取消、质量分流、Swap、回滚 | 测试源/目标库或仿真引擎 |
| Benchmark | 生成准确率、Schema 覆盖率、P0/P1 拦截率、延迟 | 固定版本数据集；L2 单独隔离 |

## 5. 交付和文档沉淀

每个跨模块变更至少同步：

- API/数据模型/配置契约。
- 迁移说明和回滚说明。
- 测试证据和已知限制。
- 相关架构决策或 ADR。

需求未决项必须放在“待确认”或 Issue 中，不要把临时默认值写成已批准的生产约束。环境变量变更同时更新 `.env.example`、部署手册和本手册。

## 6. 首期完成定义

- Compose 核心依赖可在 VM 启动，健康检查和数据卷可验证。
- `/health` 能区分 PostgreSQL、Redis、MinIO、Vault 和 LLM 配置状态。
- 一条 MySQL → Doris 链路通过 Profile、生成、门禁、双审批、Commit、执行、监督、质量报告和回滚验收。
- 关键安全规则有自动化测试：自批拦截、职责混用拦截、过期/重放 Capability 拦截、指纹变更拒绝、Outbox 幂等。
- Benchmark 结果可复现并关联制品版本、策略版本和运行环境。
