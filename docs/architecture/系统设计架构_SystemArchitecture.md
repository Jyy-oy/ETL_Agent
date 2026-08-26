# ETL-Agent 系统设计架构

状态：MVP 设计基线，可演进到多租户生产部署

## 1. 架构目标

系统提供“自然语言需求 → 结构化 ETL 方案 → 独立审批 → 受管执行 → 质量报告/回滚”的控制面能力。架构必须保证：

1. 控制面不搬运海量业务数据，数据面由 SeaTunnel 执行。
2. LLM 只生成候选和澄清文本，权限、预算、审批和副作用由确定性代码控制。
3. 所有外部副作用都经过 Prepare/Approve/Commit、Capability、Tool Broker 和 Outbox。
4. 对话、工作流、执行三种状态持久化隔离，并可跨请求恢复。
5. MVP 先实现一条合成 MySQL → VM Doris 真实链路，同时不把连接器、Provider、策略和执行引擎写死。

## 2. 逻辑分层

```text
┌─────────────────────────────────────────────────────────────────┐
│ Presentation: Vue Console / REST API / SSE or Polling           │
├─────────────────────────────────────────────────────────────────┤
│ Application: use cases, tenant context, idempotency, errors     │
├─────────────────────────────────────────────────────────────────┤
│ Workflow: LangGraph, clarification, generation, checkpoints     │
├─────────────────────────────────────────────────────────────────┤
│ Harness: PDP, Prepare/Approve/Commit, Capability, Broker, Ledger │
├─────────────────────────────────────────────────────────────────┤
│ Domain: Pipeline, Profile, EtlPlan, Quality, Approval, Run       │
├─────────────────────────────────────────────────────────────────┤
│ Infrastructure: PostgreSQL, Redis, MinIO, Vault, LLM, SeaTunnel  │
└─────────────────────────────────────────────────────────────────┘
```

依赖方向从上到下；Domain 不依赖 FastAPI、Celery、具体 LLM SDK 或 SeaTunnel。Infrastructure 通过端口/适配器实现 Domain/Application 所需的接口。

## 3. 运行时拓扑

```text
Browser / Vue
      │ HTTPS
      ▼
FastAPI Control Plane ───────► Remote Bailian LLM API
      │                         (HTTPS, redacted profile only)
      ├── PostgreSQL 16
      │    ├─ business facts
      │    ├─ LangGraph checkpoints
      │    ├─ outbox
      │    └─ evidence ledger
      ├── Redis 7
      │    ├─ Celery broker/result
      │    └─ replay guard / short cache
      ├── MinIO / S3
      │    └─ file assets, artifacts, benchmark results
      ├── Vault KV v2
      │    └─ source/target secrets
      └── Outbox → Celery Worker → Tool Broker → SeaTunnel Zeta
                                               │
                                  source DB / files → target DB
```

MVP 可在 Windows/PyCharm 运行 API/Workflow，连接 Ubuntu VM 上的基础设施；生产再将控制面、Worker 和 SeaTunnel 分开部署。

## 4. 核心模块边界

| 模块 | 责任 | 不负责 |
| --- | --- | --- |
| API | 认证、请求校验、调用用例、稳定错误和 request ID | 直接调用 SeaTunnel 或保存密码 |
| Project/Access | 租户、项目、成员、角色槽和职责分离 | 运行时令牌验签 |
| Connection/Profile | 连接引用、只读探查、脱敏 Profile、文件资产 | 搬运业务数据 |
| LLM Gateway | Provider、模型、Prompt 版本、超时/重试、敏感字段过滤 | 决定审批和执行权限 |
| Workflow | LangGraph 节点、Checkpoint、中断/恢复 | 绕过确定性门禁 |
| Gate | EtlPlan/HOCON/Schema/Quality/预算校验 | 调用模型生成内容 |
| Harness | PDP、三阶段协议、Capability、Replay Guard、Tool Broker、Ledger | 解释业务数据内容 |
| Execution | Outbox、Celery、SeaTunnel job、状态和指标 | 直接接收未授权用户命令 |
| Quality/Supervision | 错误分流、阈值、快照、诊断和回滚用例 | 修改审批事实 |
| Benchmark | 固定数据集、评测、策略版本和报告 | 在线改变生产策略 |

