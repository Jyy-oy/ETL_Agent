# ETL-Agent 需求说明（整理版）

## 1. 文档边界

`主要需求_MainRequirements.md` 是本项目当前唯一的业务需求来源，描述了生产级 ETL-Agent 控制面平台的目标、模块、协议、数据表、接口、流程和验收标准。本文件将其整理为可执行的实现基线，并标注尚未由原文确定的技术选择。

本次用户请求属于工程准备工作，不是产品需求新增：

1. 读取并澄清原始需求，产出本整理版需求文档。
2. 根据需求补齐仓库忽略规则和环境变量模板。
3. 提供需要项目负责人确认的基础设施和运行参数。

本文件中的“建议”不应视为已经批准的产品范围；在实现前应完成第 10 节的确认项。

## 2. 产品目标与交付物

系统面向企业异构数据集成场景，提供从自然语言需求到受管 ETL 执行的完整控制面。控制面负责编排、审批、授权、审计和运行监督，Apache SeaTunnel 负责大规模数据搬运，控制面不得直接搬运海量业务数据。

首期交付物：

- Vue 前端控制台。
- FastAPI 控制面 API。
- Celery Worker/Beat 异步服务。
- PostgreSQL/Redis 初始化或迁移脚本。
- SeaTunnel Zeta 集成配置。
- Benchmark 数据集、自动化评测和安全进化记录。
- 接口说明、部署说明和端到端演示链路。

## 3. 功能范围

### 3.1 身份、租户与权限

- 用户认证鉴权、多租户项目和成员管理。
- 数据工程师（Maker）、数据审批人（Checker 1）、安全审批人（Checker 2）、系统操作员、审计人员五类职责。
- 申请人不得审批自己的申请；高风险操作的审批职责槽不得由同一人占用。
- 数据源凭据只能通过 SecretProvider 受管保存，业务表和日志不得出现敏感明文。

### 3.2 数据连接与元数据

- 数据库：MySQL、PostgreSQL、Oracle、Doris、ClickHouse。
- 文件/对象/API：CSV、Excel、JSON、Parquet、S3/MinIO、REST API。
- 连接测试、连接配置维护和项目级隔离。
- 只读元数据探查：Schema、字段类型、主键、近似统计、脱敏样本。
- 文件上传后保存对象引用并提取文件头、格式和字段推断结果。

### 3.3 Agent 生成与门禁

- 使用 LangGraph 编排“意图解析 → 元数据探查 → EtlPlan/HOCON 生成 → 确定性门禁 → 试运行 → 准备审批”状态机。
- 支持中断提问、人工回答和 PostgreSQL Checkpoint 恢复。
- 支持 OpenAI 兼容接口、DeepSeek、Qwen 等模型 Provider。
- 生成结构化 EtlPlan 和 SeaTunnel HOCON 候选，并进行语法、Schema 对齐和有限自动修复。
- 门禁通过后冻结不可变 PipelineVersion，并计算 SHA-256 制品摘要。

### 3.4 Harness 受管执行

- PDP 根据 ToolIntent、资源范围、环境和数据分级输出 P0-P3 风险及审批要求。
- 强制执行 `prepare → approve → commit` 三阶段协议。
- Commit 前重新核对输入指纹和审批事实。
- 使用 Ed25519 签发短时、绑定工具/主体/环境/制品指纹的单次 Capability 令牌；默认有效期 5 分钟，Replay Guard 保证只能消费一次。
- Tool Broker 是所有副作用调用的唯一出口。
- 通过同一 PostgreSQL 事务写入业务事实和 Transactional Outbox。
- Evidence Ledger 以哈希链和签名检查点记录关键事件，支持审计校验。

### 3.5 数据面、质量与运行监督

- Celery 消费受管命令，验签后物化 Secret 并调用 SeaTunnel。
- 有效数据写入影子表；不合格数据按 QualityContract 写入错误表并标记 ErrorCode。
- 达标后使用目标引擎原子 Swap 发布正式表；失败支持受管清理和回滚。
- RuntimeSupervisionContract 冻结最大读取行数、写入字节数、执行时长、输出放大比和拒绝率预算。
- 超限时按策略预警、硬中断或隔离告警，并生成可解释诊断报告。

### 3.6 评测与安全进化

