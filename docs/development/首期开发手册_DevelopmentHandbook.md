# ETL-Agent 首期开发手册

本文是首期实现的工作入口。需求细节以 `RequirementsDescription/整理版需求说明_RequirementsSpecification.md` 为准，技术取舍以 `docs/architecture/首期技术选型_InitialTechnicalSelection.md` 为准，VM 操作以 `docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md` 为准；逐步测试命令见 [项目测试手册](项目测试手册_ProjectTestingGuide.md)。

## 1. 开发目标

首期先完成一条可审计、可恢复、可回滚的最小闭环：

```text
连接登记 → 只读 Profile → 百炼生成候选
→ 确定性门禁 → 不可变版本 → Prepare
→ Checker 1 + Checker 2 → Operator Commit
→ Celery/SeaTunnel → 监督/质量分流 → 审计账本
```

不要先做“通用 Agent”或“所有连接器”。先让合成 MySQL → VM Doris 的真实学习链路遵守 Harness 协议，再扩展真实业务连接器和生产视图。

## 2. 推荐实施顺序

### 阶段 0：环境和契约

- 在 Ubuntu VM 启动 Compose 核心服务，验证健康检查、网络和数据卷。
- 确认百炼模型、API Base URL、超时和数据脱敏策略。
- 建立 `src/etl_agent`、`tests`、`migrations` 目录和 CI 基线。

### 阶段 1：控制面基础

M1.1/M1.2 已完成：配置加载、请求 ID、统一错误结构、`/health` 依赖探针、Identity/Project 基础模型、Alembic 迁移、本地 JWT、项目成员上下文和职责槽 API 已落地。启动本机 API 和执行迁移：

```bash
uv run alembic upgrade head
uv run uvicorn etl_agent.main:app --loop etl_agent.main:selector_event_loop_factory --host 127.0.0.1 --port 8000
```

访问 `GET /health` 可检查 PostgreSQL、Redis、MinIO、Vault、SeaTunnel 和 LLM 配置状态；本阶段不在健康检查中调用真实百炼接口。

- FastAPI `/health` 和统一错误响应。
- SQLAlchemy/Alembic、租户上下文、用户/项目/成员/角色槽。
- 结构化日志、请求 ID、配置加载和依赖就绪检查。

本地开发账号使用 `POST /api/v1/auth/register` 注册、`POST /api/v1/auth/login` 登录；注册接口仅在 `APP_ENV=development` 开放。访问项目资源时必须携带 `Authorization: Bearer <access_token>`，项目列表只返回当前用户的有效成员关系。创建项目会建立初始 Maker 和 Operator 槽，Checker 不得与 Maker/Operator 兼任。为便于学习，注册接口可同时提交已有 `project_code` 和 `project_role`（`checker_1`/`checker_2`），自动建立 Checker 成员关系；生产环境仍应改为管理员或企业身份系统分配。

### 阶段 2：连接与 Profile

- M2.1/M2.2/M2.3 已完成连接、数据库 Profile 和文件资产基础：`connections`、`metadata_profiles`、`file_assets` 模型和迁移、项目连接登记/查询 API、Vault KV v2 SecretProvider、MySQL/Doris 连接测试、只读 Schema/近似行数/脱敏样本、MinIO 上传和文件 Profile；连接响应只返回 `SecretRef`，不接受 `options.password` 等敏感字段。
- 连接配置只保存 Secret 引用，不保存密码明文。
- 连接测试和只读权限检查；当前适配器只允许 `SELECT 1`、information_schema 查询和限额样本查询。
- Schema、字段类型、近似统计和脱敏样本的稳定 JSON 契约。
- MinIO 文件资产元数据和上传大小限制。

调用 `PUT /api/v1/connections/{connection_id}` 可修正连接主机、端口、数据库和 SecretRef；调用 `POST /api/v1/connections/{connection_id}/tests` 会解析 Vault `SecretRef` 并执行 MySQL/Doris `SELECT 1`。调用 `POST /api/v1/connections/{connection_id}/profiles` 可传入 `table_names` 和 `sample_rows`，服务端只保存脱敏后的 Profile 快照；不支持的数据库类型会返回稳定错误，不会自动降级为写操作。

调用 `POST /api/v1/file-assets` 时使用 multipart 字段 `project_id` 和 `file`。服务端先流式计算大小与 SHA-256，再解析 CSV/JSON/XLSX/Parquet 的有限样本并脱敏，随后把原文件上传到 MinIO，只在 PostgreSQL 保存对象键、摘要和文件 Profile。默认上传上限由 `MAX_UPLOAD_SIZE_BYTES` 控制。

