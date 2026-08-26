# ETL-Agent 学习资料与工程沉淀变更记录

## 2026-08-26：M5.1 Celery/Outbox/SeaTunnel Adapter 边界完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 5 数据面闭环
- 根因与解决过程：新增 Celery 应用工厂、Outbox Tool Broker、Capability 验签与 Redis Replay Guard 消费、SeaTunnel Zeta HTTP Adapter，以及提交/状态/取消端口。Worker 只从待投递 Outbox 读取冻结 PipelineVersion 的 HOCON，不接收 API 用户直接命令；成功提交后更新 ExecutionRun 为 `running`，失败记录稳定错误并标记 Outbox/ExecutionRun 失败。
- 未完成：SeaTunnel 2.3.10 实际 Zeta API 联调、带引擎幂等键的安全重试、影子表/错误表、QualityContract、运行监督、原子 Swap、取消回滚和合成数据初始化留待 M5.2/M5.3；当前不对已消费 Capability 自动重试。
- 验证证据：42 项测试通过、2 项外部集成测试默认跳过；Ruff、Mypy 通过。SeaTunnel Adapter 使用 MockTransport 覆盖提交、状态和取消映射。
- 涉及文件：`src/etl_agent/workers/celery_app.py`、`src/etl_agent/workers/tasks.py`、`src/etl_agent/workers/dispatcher.py`、`src/etl_agent/workers/engine.py`、`tests/unit/test_m5_engine.py`

## 2026-08-26：M4.4 Commit、ExecutionRun、Transactional Outbox 与 Evidence Ledger 完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 4 Harness 与审批
- 根因与解决过程：新增 `POST /api/v1/preparations/{preparation_id}/commit` 和 `GET /api/v1/execution-runs/{execution_id}`；Commit 在 Preparation 行锁下重新计算版本/Profile 指纹，校验审批槽，签发绑定主体、环境、Preparation 和制品摘要的 Ed25519 Capability，并在同一 PostgreSQL 事务中创建 `execution_runs`、`outbox_events` 和追加式 `evidence_ledger_events`。重复提交按 Preparation/Idempotency-Key 返回已有执行事实，响应不包含 Capability 原文。
- 安全与限制：Outbox 当前保存内部 Worker 所需的 Capability 原文，未通过 API、日志或执行查询暴露；生产阶段应改为 Vault/KMS 信封加密。Evidence Ledger 使用前序哈希、载荷摘要和当前哈希形成项目级追加链，并保留并发锁定点。
- 验证证据：39 项测试通过、2 项外部集成测试默认跳过；Ruff、Mypy 通过；Alembic 已将 VM PostgreSQL 升级到 `0009_execution_outbox_ledger`。
- 涉及文件：`src/etl_agent/api/preparations.py`、`src/etl_agent/api/preparation_models.py`、`src/etl_agent/harness/ledger.py`、`src/etl_agent/harness/models.py`、`src/etl_agent/infrastructure/models.py`、`migrations/versions/0009_execution_outbox_ledger.py`、`tests/unit/test_m4_commit.py`

## 2026-08-26：新增 MySQL/Doris 合成数据面 Compose Profile

- 级别：开发环境/部署准备
- 类型：配置增强
- 来源：M5 数据面联调前置
- 根因与解决过程：确认 SeaTunnel 2.3.10 Doris Connector 支持 Doris `>=1.1.x`；选择 `mysql:8.0.36`、`apache/doris:fe-2.1.11` 和 `apache/doris:be-2.1.11` 作为 amd64 开发基线。原 Compose 新增 `source-target` profile、MySQL 持久化卷、Doris FE/BE 静态网络和 SeaTunnel 到 Doris 网络的连接；默认基础设施启动行为不变。
- 注意事项：Doris FE/BE 必须同版本；单机仍需两个容器；启动前检查 VM 资源和 `172.30.0.0/24` 网段冲突。VM 已完成镜像拉取并启动三项服务，SeaTunnel 端到端作业仍未执行。
- 验证证据：Docker Hub 标签存在且 amd64 镜像可用；Compose YAML 通过 Python YAML 解析。Windows 本机未安装 Docker CLI，Compose 展开和实际启动需在 VM 验证。
- 涉及文件：`docker-compose.yml`、`.env.example`、`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`、`docs/architecture/首期技术选型_InitialTechnicalSelection.md`

