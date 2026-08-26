# ETL-Agent 系统详细设计

状态：MVP 详细设计基线

## 1. 领域模块与聚合

### 1.1 Identity/Project 聚合

核心实体：`User`、`Project`、`ProjectMembership`、`ProjectRoleGrant`。

约束：用户必须通过 Membership 才能访问项目；RoleGrant 为 Maker、Checker 1、Checker 2、Operator、Auditor 分配职责槽；高风险 Preparation 的同一操作者不得占用互斥槽。

### 1.2 Connection/Profile 聚合

核心实体：`ConnectionProfile`、`MetadataProfile`、`FileAsset`、`SecretRef`。

连接实体只保存连接类型、非敏感参数、Secret 引用、项目归属和版本信息。探查作业使用只读凭据，输出字段白名单、类型、主键、近似统计、脱敏样本和 Profile 摘要。

### 1.3 Pipeline/Artifact 聚合

核心实体：`Pipeline`、`PipelineVersion`、`PipelineArtifact`、`QualityContract`、`RuntimeSupervisionContract`。

PipelineVersion 创建后不可更新。修改任何 EtlPlan、HOCON、Schema 映射或质量规则都创建新版本，计算 SHA-256 `artifact_digest`，并将大对象放 MinIO、摘要和索引放 PostgreSQL。

### 1.4 AgentRun/Workflow 聚合

核心实体：`AgentRun`、`ConversationMessage`、`WorkflowCheckpoint`、`GenerationAttempt`。

AgentRun 记录 Thread ID、Prompt 摘要、Provider/Model、状态、节点计数、修复次数和错误引用。完整模型输入/输出按脱敏策略保留，默认只保存结构化结果和摘要。

### 1.5 Governance/Execution 聚合

核心实体：`Preparation`、`ApprovalRequest`、`CapabilityGrant`、`ExecutionRun`、`OutboxEvent`、`AuditEvent`。

Preparation 冻结输入指纹、资源范围、风险、预算和回滚方案。ApprovalRequest 按 required role 独立决策。ExecutionRun 只引用不可变版本和 Preparation，不直接编辑业务方案。

## 2. 关键接口（端口）

```text
LLMProvider
  generate_structured(request, schema) -> StructuredResponse
  ask_clarification(context) -> ClarificationQuestion

SourceConnector
  test_connection(profile) -> ConnectionTestResult
  inspect_metadata(profile, budget) -> MetadataProfile

ExecutionEngine
  submit(command) -> EngineJobRef
  get_status(job_ref) -> EngineStatus
  cancel(job_ref) -> CancelResult

SecretProvider
  resolve(secret_ref) -> EphemeralSecret
  rotate(secret_ref) -> RotationResult

ObjectStore
  put/get/delete(uri, content)

PolicyDecisionPoint
  decide(tool_intent, resource_scope, data_classification) -> RiskDecision
```

这些接口是扩展边界。适配器负责将外部异常转换为稳定领域错误，不把 SDK 异常直接暴露给 API。

## 3. LLM 生成子系统

### 3.1 请求模型

```json
{
  "project_id": "...",
  "pipeline_version_id": "...",
  "business_request": "...",
  "source_profile_refs": ["..."],
  "target_profile_ref": "...",
  "quality_contract": {"...": "..."},
  "prompt_version": "etl-plan-v1"
}
```

发送到远端模型前执行：租户授权检查、字段/样本脱敏、长度限制、数据出境策略检查和输入摘要计算。禁止发送密码、Token、完整业务行或未批准的敏感字段。

### 3.2 生成管线

1. `IntentParseNode`：提取源、目标、转换意图和缺失参数。
2. `ProfileEnrichmentNode`：读取受管 MetadataProfile，不直接查询海量数据。
3. `CandidateGenerationNode`：调用百炼 Provider，要求结构化 EtlPlan 候选。
4. `SchemaValidationNode`：执行 Pydantic/JSON Schema、枚举和字段白名单校验。
5. `HoconCompileNode`：生成并解析 SeaTunnel HOCON。
6. `DeterministicGateNode`：校验资源范围、连接能力、质量规则、预算和安全策略。
7. `RepairNode`：只允许有限次数的结构化修复；每次修复增加 GenerationAttempt。
8. `HumanInterruptNode`：缺参或门禁无法自动修复时暂停，等待 API 提交回答后从 Checkpoint 恢复。

模型输出不能直接创建 Preparation、审批请求或 ExecutionRun。

M3.1 实现对应 `src/etl_agent/workflows/graph.py`、`src/etl_agent/workflows/validation.py` 和 `src/etl_agent/infrastructure/llm.py`。当前图支持 `needs_clarification`、结构化校验失败、一次有限修复和 `completed` 四类结果；`src/etl_agent/workflows/checkpoint.py` 使用 `AsyncPostgresSaver` 初始化 PostgreSQL Checkpoint。成功候选通过 API 写入草稿 `PipelineVersion`，以 EtlPlan/HOCON 规范化内容计算 SHA-256 后设置不可变标志；失败候选只记录 `AgentRun`/`GenerationAttempt` 证据。

澄清恢复由 `POST /api/v1/agent-runs/{run_id}/answers` 提供：服务端只合并答案文本，复用 AgentRun 的脱敏请求快照和 `thread_id`，不允许答案修改项目授权或预算上限。

## 4. Harness 执行协议

### Prepare