- L0 静态注入、L1 模拟故障、L2 真实链路三层仿真。
- Benchmark 数据集和结果版本化，至少展示生成准确率、Schema 覆盖率、P0/P1 拦截率和延迟。
- Prompt/策略改进候选必须经过审查，并支持小流量或影子授权。

## 4. 页面与用户流程

前端至少包含总览工作台、数据连接与资产、Pipeline Studio、运行中心、安全治理与 Benchmark 五个模块。Pipeline Studio 应能查看对话/工作流步骤、EtlPlan、HOCON、DAG、字段 Diff、质量规则和版本审批入口；运行中心应能查看运行指标、实时日志、质量分流、诊断、取消、重跑和回滚。

端到端主流程：连接配置 → 只读 Profile → 自然语言需求 → LangGraph 澄清/恢复 → 生成并门禁校验 → 冻结版本 → Prepare → 两名独立 Checker 审批 → Operator Commit → Celery/SeaTunnel 执行 → 监督和质量分流 → 原子 Swap → 审计账本。

失败流程：执行异常或预算超限 → 保存失败快照 → 生成诊断建议 → Operator 发起受管回滚 → 清理影子表并恢复原状态。

## 5. 状态与一致性约束

系统必须区分三层状态：

- Conversation State：自然语言交互历史。
- Workflow State：LangGraph 节点、澄清、生成、门禁和 Checkpoint。
- Execution State：ExecutionRun、SeaTunnel 作业 ID 和运行指标。

Prepare 不产生外部副作用；Approve 只针对冻结事实；Commit 必须在一次 PostgreSQL 事务中完成指纹复核、Capability 签发、ExecutionRun 创建和 Outbox 投递。多 Worker/多实例部署时，任务注册、Replay Guard 和缓存状态不得只存于进程内存。

## 6. 核心数据实体

必须覆盖原文定义的 `users`、`projects`、`project_memberships`、`project_role_grants`、`pipelines`、`pipeline_versions`、`pipeline_artifacts`、`agent_runs`、`approval_requests`、`execution_runs`、`file_assets`、`runtime_supervision_snapshots`、`audit_events` 等实体。不可变版本至少保存 EtlPlan、HOCON、制品类型/摘要、创建时间和不可变标记；审计事件至少保存前序哈希、当前哈希、载荷摘要、操作者和资源定位。

## 7. API 基线

至少实现原文列出的 `/health`、连接与 Profile、文件资产、Pipeline/版本生成、Agent 回答、设计查询、Preparation、审批决策、Commit、ExecutionRun 查询/取消/回滚和 Benchmark 触发接口。所有写接口应执行租户隔离、角色校验、幂等性策略和审计记录；错误响应应包含稳定错误码和可关联日志的请求标识。

## 8. 非功能要求

- 安全：最小权限、凭据不落日志、只读探查、脱敏样本、签名验证、防重放、职责分离和不可篡改审计。
- 可靠性：Checkpoint 可恢复、Outbox 可重试且幂等、任务取消/回滚有明确状态、依赖健康检查可观测。
- 可观测性：结构化日志、运行指标、质量指标、审计事件和 Benchmark 结果可查询。
- 可测试性：Harness 协议、权限边界、门禁、质量分流、超预算中断、回滚和三层 Benchmark 均需自动化测试。
- 可部署性：本地开发可用 Docker Compose 或等价服务；生产环境支持控制面和数据面分离、密钥外置和多实例 Worker。

## 9. 建议的首期实现基线

以下是便于启动开发的建议默认值，不替代技术评审：Python 3.12 + uv、FastAPI、Vue 3 + Vite + TypeScript、PostgreSQL 16、Redis 7、MinIO（S3 API）、HashiCorp Vault KV v2、Celery 5、LangGraph、Apache SeaTunnel Zeta。PostgreSQL 同时承载业务数据和 LangGraph Checkpoint；Redis 用于 Celery Broker、结果后端和短时 Replay Guard；MinIO 保存上传文件和制品大对象；Vault 保存连接凭据；SeaTunnel 独立运行。

本地环境可以先使用 Docker Compose 启动 PostgreSQL/Redis/MinIO/Vault/SeaTunnel，模型 Provider 使用一个 OpenAI 兼容端点。生产环境必须替换默认账号、密钥、JWT 密钥和 Ed25519 密钥，并将这些值注入 Secret Manager 或部署平台。