## 2026-08-26：M4.3 Capability 与 Replay Guard 基础完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 4 Harness 与审批
- 根因与解决过程：新增 Ed25519 Capability v1，令牌绑定主体、工具、环境、Preparation、制品摘要和有效期；新增 Redis Replay Guard，按令牌 SHA-256 摘要使用 `SET NX EX` 原子消费。Capability 当前只提供领域/基础设施端口，尚未由 Commit/API 单独签发。
- 未完成：Commit 指纹复核、事务性 ExecutionRun/Outbox 和 Evidence Ledger 留待后续 M4 切片。
- 验证证据：36 项单元/API 测试通过、2 项外部集成测试默认跳过；Ruff、Mypy、Alembic check、锁文件检查通过；VM PostgreSQL 当前为 `0008_approval_requests`。
- 涉及文件：`src/etl_agent/harness/capability.py`、`src/etl_agent/config.py`、`tests/unit/test_m4_capability.py`

## 2026-08-26：M4.2 独立审批槽与 Checker 决策完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 4 Harness 与审批
- 根因与解决过程：新增 `approval_requests` 表和 `POST /api/v1/approval-requests/{approval_id}/decisions`；Prepare 按 PDP 结果创建唯一 Checker 槽，Approve 使用 Preparation/审批行锁，校验当前用户职责、申请人自批、过期和重复决策，全部槽批准后才将 Preparation 置为 `approved`，任一拒绝则置为 `rejected`。
- 未完成：Commit 指纹复核、Ed25519 Capability、Redis Replay Guard、Transactional Outbox、Evidence Ledger 留待后续 M4 切片。
- 验证证据：32 项单元/API 测试通过、2 项外部集成测试默认跳过；Ruff、Mypy、Alembic check 通过；VM PostgreSQL 已升级到 `0008_approval_requests`。
- 涉及文件：`src/etl_agent/api/preparations.py`、`src/etl_agent/api/preparation_models.py`、`src/etl_agent/harness/models.py`、`src/etl_agent/infrastructure/models.py`、`migrations/versions/0008_approval_requests.py`

## 2026-08-26：M4.1 PDP 与 Prepare 基础切片完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 4 Harness 与审批
- 根因与解决过程：新增确定性 PDP v1，根据环境、目标写入、数据分级和运行预算计算 P0-P3 风险及 Checker 审批槽；新增 `preparations` 表和 `POST /api/v1/versions/{version_id}/prepare`，只允许对已通过门禁的不可变 PipelineVersion 冻结 Profile 指纹、资源范围、预算、策略版本和有效期，不执行源库、目标库或 SeaTunnel 副作用。
- 未完成：Approve 决策、Ed25519 Capability、Redis Replay Guard、Transactional Outbox、Evidence Ledger 留待 M4.2/M4.3。
- 验证证据：32 项单元/API 测试通过、2 项外部集成测试默认跳过；Ruff、Mypy、Alembic check 通过；VM PostgreSQL 已升级到 `0007_preparations`。
- 涉及文件：`src/etl_agent/harness/`、`src/etl_agent/api/preparations.py`、`src/etl_agent/api/preparation_models.py`、`src/etl_agent/infrastructure/models.py`、`migrations/versions/0007_preparations.py`、`tests/unit/test_m4_harness.py`

## 2026-08-26：M3.2 Provider 边界与集成测试入口完成

- 级别：开发实现
- 类型：增强
- 来源：阶段 3 M3.2
- 根因与解决过程：增加 `LLM_MAX_PROMPT_BYTES` Prompt 字节上限，超限请求在网络调用前拒绝；增加 `LLM_REAL_SMOKE_ENABLED` 和 `CHECKPOINT_INTEGRATION_ENABLED` 显式测试开关；补充 Provider 瞬态错误重试、超限拒绝和显式集成测试。VM PostgreSQL Checkpoint 自动化测试已通过，真实百炼测试默认关闭。
- 未完成：真实百炼非生产调用验收、Prepare/Approve/Commit 和 SeaTunnel 执行留待后续阶段。
- 验证证据：全量 pytest 29 项通过、2 项集成测试默认跳过；Ruff、Mypy 通过；Checkpoint 集成测试显式开启后通过。
- 涉及文件：`src/etl_agent/config.py`、`src/etl_agent/infrastructure/llm.py`、`.env.example`、`tests/unit/test_m3_generation.py`、`tests/integration/test_m3_runtime.py`