输入：PipelineVersion、Profile 摘要、请求主体、ToolIntent。

处理：计算输入指纹、资源范围、数据分级、PDP 风险、执行预算、影子表名和回滚方案；输出不可变 Preparation。

副作用：只写控制面事实，不调用源库写接口或 SeaTunnel。

M4.1 实现：`POST /api/v1/versions/{version_id}/prepare` 只接受已通过门禁且 `immutable=true` 的版本；服务端重新读取项目内 Profile 指纹，使用 PDP v1 计算 P0-P3 风险和 Checker 槽，保存 `preparations` 事实、输入指纹、预算、资源范围和有效期。M4.2 已在此基础上接入审批槽。

M4.2 实现：Prepare 同步创建 `approval_requests`；`POST /api/v1/approval-requests/{approval_id}/decisions` 使用 Preparation 行锁和审批槽行锁，检查当前用户是否拥有对应 Checker 职责且不是申请人。全部槽批准后才进入 `approved`，拒绝、过期或重复决定均返回稳定错误。

M4.3 基础能力：`harness/capability.py` 使用 Ed25519 对 `capability.v1` 声明签名和验签，绑定主体、工具、环境、Preparation、制品摘要与过期时间；`RedisReplayGuard` 对令牌摘要执行 `SET NX EX`。Capability 只有在 Commit 完成指纹复核后才允许签发和消费。

M4.4 实现：`POST /api/v1/preparations/{preparation_id}/commit` 使用 Preparation 行锁和版本/Profile 查询重新计算输入指纹；通过审批和有效期检查后签发 Capability，并在同一个 PostgreSQL 事务中创建 `ExecutionRun`、`OutboxEvent` 和 `EvidenceLedgerEvent`，将 Preparation 设置为 `committed`。重复 Commit 按 Preparation 唯一约束或 `Idempotency-Key` 返回已有执行事实；`GET /api/v1/execution-runs/{execution_id}` 只返回脱敏状态和摘要。Outbox 当前保存内部消费所需的 Capability 原文，生产部署必须改用 Vault/KMS 信封加密。

M5.1 实现：`workers/dispatcher.py` 是 Outbox Tool Broker，消费前验签 Capability、校验 Preparation/制品绑定并执行 Redis Replay Guard，再通过 `ExecutionEngine` 端口调用 `SeaTunnelAdapter`。Celery 任务只负责调度事件 ID 和建立短生命周期依赖，不在进程内保存执行状态；真实 Zeta HTTP 路径通过配置注入，后续集成测试确认具体版本契约。

### Approve

输入：Preparation 冻结视图和所需角色槽。

处理：Checker 1 审查数据映射/质量；Checker 2 审查资源/Secret/预算/回滚。服务端检查独立身份、职责分离和决策幂等。

副作用：追加 ApprovalRequest 决策和 AuditEvent。

### Commit

输入：Preparation ID、操作员身份、审批事实。

处理：重新计算指纹，确认所有审批满足策略，签发绑定主体/工具/环境/版本摘要的 Ed25519 Capability，使用 PostgreSQL 事务创建 ExecutionRun 和 OutboxEvent。

副作用：只允许 Tool Broker 消费 Outbox；重复 Commit 返回已有结果或稳定幂等错误。

## 5. 数据库设计原则

- 所有业务表包含租户/项目边界、创建/更新时间和必要的版本字段。
- 状态字段使用受限枚举；状态迁移由应用层显式校验。
- 大文本/大对象优先 MinIO，数据库保存 URI、摘要、大小、内容类型和不可变版本。
- `audit_events` 通过 `prev_event_hash`、`event_hash` 和载荷摘要构成追加链。
- JSON 字段只用于扩展属性，关键过滤字段单独建列和索引。
- Outbox 使用唯一事件 ID、状态、尝试次数、下一次投递时间和最后错误。

## 6. API 设计约定

- 所有 API 前缀为 `/api/v1`，写接口需要 `Idempotency-Key` 或业务幂等键。
- 错误结构统一为 `code`、`message`、`request_id`、`details`；不返回堆栈和 Secret。
- 分页使用 `limit`/`cursor`，不允许大范围无界查询。
- API 只返回脱敏 Profile、制品摘要和授权范围；下载大对象使用短时签名 URI。
- 异步操作返回 `202 Accepted` 和资源 ID；客户端通过查询或 SSE 获取状态。

## 7. 失败与恢复

| 失败点 | 记录 | 恢复策略 |
| --- | --- | --- |
| LLM 超时/配额 | AgentRun、Provider、重试次数 | 有界重试、Provider 降级或人工重试 |
| 结构化输出非法 | GenerationAttempt、校验错误 | 有限修复，超限后中断 |
| 指纹漂移 | Preparation/Commit 审计事件 | 拒绝 Commit，重新 Prepare |
| Outbox 投递失败 | Outbox 状态和错误 | 指数退避、幂等重试、告警 |
| SeaTunnel 失败 | ExecutionRun、引擎日志引用 | 取消/诊断/影子表清理/回滚 |
| 监督超限 | RuntimeSnapshot、决策 | 预警或 Kill Job，保留失败快照 |

## 8. 扩展实施规则

新增 Provider、连接器或执行引擎时必须提供：能力声明、配置 Schema、错误映射、健康检查、最小集成测试、权限范围说明和回滚行为。核心用例只依赖端口接口，不依赖具体实现名称。
