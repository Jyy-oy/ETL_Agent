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

M1.1/M1.2 已完成：配置加载、请求 ID、统一错误结构、`/health` 依赖探针、Identity/Project 基础模型、Alembic 迁移、本地 JWT、项目成员上下文和职责槽 API 已落地。启动本机 API 和执行迁移：

```bash
uv run alembic upgrade head
uv run uvicorn etl_agent.main:app --host 127.0.0.1 --port 8000
```

访问 `GET /health` 可检查 PostgreSQL、Redis、MinIO、Vault、SeaTunnel 和 LLM 配置状态；本阶段不在健康检查中调用真实百炼接口。

- FastAPI `/health` 和统一错误响应。
- SQLAlchemy/Alembic、租户上下文、用户/项目/成员/角色槽。
- 结构化日志、请求 ID、配置加载和依赖就绪检查。

本地开发账号使用 `POST /api/v1/auth/register` 注册、`POST /api/v1/auth/login` 登录；注册接口仅在 `APP_ENV=development` 开放。访问项目资源时必须携带 `Authorization: Bearer <access_token>`，项目列表只返回当前用户的有效成员关系。创建项目会建立初始 Maker 和 Operator 槽，Checker 不得与 Maker/Operator 兼任。

### 阶段 2：连接与 Profile

- M2.1/M2.2/M2.3 已完成连接、数据库 Profile 和文件资产基础：`connections`、`metadata_profiles`、`file_assets` 模型和迁移、项目连接登记/查询 API、Vault KV v2 SecretProvider、MySQL/Doris 连接测试、只读 Schema/近似行数/脱敏样本、MinIO 上传和文件 Profile；连接响应只返回 `SecretRef`，不接受 `options.password` 等敏感字段。
- 连接配置只保存 Secret 引用，不保存密码明文。
- 连接测试和只读权限检查；当前适配器只允许 `SELECT 1`、information_schema 查询和限额样本查询。
- Schema、字段类型、近似统计和脱敏样本的稳定 JSON 契约。
- MinIO 文件资产元数据和上传大小限制。

调用 `POST /api/v1/connections/{connection_id}/tests` 会解析 Vault `SecretRef` 并执行 MySQL/Doris `SELECT 1`。调用 `POST /api/v1/connections/{connection_id}/profiles` 可传入 `table_names` 和 `sample_rows`，服务端只保存脱敏后的 Profile 快照；不支持的数据库类型会返回稳定错误，不会自动降级为写操作。

调用 `POST /api/v1/file-assets` 时使用 multipart 字段 `project_id` 和 `file`。服务端先流式计算大小与 SHA-256，再解析 CSV/JSON/XLSX/Parquet 的有限样本并脱敏，随后把原文件上传到 MinIO，只在 PostgreSQL 保存对象键、摘要和文件 Profile。默认上传上限由 `MAX_UPLOAD_SIZE_BYTES` 控制。

### 阶段 3：LangGraph 生成

- M3.1 已完成最小可验证切片：`GenerationRequest`、`EtlPlan`、`QualityContract`、`RuntimeBudget` 和 Profile 引用模型位于 `src/etl_agent/domain/generation.py`。
- LangGraph 节点已按 `IntentParseNode → ProfileEnrichmentNode → CandidateGenerationNode → SchemaValidationNode → HoconCompileNode → DeterministicGateNode → RepairNode` 编排；缺少 Profile 或增量字段时返回 `needs_clarification`，不调用 LLM。
- 百炼通过 OpenAI-compatible `LLMProvider` 适配器调用，具备超时、有限重试、JSON 解析、脱敏和 API Key 不落日志保护；`FakeLLMProvider` 用于离线测试。
- 候选必须通过 Pydantic/JSON Schema、Profile/字段引用、预算上限和 PyHOCON 编译校验；非法候选最多自动修复一次，超限返回 `validation_failed`，不能冻结版本。
- `POST /api/v1/pipelines` 创建 Pipeline，`POST /api/v1/pipelines/{pipeline_id}/versions` 创建草稿，`POST /api/v1/versions/{version_id}/generation` 运行生成；门禁通过才写入 SHA-256 摘要并将版本标记为 immutable。
- PostgreSQL Checkpoint 使用 `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`；API 每次生成使用配置的 `LANGGRAPH_CHECKPOINT_DATABASE_URL`，生产部署应确保同一 thread_id 复用同一数据库。
- Windows/PyCharm 入口 `src/etl_agent/main.py` 会切换到 `WindowsSelectorEventLoopPolicy`，因为 psycopg 异步连接不支持默认 Proactor loop；不要直接绕过 `etl_agent.main:app` 创建异步 Checkpoint。
- `POST /api/v1/agent-runs/{run_id}/answers` 会合并澄清答案，复用 AgentRun 的脱敏请求快照和原 `thread_id` 从 PostgreSQL Checkpoint 恢复；当前只允许更新澄清参数，不允许通过答案修改项目权限或资源预算。
- 当前限制：真实百炼调用的集成验收留待配置非生产 API Key 后执行；本地单元测试使用 fake Provider，不会发送业务数据。

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