## 2026-08-25：M3.1 Agent 结构化生成切片完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 3 LangGraph 生成
- 根因与解决过程：新增严格 EtlPlan/RuntimeBudget/Profile 引用模型、OpenAI-compatible 远端 Provider 与 fake Provider；实现 LangGraph 意图解析、缺参中断、结构化校验、PyHOCON 编译、Profile/预算确定性门禁和一次有限修复。新增 `pipelines`、`pipeline_versions`、`agent_runs`、`generation_attempts` 及迁移 `0005_agent_generation`、`0006_agent_run_request`；生成 API 只有在门禁通过后才计算 SHA-256 并冻结不可变版本；AgentRun 保存脱敏请求快照，答案 API 复用原 thread 从 Checkpoint 恢复。
- 未完成：真实百炼调用、Prepare/Approve/Commit 和 SeaTunnel 执行留待 M3.2/M4。
- 验证证据：阶段 3 单元测试 7 项通过；全量 pytest 27 项通过；Ruff、Mypy、Alembic check 通过。VM PostgreSQL 已升级到 `0006_agent_run_request`，并通过真实 Checkpoint setup 与 fake Provider 生成恢复验证。
- 涉及文件：`src/etl_agent/domain/generation.py`、`src/etl_agent/infrastructure/llm.py`、`src/etl_agent/workflows/`、`src/etl_agent/api/generation.py`、`migrations/versions/0005_agent_generation.py`、`migrations/versions/0006_agent_run_request.py`、`tests/unit/test_m3_generation.py`

## 2026-08-25：Windows 异步 Checkpoint 运行时修正

- 级别：开发环境/修复
- 类型：配置修正
- 来源：首次使用 VM PostgreSQL 初始化 LangGraph Checkpoint
- 根因与解决过程：Windows 默认 Proactor event loop 不被 psycopg 异步连接支持；在 `src/etl_agent/main.py` 入口设置 `WindowsSelectorEventLoopPolicy`，确保 PyCharm/Uvicorn 启动控制面时 `AsyncPostgresSaver` 可正常建立连接。
- 验证证据：通过入口导入后在 VM PostgreSQL 完成 Checkpoint setup，并用 fake Provider 完成带 `thread_id` 的 LangGraph 生成。

## 2026-08-25：M2.3 MinIO 文件资产与文件 Profile 完成

- 级别：开发实现
- 类型：新增
- 来源：完成 M2 连接与 Profile 剩余工作
- 根因与解决过程：新增 `file_assets` 表和 MinIO S3 对象存储适配器；上传前流式计算大小/SHA-256，限制 CSV、JSON、XLSX、Parquet 格式，生成字段类型和有限脱敏样本后上传原文件；数据库仅保存对象引用、摘要和 Profile，提交失败执行对象删除补偿。
- 未完成：真实 MinIO 上传和业务文件集成验收需配置 VM bucket 与认证；Worker 异步大文件处理留待 M5。
- 验证证据：CSV/JSON Profile、敏感字段脱敏、大小/格式拒绝和 API 路由测试通过；Alembic 已升级至 `0004_file_assets`。
- 涉及文件：`src/etl_agent/infrastructure/object_store.py`、`src/etl_agent/infrastructure/file_profiling.py`、`src/etl_agent/api/file_assets.py`、`migrations/versions/0004_file_assets.py`、`tests/unit/test_file_assets.py`

## 2026-08-25：M2.2 SecretProvider、连接测试与只读 Profile 完成

- 级别：开发实现
- 类型：新增
- 来源：按阶段顺序补齐连接与 Profile 核心能力
- 根因与解决过程：新增 Vault KV v2 `SecretProvider`，实现 SecretRef 路径规范化和错误脱敏；新增 MySQL/Doris 兼容连接测试和 `SELECT 1` 只读探针；实现 information_schema Schema、近似行数、限额样本、字段脱敏和 SHA-256 Profile 指纹；连接测试与 Profile API 增加项目成员校验。
- 未完成：MinIO 文件资产、上传大小限制和文件 Profile；PostgreSQL/Oracle/ClickHouse 适配器留待连接器扩展。
- 验证证据：SecretRef、连接探针和 Profile 单元测试通过；真实业务库探查需配置对应 Vault SecretRef 后执行集成测试。
- 涉及文件：`src/etl_agent/infrastructure/secrets.py`、`src/etl_agent/infrastructure/connection_testing.py`、`src/etl_agent/infrastructure/profiling.py`、`src/etl_agent/api/connections.py`、`tests/unit/test_m2_2.py`

## 2026-08-25：M1.2 本地认证与项目职责上下文完成