### 阶段 3：LangGraph 生成

- M3.1 已完成最小可验证切片：`GenerationRequest`、`EtlPlan`、`QualityContract`、`RuntimeBudget` 和 Profile 引用模型位于 `src/etl_agent/domain/generation.py`。
- LangGraph 节点已按 `IntentParseNode → ProfileEnrichmentNode → CandidateGenerationNode → SchemaValidationNode → HoconCompileNode → DeterministicGateNode → RepairNode` 编排；缺少 Profile 或增量字段时返回 `needs_clarification`，不调用 LLM。
- 百炼通过 OpenAI-compatible `LLMProvider` 适配器调用，具备超时、有限重试、JSON 解析、脱敏和 API Key 不落日志保护；`FakeLLMProvider` 用于离线测试。
- M3.2 增加 `LLM_MAX_PROMPT_BYTES` 发送前硬上限，以及默认关闭的 `LLM_REAL_SMOKE_ENABLED` / `CHECKPOINT_INTEGRATION_ENABLED` 集成测试开关。
- 候选必须通过 Pydantic/JSON Schema、Profile/字段引用、预算上限和 PyHOCON 编译校验；非法候选最多自动修复一次，超限返回 `validation_failed`，不能冻结版本。
- `POST /api/v1/pipelines` 创建 Pipeline，`POST /api/v1/pipelines/{pipeline_id}/versions` 创建草稿，`POST /api/v1/versions/{version_id}/generation` 运行生成；门禁通过才写入 SHA-256 摘要并将版本标记为 immutable。
- PostgreSQL Checkpoint 使用 `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`；API 每次生成使用配置的 `LANGGRAPH_CHECKPOINT_DATABASE_URL`，生产部署应确保同一 thread_id 复用同一数据库。
- Windows/PyCharm 入口 `src/etl_agent/main.py` 会切换到 `WindowsSelectorEventLoopPolicy`，因为 psycopg 异步连接不支持默认 Proactor loop；不要直接绕过 `etl_agent.main:app` 创建异步 Checkpoint。
- `POST /api/v1/agent-runs/{run_id}/answers` 会合并澄清答案，复用 AgentRun 的脱敏请求快照和原 `thread_id` 从 PostgreSQL Checkpoint 恢复；当前只允许更新澄清参数，不允许通过答案修改项目权限或资源预算。
- 当前限制：真实百炼调用的集成验收留待配置非生产 API Key 后执行；本地单元测试使用 fake Provider，不会发送业务数据。

### 阶段 4：Harness 协议

- M4.1 已完成 PDP v1 风险评级和审批槽分配；`POST /api/v1/versions/{version_id}/prepare` 只冻结通过门禁的不可变版本和 Profile 指纹，不产生外部副作用。
- M4.2 已完成独立审批槽和 `POST /api/v1/approval-requests/{approval_id}/decisions`；服务端拦截申请人自批、职责不匹配、过期和重复决定，全部 Checker 槽批准后 Preparation 才进入 `approved`。
- M4.3/M4.4 已完成 Ed25519 Capability 声明/签发/验签、Redis `SET NX EX` Replay Guard、Commit 指纹复核、ExecutionRun、Transactional Outbox 和 Evidence Ledger；`POST /api/v1/preparations/{preparation_id}/commit` 不返回 Capability 原文，重复提交按 Preparation/Idempotency-Key 返回已有执行事实。取消、清理、原子发布和回滚分别签发独立 Capability，不能复用提交令牌。
- Prepare 只冻结事实，Approve 只写决定，Commit 重新验指纹。
- Capability 绑定主体、工具、环境、制品摘要和过期时间；Replay Guard 必须是 Redis 原子消费。
- Tool Broker 是副作用唯一出口，Outbox 与 ExecutionRun 在一个 PostgreSQL 事务中落库；当前 Outbox 内部字段暂保存 Capability 原文，后续生产化切换 Vault/KMS 信封加密。
- Evidence Ledger 用前序哈希、载荷摘要和当前哈希形成项目级追加链，Commit 事件与执行事实同事务落库。

### 阶段 5：数据面和监督

