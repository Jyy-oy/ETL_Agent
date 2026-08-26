# ETL-Agent 学习资料与工程沉淀变更记录

## 2026-08-26：真实数据面单表 Profile 使用提示和执行错误详情

- 根因：Doris 最近 Profile 同时包含 `orders_current`、`orders_current__smoke` 两张表，真实运行编译器按首期约束拒绝多表目标 Profile，ExecutionRun 记录为 `OUTBOX_DISPATCH_FAILED`。
- 处理：连接与 Profile 页面增加表名范围输入，支持重新探查指定单表；运行中心显示稳定中文错误码和后端脱敏错误详情。
- 边界：该错误发生在 SeaTunnel 提交前，不会创建外部作业或修改目标表；修复后需生成新 PipelineVersion 并重新 Prepare/审批/Commit。
- 验证：前端 TypeScript 检查和生产构建通过；API 健康检查、Worker/Beat 运行状态正常。

## 2026-08-26：项目总览补充项目编码展示

- 内容：项目选择器显示“项目名称（项目编码）”；总览标题下增加当前项目名称和编码，解决创建项目后难以确认项目编码的问题。
- 验证：前端 TypeScript 检查和生产构建通过。

## 2026-08-26：开发环境注册页支持直接绑定 Checker

- 内容：注册页增加项目编码和 Checker 1/2 选择；注册成功后自动建立项目成员关系和对应职责槽，便于直接演示 Prepare/四眼审批。
- 边界：仅在 `APP_ENV=development` 开放；普通账号注册保持兼容；项目编码必须已存在，已占用的 Checker 槽会返回稳定中文错误。
- 使用：先由 Maker 创建项目，退出后在注册页选择 Checker 职责并填写项目编码；建议 Checker 1、Checker 2 使用两个不同账号。
- 验证：新增注册请求模型回归测试，前端构建、Ruff 和 Mypy 检查通过。

## 2026-08-26：M6.1 Benchmark 历史摘要持久化

- 内容：新增 `benchmark_runs` 表和 `0011_benchmark_runs` 迁移；Benchmark POST 在返回报告的同时保存固定参数、数据摘要、制品摘要、策略版本、环境和统计指标。
- API：新增项目级最近历史查询 `GET /api/v1/projects/{project_id}/benchmarks` 和单条报告查询 `GET /api/v1/benchmarks/{benchmark_id}`，均执行项目成员权限校验。
- 前端：Benchmark 页面增加历史报告列表，刷新项目数据时自动加载最近 20 条，选中历史记录可恢复报告摘要。
- 边界：只保存脱敏统计摘要，不保存业务样本；MinIO 详细报告归档、实时推送、真实 L2 Benchmark 和企业 SSO 仍是后续扩展。
- 验证：迁移升级成功；Benchmark POST/项目历史/单条查询真实 API 联调通过；56 项非集成测试通过、2 项显式跳过；Ruff、Mypy、Alembic check、`uv lock --check` 和前端构建通过。

## 2026-08-26：M5.5 真实合成 MySQL → Doris 联调修复

- 根因：VM Doris 开发账号使用空密码，连接测试适配器用 `if not password` 把合法空密码当成缺少凭据，导致 Doris 连接测试和 Profile 探查返回 `SECRET_UNAVAILABLE`。
- 修复：仅在 Secret 缺少 `password` 字段（`None`）时拒绝；保留运行时不把密码写入 PostgreSQL、日志和 API 响应的边界。
- 验证：新增空密码回归测试，随后重新执行 VM Doris 连接测试、Profile 和真实 SeaTunnel 数据面联调。

## 2026-08-26：Windows LangGraph Checkpoint 事件循环启动修复

- 根因：新版 Uvicorn 在 Windows 默认使用 `ProactorEventLoop`，覆盖了应用导入阶段设置的事件循环策略，导致 LangGraph psycopg 异步 Checkpoint 抛出 `InterfaceError`。
- 修复：增加 `selector_event_loop_factory`，启动 API 时通过 Uvicorn `--loop` 显式使用 `SelectorEventLoop`，并同步开发手册命令。
- 验证：使用该启动方式重新执行 PostgreSQL Checkpoint setup 和生成工作流。