## 5. MVP 与扩展点

| 领域 | MVP | 扩展方式 |
| --- | --- | --- |
| LLM Provider | 百炼一个 OpenAI 兼容端点 | `LLMProvider` 接口注册 DeepSeek、Qwen、企业网关 |
| 数据源 | MySQL 源、Doris 目标 | `SourceConnector`/`TargetConnector` 插件和能力声明 |
| 文件 | CSV/JSON/Parquet 基础 Profile | 格式解析器、对象存储策略和大文件异步任务 |
| 执行引擎 | SeaTunnel Zeta | Engine Adapter 支持不同 SeaTunnel 集群或其他引擎 |
| 审批 | Checker 1 + Checker 2 + Operator | PDP 策略包、风险级别和动态审批槽 |
| 实时性 | 轮询 ExecutionRun | SSE/WebSocket/事件总线，不改变执行事实模型 |
| 身份 | 本地 JWT 账号 | OIDC/LDAP/SSO Adapter |
| 存储 | 单 PostgreSQL + Redis | 读写分离、Redis Cluster、对象存储托管化 |
| 评测 | L0/L1 本地 | L2 隔离执行、分布式 Runner、结果仓库 |

扩展模块必须通过接口、能力声明和版本化配置接入，不在核心用例中写 `if provider == ...` 的分支堆积。

## 6. 状态模型

### 6.1 Conversation State

保存用户输入、澄清问题、回答、模型响应摘要和消息序号。不得保存未经脱敏的连接凭据或完整业务样本。

### 6.2 Workflow State

保存 LangGraph 当前节点、Profile 引用、候选 EtlPlan、门禁结果、修复次数和 Checkpoint。每次模型输出都带 Provider、Model、PromptVersion 和输入摘要。

### 6.3 Execution State

保存 Preparation、审批决策、Capability 摘要、ExecutionRun、SeaTunnel job ID、运行指标、质量结果和回滚状态。Execution State 不反向修改已冻结 PipelineVersion。

## 7. 一致性与可靠性

- Preparation 是冻结事实快照；Commit 前重新计算指纹并拒绝漂移。
- ExecutionRun 创建、Capability 摘要落库和 Outbox 事件在同一 PostgreSQL 事务中完成。
- Outbox 消费采用事件 ID 幂等；Worker 重试不能重复创建 SeaTunnel 作业。
- Replay Guard 使用 Redis 原子写入/消费，并设置不小于 Capability TTL 的过期时间。
- 外部执行状态采用状态机和版本号，迟到事件不能覆盖终态。
- 运行取消、超限和回滚都必须可重复调用且有审计事件。

## 8. 可观测性

所有请求、AgentRun、Preparation、ApprovalRequest、ExecutionRun 和 Outbox 事件共享 correlation ID。指标至少包括 API 延迟、LLM 延迟/重试、Checkpoint 恢复、队列积压、SeaTunnel 状态、吞吐、错误拒绝率、放大比、回滚次数和审计链校验结果。

日志采用结构化字段，Secret 只记录引用和摘要。生产环境应将日志、指标和审计事件分别设置访问权限和保留周期。

## 9. 架构约束与风险

- VM Docker 是开发环境，不等于生产 HA；生产 PostgreSQL、Redis、Vault、MinIO 和 SeaTunnel 需要独立可用性与备份方案。
- SeaTunnel 版本、Zeta API 和连接器插件是外部契约，必须通过 Adapter 和集成测试隔离变化。
- 远端百炼带来网络、配额、成本和数据出境风险；必须有超时、重试、Provider 降级和脱敏策略。
- 单 PostgreSQL 承载业务与 Checkpoint 适合 MVP；高负载时需要独立连接池、分区和容量评估。
