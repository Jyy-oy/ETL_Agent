# ETL-Agent 数据模型与数据库设计

状态：MVP 数据模型基线

## 1. 数据分层

| 数据层 | 代表实体 | 存储 | 说明 |
| --- | --- | --- | --- |
| 控制面事实 | users、projects、pipelines、approvals、runs | PostgreSQL | 事务性、可审计、可查询 |
| 工作流状态 | agent_runs、checkpoint、messages | PostgreSQL | LangGraph 跨请求恢复 |
| 大对象 | HOCON、文件、Benchmark、日志归档 | MinIO/S3 | 数据库保存 URI、摘要和元数据 |
| 短时状态 | Capability 消费、队列、缓存 | Redis | 不作为业务事实唯一来源 |
| Secret | 数据源凭据、运行时 Token | Vault/KMS | 业务表只保存 SecretRef |

## 2. 核心表目录

| 表 | 责任 | 关键约束 |
| --- | --- | --- |
| `users` | 用户身份和状态 | `username` 唯一 |
| `projects` | 租户项目边界 | `code` 在租户内唯一 |
| `project_memberships` | 用户项目成员关系 | `(project_id,user_id)` 唯一 |
| `project_role_grants` | Maker/Checker/Operator/Auditor 槽 | 互斥槽和职责分离 |
| `connections` | 非敏感连接参数和 SecretRef | 项目隔离、Secret 不落库 |
| `metadata_profiles` | 只读 Schema/统计/脱敏摘要 | 绑定连接版本和数据摘要 |
| `file_assets` | 文件对象引用和格式 Schema | URI、大小、摘要 |
| `pipelines` | Pipeline 逻辑定义 | `(project_id,code)` 唯一 |
| `pipeline_versions` | 不可变 EtlPlan/HOCON 版本 | digest 唯一、禁止更新 |
| `pipeline_artifacts` | 大对象索引和制品类型 | `(version_id,type)` 唯一 |
| `agent_runs` | LangGraph 执行和生成证据 | thread、provider、prompt 版本 |
| `approval_requests` | 独立审批槽和决策 | 一个请求一个角色槽 |
| `preparations` | 冻结事实、风险、预算和回滚 | 指纹和版本不可漂移 |
| `execution_runs` | SeaTunnel 作业和运行指标 | 由 Commit 事务创建 |
| `runtime_supervision_snapshots` | 运行时监督快照 | 按运行和时间索引 |
| `outbox_events` | 可靠投递命令 | event_id 幂等 |
| `evidence_ledger_events` | 项目级追加式哈希链审计证据 | `project_id + sequence_number` 唯一，`prev_event_hash`/`event_hash` |
| `audit_events` | 面向查询的业务审计索引 | 后续阶段从账本事件投影 |

M2.1 已落地 `connections` 和 `metadata_profiles`：连接表只保存 host/port/database/username、非敏感 options 和 Vault `secret_ref`；Profile 表保存版本、指纹、Schema 快照、脱敏样本和近似行数。Profile 通过 `(connection_id, fingerprint)` 唯一约束保证同一快照可复用。

M1.2 为 `users` 增加可轮换的 `password_hash` 字段，仅用于本地 development 登录；API 永不返回该字段，企业环境应通过 OIDC/SSO 替换本地注册。

M2.3 的 `file_assets` 保存项目归属、上传用户、MinIO bucket/object key、原始文件名、内容类型、格式、大小、SHA-256 和脱敏 `schema_json`；原始文件不进入 PostgreSQL。上传限制由配置 `MAX_UPLOAD_SIZE_BYTES` 控制，数据库提交失败时执行对象删除补偿。

M3.1 已落地 `pipelines`、`pipeline_versions`、`agent_runs` 和 `generation_attempts`（迁移 `0005_agent_generation`、`0006_agent_run_request`）。草稿版本允许生成写入；门禁通过后才写入规范化 EtlPlan、HOCON、`artifact_digest` 并设置 `immutable=true`。AgentRun 保存 thread/provider/model、Prompt 摘要、脱敏请求快照、节点轨迹、修复次数和错误码；每次候选或修复只保存输出摘要与校验错误，不保存完整 Prompt、API Key 或未脱敏样本。LangGraph Checkpoint 由 PostgreSQL 独立表承载，不能以进程内存替代。

M4.1 已落地 `preparations`（迁移 `0007_preparations`）。Prepare 只接受 `immutable=true` 且 `ready` 的 PipelineVersion，重新读取项目内 Profile 指纹，由 PDP v1 计算风险级别和所需 Checker 槽，并保存输入指纹、资源范围、预算、策略版本、脱敏事实和过期时间；该阶段不触发任何外部写操作。

M4.2 已落地 `approval_requests`（迁移 `0008_approval_requests`）。Prepare 按 PDP 返回的 Checker 槽创建唯一审批请求；Approve 以行锁保护单槽决策和 Preparation 状态汇聚，拒绝申请人自批、无职责用户、过期 Preparation 和重复决策。所有槽批准后才进入 `approved`，任一槽拒绝则进入 `rejected`。

M4.4 已落地 `execution_runs`、`outbox_events` 和 `evidence_ledger_events`（迁移 `0009_execution_outbox_ledger`）。Commit 重新读取版本/Profile 指纹并校验审批，在同一事务中写入排队状态的 ExecutionRun、`execution.submit` Outbox 命令和账本事件；ExecutionRun 只保存 Capability 摘要，Outbox 的内部 Capability 原文暂为 MVP 实现，生产阶段改用 Vault/KMS 信封加密。Preparation 与 ExecutionRun、Outbox 使用唯一约束支持重复 Commit 幂等。

## 3. 关系约束

- 所有项目资源必须可通过 `project_id` 追溯，查询默认带租户/项目过滤。
- `pipeline_versions` → `preparations` → `approval_requests` → `execution_runs` 是单向生命周期。
- `execution_runs` 不允许反向修改 `pipeline_versions` 或已完成审批。
- `approval_requests.approver_id` 不能等于 Preparation Maker，且高风险槽不能由同一人占用。
- `execution_runs.capability_token_digest` 只保存摘要，不保存 Capability 原文。
- `evidence_ledger_events` 只能追加，不提供普通更新/删除 API；账本事件使用前序哈希和当前哈希校验完整性。

## 4. 索引与并发

首期至少建立：项目资源组合索引、PipelineVersion digest 唯一索引、AgentRun thread/status 索引、ApprovalRequest role/status 索引、ExecutionRun status/created_at 索引、Outbox status/next_attempt_at 索引和 AuditEvent project/created_at 索引。

状态迁移使用乐观锁或版本号；Commit、审批、Outbox 消费和 Replay Guard 必须具备唯一约束或原子条件更新，不能依赖应用进程内锁。

## 5. 迁移和保留

- 使用 Alembic 管理 schema；迁移必须可升级、可验证，破坏性迁移分阶段执行。
- 每次发布记录迁移版本、执行时间和回滚限制。
- 运行指标、Agent 证据和审计事件按环境/合规要求设置保留期；审计记录不得因普通业务清理被删除。
- MinIO 大对象删除必须先确认数据库引用已解除，并留下删除审计事件。
- 备份包含 PostgreSQL、MinIO bucket 清单和 Vault/KMS 恢复材料；Redis 只作为可重建短时状态，除非明确纳入队列恢复方案。

## 6. ER 图源文件

ER 图源文件位于 `docs/architecture/diagrams/ETLAgent逻辑ER图_ETLAgentER.puml`。它表达逻辑关系，不替代实际 Alembic migration；实现时应为新增字段、索引和约束补充迁移测试。