## 2026-08-26：Prepare 审批槽外键事务顺序修复

- 根因：Preparation 与 ApprovalRequest 没有 ORM 关系映射，SQLAlchemy flush 时可能先插入审批槽，导致 `preparation_id` 外键约束失败并返回 500。
- 修复：加入审批槽前显式 flush Preparation；仍在同一个 PostgreSQL 事务中提交，失败会整体回滚。
- 验证：重新调用 Prepare，确认返回审批槽并继续 Checker/Operator/Worker 联调。

## 2026-08-26：Beat Outbox 发布后监督任务补投

- 根因：Celery Beat 的批量 Outbox 路径成功提交 SeaTunnel 后没有投递 `supervise_execution_run_task`，ExecutionRun 会永久保持 `running`。
- 修复：批量 Broker 收到引擎作业 ID 后记录执行 ID，在本轮 Outbox 消费结束后安排独立监督任务；单事件消费路径保持原有行为。
- 验证：当前真实 SeaTunnel 作业补触发监督后，确认质量报告、Doris 影子表和原子 Swap 状态。

## 2026-08-26：Doris 原子 Swap 语法兼容修复

- 根因：Doris 2.1 `ALTER TABLE ... REPLACE WITH` 语法要求在影子表前显式写 `TABLE`，旧 SQL 缺少关键字，导致质量通过后的发布动作失败。
- 修复：原子 Swap 和回滚统一生成 `REPLACE WITH TABLE` 语句，并更新 SQL 回归测试。
- 验证：使用新的 Preparation/Capability 重跑真实 SeaTunnel → Doris 影子表 → 原子 Swap。

## 2026-08-26：重复监督不覆盖已发布状态

- 根因：单事件消费路径和 Beat 批量路径都可能投递一次监督任务，第二次监督在 Swap 已发布后重新写入 `swap_requested`，造成执行状态与发布状态不一致。
- 修复：质量通过时仅在发布状态不是 `published` 时写入 `swap_requested`，已发布事实保持不可回退。
- 验证：真实 Doris 目标表已完成原子切换，重复监督后发布状态保持 `published`。

## 2026-08-26：回滚后的清理状态保持单调

- 根因：回滚动作完成后将发布状态设为 `cleaned`，迟到的重复监督仍按质量通过逻辑写回 `swap_requested`。
- 修复：质量通过的重复监督同时保护 `published` 和 `cleaned` 两类已完成状态。
- 验证：重新执行真实发布和回滚，确认回滚终态为 `rollback=completed`、`publish=cleaned`。

## 2026-08-26：修复子目录启动导致 PostgreSQL 回退 localhost

- 级别：缺陷修复
- 类型：配置/测试/文档
- 来源：重启 FastAPI 后登录返回 HTTP 500
- 根因与解决过程：从 `src/etl_agent` 子目录启动时，Pydantic Settings 按当前目录寻找 `.env`，未加载项目根配置，数据库连接退回默认 `localhost:5432`，登录查询用户时被拒绝。配置现在优先按 `config.py` 所在源码位置解析项目根 `.env`，并保留部署目录 `.env` 回退路径。
- 验证证据：在 `src/etl_agent` 目录执行 `uv run python` 时解析到 PostgreSQL `192.168.181.128:5432`、Vault `192.168.181.128:8200` 和 MinIO `192.168.181.128:9000`；配置/API 测试通过，Ruff 和 Mypy 通过。
- 涉及文件：`src/etl_agent/config.py`、`tests/unit/test_config.py`、`docs/development/开发环境与依赖_DevelopmentEnvironment.md`、`项目学习资料_ProjectLearning/故障排查手册_TroubleshootingGuide.md`

## 2026-08-26：修复 MySQL Profile 元数据键大小写兼容问题