- M5.1-M5.5 已完成 Celery 应用工厂、Outbox Tool Broker、Capability/Replay Guard 消费和 SeaTunnel Adapter 的提交/状态/取消端口；Worker 从 Outbox 读取冻结 PipelineVersion 的运行时 HOCON，不能接收未经 Commit 的直接命令，并通过 Doris 适配器执行影子表、清理、原子切换和回滚。
- `supervise_execution_run` 会把引擎状态和指标写入 `runtime_supervision_snapshots`，按冻结 RuntimeBudget 执行行数、字节、时长、放大比和拒绝率硬中断判断；终态按 QualityContract 写入 `execution_quality_results`，通过后自动生成 `execution.swap` Outbox，失败或质量拒绝后自动生成 `execution.cleanup` Outbox。
- `POST /api/v1/execution-runs/{id}/cancel`、`POST /api/v1/execution-runs/{id}/rollback` 和监督/质量查询接口已提供；动作均走 Outbox，重复请求保持幂等并写入 Evidence Ledger。
- 已在 VM 用 SeaTunnel 2.3.10 验证 Zeta REST：提交 `POST /submit-job?format=hocon`（`text/plain` HOCON）、状态 `GET /job-info/{job_id}`（`jobStatus`/`jobId`）和取消 `POST /stop-job`（JSON `jobId`）。`SeaTunnelAdapter` 将原生指标转换为 `input_records`、`output_records`、`input_bytes`、`output_bytes`、`rejected_records`、`elapsed_seconds`；路径通过 `SEATUNNEL_*_PATH` 配置，不应散落在业务用例中。Doris 适配器负责影子表准备、失败清理、原子 Swap 和回滚，SeaTunnel 本身不提供这些目标库动作。
- `scripts/seed_synthetic_mysql.py` 可向 Compose MySQL 写入确定性大批量演示数据；M5.5 已用该数据通过真实 MySQL → SeaTunnel → Doris 链路验收，输入/输出各 10,000 行并验证质量、原子发布和回滚。生产业务连接器、压测和高可用部署属于后续扩展。

### 阶段 6：前端和 Benchmark

- M6 已完成 Vue 3 + Vite + TypeScript 控制台首版，入口位于 `frontend/`，覆盖总览、连接/Profile、Pipeline Studio、四眼审批、运行中心和 Benchmark 六个视图。前端只展示脱敏结构、摘要、稳定错误码和运行状态，不保存或展示 Secret 原文。
- 后端新增项目级 Pipeline/Version、Preparation、ExecutionRun 列表查询，前端不需要绕过 API 读取数据库；所有写动作仍由 JWT、项目职责和 Harness 服务端校验。
- `POST /api/v1/benchmarks/run` 和 `scripts/run_benchmark.py` 提供离线 L0 基线、L1 故障注入 Benchmark。固定 `dataset_rows`、`seed`、`repeat`、`artifact_digest`、`policy_version` 和 `environment` 后，数据摘要、质量分流、Schema 覆盖率、P0 拦截率和吞吐指标可重复。
- 启动控制台：

  ```bash
  # 终端 1：控制面 API
  uv run uvicorn etl_agent.main:app --loop etl_agent.main:selector_event_loop_factory --host 127.0.0.1 --port 8000
  # 终端 2：前端控制台
  cd frontend
  npm install
  npm run dev
  ```

  Vite 默认在 `http://127.0.0.1:5173`，`/api` 和 `/health` 代理到本机 `8000`；也可用 `npm run build` 生成静态产物。运行 Benchmark CLI：

  ```bash
  uv run python scripts/run_benchmark.py --project-id <项目UUID> --level l1 --rows 10000 --seed 7
  ```

- M6/M6.1 前端、Benchmark 和历史摘要可离线验收；真实合成数据面需要 VM MySQL/Doris、Vault SecretRef 和 SeaTunnel，真实 LLM 生成还需要非生产百炼 API Key。Benchmark MinIO 报告存档、SSE/WebSocket 实时推送和企业 SSO 属于后续扩展。

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
| 数据面测试 | SeaTunnel 命令、取消、清理、质量分流、Swap、回滚和监督快照 | 测试源/目标库或仿真引擎 |
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
- 一条合成 MySQL → Doris 真实链路通过 Profile、生成、门禁、审批、Commit、SeaTunnel、监督、质量报告、原子 Swap 和回滚验收；不要求真实业务数据库。
- 关键安全规则有自动化测试：自批拦截、职责混用拦截、过期/重放 Capability 拦截、指纹变更拒绝、Outbox 幂等。
- Benchmark 结果可复现并关联制品版本、策略版本和运行环境。