- 级别：开发实现
- 类型：新增
- 来源：按里程碑顺序补齐 M1 控制面基础
- 根因与解决过程：新增 scrypt 密码哈希、JWT 访问令牌、认证依赖、development 本地注册/登录、项目查询与创建、成员管理和职责槽冲突校验；新增用户密码哈希迁移 `0003_user_password_hash`。项目资源查询依赖当前用户成员关系，Checker 不得与 Maker/Operator 兼任。
- 未完成：企业 OIDC/LDAP/SSO、细粒度审计和完整结构化日志接入。
- 涉及文件：`src/etl_agent/infrastructure/security.py`、`src/etl_agent/api/auth.py`、`src/etl_agent/api/auth_dependencies.py`、`src/etl_agent/api/projects.py`、`migrations/versions/0003_user_password_hash.py`、`tests/unit/test_security.py`

## 2026-08-25：M2.1 连接登记与 Profile 契约基础完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 2 连接与 Profile 的首个可验证切片
- 根因与解决过程：新增项目级 `connections` 和 `metadata_profiles` ORM 模型及 Alembic 迁移；实现连接登记、项目连接查询和最近 Profile 查询 API；Pydantic 请求模型拒绝密码、Token、API Key 等敏感扩展字段，业务表仅保留 `secret_ref`。
- 未完成：Vault SecretProvider、真实连接测试、MySQL/Doris 只读探查、脱敏样本生成和 MinIO 文件资产。
- 涉及文件：`src/etl_agent/api/connection_models.py`、`src/etl_agent/api/connections.py`、`src/etl_agent/infrastructure/database.py`、`src/etl_agent/infrastructure/models.py`、`migrations/versions/0002_connections_profiles.py`、`tests/unit/test_connections.py`

## 2026-08-25：M1.1 控制面基础实现完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 1 控制面基础的首批开发任务
- 根因与解决过程：新增 Pydantic Settings 配置加载、FastAPI `/health` 和 `/api/v1/health`、请求 ID、统一错误响应、PostgreSQL/Redis/MinIO/Vault/SeaTunnel/LLM 探针、Identity/Project 基础模型和 Alembic 初始迁移；VM 数据库已升级到 `0001_identity_project`，实时健康检查返回 200。
- 验证证据：5 个 pytest 通过，Ruff 格式/规则、Mypy、`uv lock --check` 通过；PostgreSQL `select 1`、Redis PING、MinIO/Vault health 和 SeaTunnel TCP 均通过。
- 未完成：JWT 登录、租户上下文、用户/项目/成员/角色 API 和职责分离测试。
- 涉及文件：`src/etl_agent/`、`migrations/`、`alembic.ini`、`tests/unit/`、`pyproject.toml`、`uv.lock`、`docs/development/首期开发手册_DevelopmentHandbook.md`

## 2026-08-25：阶段 0 网络与依赖健康检查通过

- 级别：开发环境/验证
- 类型：验收完成
- 来源：Windows 本地 `.env` 和 VM 依赖连通性复测
- 根因与解决过程：确认 Windows 到 VM 的 PostgreSQL、Redis、MinIO、Vault、SeaTunnel 端口均可达；协议级检查中 PostgreSQL `select 1`、Redis PING、MinIO health `200`、Vault health `200` 全部通过，阶段 0 的环境基线完成。
- 涉及文件：`项目学习资料_ProjectLearning/开发计划与里程碑_DevelopmentPlan.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：执行阶段 0 环境与契约基线

- 级别：开发环境/工程基线
- 类型：执行记录
- 来源：首期开发手册阶段 0 完成定义
- 根因与解决过程：完成 VM Compose 依赖启动确认、源码/测试/迁移目录骨架、GitHub Actions CI 基线及本地 `uv`/Ruff/pytest/Mypy/锁文件检查；Windows 到 VM 的端口测试失败，原因是 VM Compose 仍绑定 `127.0.0.1`，阶段 0 暂标记为部分完成。
- 涉及文件：`src/etl_agent/`、`tests/`、`migrations/`、`.github/workflows/ci.yml`、`pyproject.toml`、`项目学习资料_ProjectLearning/开发计划与里程碑_DevelopmentPlan.md`

## 2026-08-25：SeaTunnel 单节点启动验证通过

- 级别：开发环境/验证
- 类型：修复完成
- 来源：VM `docker compose --profile data-plane ps seatunnel` 和最近日志
- 根因与解决过程：移除不受支持的显式 `master_and_worker` 参数后，SeaTunnel 2.3.10 容器稳定保持 `Up`，端口 `5801-5803` 已绑定，最近 10 秒无新增错误日志。
- 涉及文件：VM `docker-compose.yml`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：纠正 SeaTunnel 单节点角色参数

- 级别：开发环境/故障排查
- 类型：配置纠正
- 来源：SeaTunnel 2.3.10 日志显示 `Not supported cluster role: master_and_worker`
- 根因与解决过程：确认该版本显式角色只接受 `master` 或 `worker`；不传 `-r` 时由引擎默认使用 `MASTER_AND_WORKER`。撤销上一条错误的 `-r master_and_worker` 修正，单节点 Compose 改为不传角色参数。
- 涉及文件：`docker-compose.yml`、`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：定位 SeaTunnel 启动角色参数故障