- 级别：缺陷修复
- 类型：修复/测试/排障文档
- 来源：VM 合成 MySQL Profile 探查失败
- 根因与解决过程：连接测试只执行 `SELECT 1` 可以通过，但 MySQL `information_schema` 查询返回大写列名，Profile 代码按小写键读取并触发 `KeyError('table_schema')`。新增大小写不敏感的元数据字段读取，补充回归测试和排障说明；前端对仍指向 `127.0.0.1` 的旧 MySQL 连接给出 VM 地址修正提示。
- 验证证据：VM 连接 `192.168.181.128` 成功识别 `demo_orders`，读取 1 张表和样本；MySQL 精确行数为 10,000；M2.2 单元测试 6 项通过，Ruff、Mypy 和格式检查通过。
- 涉及文件：`src/etl_agent/infrastructure/profiling.py`、`tests/unit/test_m2_2.py`、`frontend/src/App.vue`、`项目学习资料_ProjectLearning/故障排查手册_TroubleshootingGuide.md`、`项目学习资料_ProjectLearning/开发问答笔记_LearningNotes.md`

## 2026-08-26：Profile 选择防误填和合成订单数据初始化

- 级别：开发体验增强/学习数据
- 类型：修复/增强
- 来源：Pipeline Studio 使用反馈
- 根因与解决过程：页面原先允许把 `1`、`2` 等示例数字直接提交为 Profile ID，后端按 UUID 校验后返回英文错误。现在源/目标 Profile 改为已读取 Profile 下拉选择，未加载 Profile 时生成和 Prepare 按钮禁用，并补充 UUID 和字段校验中文提示；同时在 VM 合成 MySQL 中初始化 `demo_orders` 10,000 行确定性订单数据。
- 验证证据：前端 `npm run build` 通过；MySQL `demo_orders` 行数为 10,000，主键范围 1-10,000；Ruff、Mypy 和格式检查通过。
- 涉及文件：`frontend/src/App.vue`、`docs/development/项目使用手册_ProjectUserGuide.md`、`docs/development/项目测试手册_ProjectTestingGuide.md`

## 2026-08-26：项目使用手册和百炼验证步骤补齐

- 级别：开发体验增强
- 类型：文档
- 来源：项目实际使用与百炼配置确认
- 内容：新增《项目使用手册》，覆盖 VM/Windows 启动、百炼配置确认、合成 MySQL 数据准备、控制台主流程、连接/Profile、Pipeline 生成、Prepare/Approve/Commit、运行监督、Benchmark 和中文错误提示模拟测试；每个测试用例明确验证的功能和预期结果。
- 边界：当前 `.env` 已配置百炼兼容地址、模型和非空 API Key，但真实烟测开关仍默认关闭；文档提供仅发送虚拟 Profile 的显式烟测命令。
- 涉及文件：`docs/development/项目使用手册_ProjectUserGuide.md`、`docs/README.md`

## 2026-08-26：连接编辑与 VM 地址修正流程补齐

- 级别：开发体验增强
- 类型：修复/增强
- 来源：连接与 Profile 页面实测反馈
- 根因与解决过程：旧连接可能仍保存 `127.0.0.1`，而 Windows/PyCharm 访问的是 Ubuntu VM；同时已有连接无法在页面内修正。前端默认合成 MySQL 地址使用 `192.168.181.128`，新增“编辑/保存/取消”流程和中文提示；后端新增 `PUT /api/v1/connections/{connection_id}`，仍只允许更新非敏感字段并保留 SecretRef 约束。
- 验证证据：VM 上的 MySQL、Vault 凭据和连接测试已通过；前端构建、51 项非集成测试、Ruff、Mypy、Alembic check 和 `uv lock --check` 通过。
- 涉及文件：`frontend/src/App.vue`、`src/etl_agent/api/connection_models.py`、`src/etl_agent/api/connections.py`、`tests/unit/test_api.py`、`docs/development/项目测试手册_ProjectTestingGuide.md`

## 2026-08-26：控制台中文错误提示与首轮测试手册补齐