## 10. 需要确认的技术环境

请在开始实现前确认下表。未确认项按“建议默认值”实现会影响部署、依赖和接口契约。

| 类别 | 建议默认值 | 需要确认 |
| --- | --- | --- |
| 部署方式 | 本地 Docker Compose，生产 Kubernetes | 是否需要从第一版支持 K8s/Helm？ |
| 结构化数据库 | PostgreSQL 16 | 版本、托管方式、是否允许单库承载 Checkpoint？ |
| 缓存/队列 | Redis 7，Broker/结果/Replay Guard 分库 | 是否使用 Redis Sentinel/Cluster，是否已有共享 Redis？ |
| 对象存储 | MinIO S3 API | endpoint、bucket、region、是否生产改用云 S3？ |
| SecretProvider | Vault KV v2 | Vault 地址、认证方式、namespace、mount；本地是否允许 dev server？ |
| 数据面引擎 | SeaTunnel Zeta 独立服务 | 版本、部署地址、认证方式、作业提交协议和日志采集方式？ |
| 源/目标连接 | 首条演示链路 MySQL → Doris | 首期是否必须同时验收 PostgreSQL/Oracle/ClickHouse/文件/API？ |
| LLM | 一个 OpenAI 兼容 Provider | 首选 Provider、模型、API Base URL、超时/重试和数据出境限制？ |
| 身份认证 | JWT（短时 access token） | 是否接入企业 OIDC/LDAP/SSO，还是先做本地账号？ |
| 密钥算法 | Ed25519 PEM 文件或 Vault Transit | 密钥由谁生成、轮转周期、是否使用 HSM/KMS？ |
| 前端实时性 | 轮询，后续可接 SSE/WebSocket | 首版是否要求实时日志和指标推送？ |
| 运行预算 | Capability TTL 300 秒 | 默认行数、字节、时长、放大比、拒绝率阈值分别是多少？ |
| Benchmark | L0/L1 本地，L2 可选 | 数据集位置、脱敏要求、目标准确率和拦截率基线？ |

## 11. 验收映射

验收必须覆盖：职责分离和自批拦截、异构连接和脱敏 Profile、Agent 中断/恢复、EtlPlan/HOCON 门禁和 SHA-256 不可变版本、Prepare/Approve/Commit 与单次 Capability、Celery/SeaTunnel 搬运、影子表/错误表/原子 Swap、监督超限和回滚、Benchmark 报告。每项验收应能关联到 API、审计事件和自动化测试证据。

## 12. 配置文件约定

- `.env.example` 是可提交的变量名和本地开发占位值模板。
- `.env` 只供本机使用，已加入 `.gitignore`，不得提交真实凭据。
- 变量定义和建议值见仓库根目录 `.env.example`；确认第 10 节后再替换为具体环境参数。

## 13. 当前开发环境事实

以下信息来自项目负责人，属于当前开发约束而不是生产部署承诺：

- 开发虚拟机使用 Ubuntu，地址为 `192.168.181.128`。
- 截至 2026-08-25，VM 已通过 Docker Compose 启动 PostgreSQL 16、Redis、MinIO、Vault 和 SeaTunnel 2.3.10，核心基础设施均处于运行状态；SeaTunnel 使用不传 `-r` 的单节点默认 `MASTER_AND_WORKER` 角色，Windows 到 VM 的 TCP/协议级依赖检查已通过。
- LLM 调用远端百炼平台，不在本地或服务器部署模型。
- 首期 Compose 仅负责基础设施；FastAPI、Celery、Vue 应用服务需在源码实现后再加入。
- 本项目为学习和工程演练项目，M4/M5 首期使用合成 MySQL 数据、SeaTunnel FakeSource 和 Mock Doris 目标动作完成验收，不要求真实业务 MySQL/Doris；真实连接器和生产链路作为后续可选扩展。

部署和开发操作见：

- `docs/architecture/首期技术选型_InitialTechnicalSelection.md`
- `docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`
- `docs/development/开发环境与依赖_DevelopmentEnvironment.md`
- `docs/development/首期开发手册_DevelopmentHandbook.md`