- 级别：开发环境/故障排查
- 类型：配置修正
- 来源：VM SeaTunnel 日志显示 `Expected a value after parameter -r`
- 根因与解决过程：默认配置补齐后，SeaTunnel 继续因 Compose 仅传入 `-r` 而未传入角色值退出；将单节点开发配置修正为 `-r master_and_worker`，并要求重新创建容器。
- 涉及文件：`docker-compose.yml`、`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：定位 SeaTunnel 配置目录故障

- 级别：开发环境/故障排查
- 类型：修复指引
- 来源：VM `docker compose --profile data-plane logs --tail=200 seatunnel`
- 根因与解决过程：日志显示 `seatunnel-cluster.sh` 找不到 `/opt/seatunnel/config/jvm_options`；确认 Compose 的宿主机配置目录为空并覆盖了镜像内置配置。停止重启中的容器，补充从镜像提取默认配置再重启的操作，并同步部署手册。
- 涉及文件：`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：更新 VM 基础设施运行状态

- 级别：开发环境/故障排查
- 类型：状态更新
- 来源：项目负责人提供 Ubuntu VM `docker ps` 输出
- 根因与解决过程：确认 PostgreSQL、Redis、MinIO、Vault 已由 Compose 启动且健康；SeaTunnel 2.3.10 容器持续 `Restarting (1)`，待通过容器日志和挂载配置进一步定位
- 涉及文件：根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：核对并校正 Agent 文档状态

- 级别：文档治理/安全提醒
- 类型：校正
- 来源：核对根目录和学习资料目录 `AGENTS.md` 与当前仓库、需求基线及开发环境记录
- 根因与解决过程：修正已删除 `AGENT.md` 的残留阅读指引，明确当前尚无 `src/`、`tests/` 等业务实现，明确 VM Docker 尚无运行实例，并将控制面描述调整为后续开发环境；同时发现本机 `.env` 存在非占位 LLM 密钥值，已在交付说明中要求轮换，未修改本机配置
- 涉及文件：根目录 `AGENTS.md`、本目录 `AGENTS.md`

## 2026-08-25：合并项目根目录 Agent 入口

- 级别：文档治理
- 类型：整理
- 来源：统一项目根目录 Agent 自动发现入口，减少主入口与增量约定之间的重复跳转
- 根因与解决过程：将根目录 `AGENT.md` 的项目定位、技术边界、导航和工程规则合并到 `AGENTS.md`，再合并原 `AGENTS.md` 的执行与文档约定；删除根目录重复的 `AGENT.md`
- 涉及文件：根目录 `AGENTS.md`、删除的根目录 `AGENT.md`、本目录 `AGENTS.md`

## 2026-08-25：合并学习资料目录入口

- 级别：文档治理
- 类型：整理
- 来源：统一 Agent 使用入口，减少入口文档重复和维护漂移
- 根因与解决过程：将本目录原 `AGENT.md`、`AGENTS.md` 和 `README.md` 的入口、导航、学习顺序和执行约定合并到唯一权威文件 `AGENTS.md`；删除两个重复文件，并同步更新项目根目录和 `docs/README.md` 的链接
- 涉及文件：`项目学习资料_ProjectLearning/AGENTS.md`、根目录 `AGENT.md`、`docs/README.md`

## 2026-08-25：建立项目学习资料目录

- 级别：文档/工程基础
- 类型：新增
- 来源：参考 `smart-audiobook-assistant` 的 `AGENT.md`、`AGENTS.md`、学习笔记、架构亮点和开发计划组织方式
- 根因与解决过程：ETL-Agent 原有架构和开发文档较完整，但缺少面向初学者的扫盲、学习路径、代码阅读指南、LLM 专题、故障排查和 Agent 入口索引；新增 `项目学习资料_ProjectLearning/`，并将普通文档按中文+英文关键词命名
- 涉及文件：本目录全部文档、`docs/README.md`

## 记录规则

后续每次重要修复、优化、配置变更或学习结论按“级别、类型、来源、根因与解决过程、涉及文件”记录。未解决事项单独标记为待处理，不把失败假设写成完成结论。