- 级别：开发体验增强
- 类型：修复/文档
- 来源：M6 前端首次使用反馈
- 根因与解决过程：注册参数校验、网络不可达、权限错误和状态枚举此前部分直接显示英文或底层编码，且新用户注册后缺少项目初始化入口；前端现在统一转换为中文提示，并在无项目时提供“创建学习项目”入口。新增《项目测试手册》，明确自动化、集成、浏览器、API 冒烟和合成数据面验收步骤。
- 验证证据：`npm run build` 通过；全量非集成 pytest 51 项通过；Ruff、Mypy 和格式检查通过。
- 涉及文件：`frontend/src/App.vue`、`docs/development/项目测试手册_ProjectTestingGuide.md`、`docs/development/首期开发手册_DevelopmentHandbook.md`、`docs/README.md`

## 2026-08-26：M6 Vue 控制台与可重复 Benchmark 完成

- 级别：开发实现
- 类型：新增
- 来源：阶段 6 前端与 Benchmark
- 根因与解决过程：新增 Vue 3 + Vite + TypeScript 控制台，覆盖项目总览、连接/Profile、Pipeline Studio、四眼审批、运行中心和 Benchmark；新增项目级 Pipeline/Version、Preparation、ExecutionRun 列表 API，避免前端绕过控制面读取数据库。新增确定性 L0 基线、L1 故障注入 Benchmark API 与 CLI，固定数据规模、随机种子、制品摘要、策略版本和环境即可复现实验结果。
- 验证证据：前端 `npm run build` 通过；M6 Benchmark/路由测试通过；全量非集成 pytest 51 项通过、2 项显式跳过；Ruff、Mypy 和格式检查通过。
- 学习项目边界：Benchmark 使用合成统计，不访问真实 MySQL/Doris，不保存业务样本；历史报告持久化、真实 L2 链路、实时推送和企业 SSO 留待后续扩展。
- 涉及文件：`frontend/`、`src/etl_agent/benchmark.py`、`src/etl_agent/api/benchmarks.py`、`src/etl_agent/api/generation.py`、`src/etl_agent/api/preparations.py`、`scripts/run_benchmark.py`、`tests/unit/test_m6_benchmark.py`

## 2026-08-26：M5.2 质量监督和 SeaTunnel 2.3.10 REST 契约校准完成

- 级别：开发实现/联调修复
- 类型：增强
- 来源：阶段 4、阶段 5 收尾
- 根因与解决过程：补齐 `ExecutionRun` 质量、发布、清理和回滚状态，运行监督快照、QualityContract/RuntimeBudget 判定、取消/清理/Swap/回滚 Outbox 动作和查询 API；SeaTunnel Adapter 现在按 2.3.10 实际契约发送 `text/plain` HOCON，读取 `jobStatus`/`jobId` 和原生计数/字节指标，并将其转换为控制面稳定字段。取消请求改为 `POST /stop-job` + JSON `jobId`，VM Compose 将宿主 `5802` 正确映射到容器 REST `8080` 并开启 Hazelcast REST API。
- 验证证据：VM FakeSource → Console 作业提交返回作业 ID，`/job-info/{id}` 返回 `FINISHED` 和指标，`/stop-job` 接受 JSON 请求；Windows 访问 `http://192.168.181.128:5802/running-jobs` 返回 200。全量 pytest 49 项通过、2 项集成测试默认跳过；Ruff、Mypy、Alembic check、`uv lock --check` 和 `git diff --check` 通过。
- 学习项目边界：首期用合成 MySQL 数据、SeaTunnel FakeSource/Mock 目标动作验收清理、Swap、回滚和质量分流，不要求真实 MySQL/Doris。真实目标库适配、大数据量生产压测、真实百炼调用、前端和 Benchmark 属于后续可选扩展。
- 涉及文件：`src/etl_agent/workers/engine.py`、`src/etl_agent/workers/quality.py`、`src/etl_agent/workers/supervision.py`、`src/etl_agent/workers/tasks.py`、`migrations/versions/0010_quality_supervision.py`、`.env.example`、`docker-compose.yml`、`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`

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
