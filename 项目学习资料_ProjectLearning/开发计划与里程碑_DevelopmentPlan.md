# ETL-Agent 开发计划与里程碑

状态：MVP 初始计划，具体日期待容量和技术确认后排期

## 阶段 0 执行记录（2026-08-25）

当前结果：阶段 0 环境与契约基线已完成；百炼真实 API 调用留待控制面实现后执行。

- [x] VM 已启动 PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel；SeaTunnel 单节点默认角色已稳定运行。
- [x] 百炼配置项已在本机 `.env` 中具备 Provider、Base URL、模型、超时和重试参数；真实 API 调用暂不在本次环境基线检查中执行。
- [x] 已建立 `src/etl_agent` 分层包、`tests/unit`、`tests/integration`、`migrations` 和 GitHub Actions CI 基线。
- [x] `uv lock --check`、Ruff 格式/规则检查、pytest 和 Mypy 全部通过。
- [x] Windows 到 VM `192.168.181.128` 的 PostgreSQL/Redis/MinIO/Vault/SeaTunnel 端口连通性：TCP 和协议级健康检查均通过。

网络配置：VM 的 `.env` 已改为对外绑定并完成连通性验证；Windows 本机 `.env` 的依赖服务地址已指向 `192.168.181.128`。`COMPOSE_BIND_IP=127.0.0.1` 仍保留在模板和不运行 Compose 的本机环境中作为安全默认值。

## 里程碑 M0：环境与文档

交付：uv/Compose 可用、VM 依赖健康、百炼配置确认、目录骨架、需求/架构/API/测试文档。

完成条件：Windows/PyCharm 能访问 VM 依赖；关键文档和 `.env.example` 一致。

## 里程碑 M1：控制面基础

当前进度：M1.2 本地认证、项目成员上下文和职责槽 API 已完成；企业 OIDC/SSO 仍属于后续扩展。

- [x] 配置加载、请求 ID、统一错误结构和 `/health` 基础 API。
- [x] PostgreSQL/Redis/MinIO/Vault/SeaTunnel/LLM 配置探针。
- [x] Alembic 初始迁移：`users`、`projects`、`project_memberships`、`project_role_grants`。
- [x] JWT 认证、本地开发用户注册/登录和项目成员上下文。
- [x] 用户/项目/成员/角色管理 API 及职责槽冲突测试。
- [ ] 企业 OIDC/LDAP/SSO 适配器。

交付：FastAPI `/health`、配置加载、数据库迁移、用户/项目/成员/角色、统一错误和 request ID。

完成条件：租户隔离和职责槽有单元/API 测试。

## 里程碑 M2：连接与 Profile

当前进度：M2.3 文件资产和文件 Profile 已完成；MySQL/Doris 使用 Compose 合成环境和脱敏样本学习验证，真实业务凭据不是首期前置条件。

- [x] 连接和元数据 Profile ORM 模型及 Alembic 迁移。
- [x] 项目级连接登记/查询 API，仅保存非敏感参数和 `SecretRef`。
- [x] Profile 稳定响应模型和最近快照查询 API。
- [x] 敏感字段不得通过 `options` 绕过 SecretRef 的单元测试。
- [x] Vault KV v2 SecretProvider 与 MySQL/Doris 连接测试适配器。
- [x] MySQL/Doris 只读 Schema、近似行数、脱敏样本和 SHA-256 Profile 指纹。
- [x] MinIO 文件资产、上传大小限制和 CSV/JSON/XLSX/Parquet 文件 Profile。

交付：MySQL/Doris 连接、SecretRef、连接测试、只读 Profile、脱敏样本和 MinIO 文件资产。

完成条件：无 Secret 明文泄露，Profile 摘要可复用。

## 里程碑 M3：Agent 生成

当前进度：M3.2 已完成 Provider 边界加固和显式集成测试入口；真实百炼验收需由项目负责人开启非生产测试开关后执行。

- [x] EtlPlan、Profile 引用、QualityContract、RuntimeBudget 和 ValidationIssue 严格模型。
- [x] OpenAI-compatible 远端 Provider、超时/有限重试、脱敏请求和 fake Provider。
- [x] LangGraph 意图检查、Profile 摘要、候选生成、Schema/HOCON 校验、确定性门禁和一次修复。
- [x] PostgreSQL Checkpoint 封装、Pipeline/不可变 PipelineVersion/AgentRun/GenerationAttempt 迁移和最小生成 API。
- [x] 澄清回答 `/api/v1/agent-runs/{run_id}/answers` 与同一 thread 的 API 恢复。
- [x] Provider Prompt 字节上限、瞬态错误重试、显式集成测试标记和 VM Checkpoint 自动化测试。
- [ ] 使用非生产 API Key 执行真实百炼脱敏调用验收。

交付：LLMProvider、LangGraph、Checkpoint、澄清中断、EtlPlan/HOCON 结构化输出、门禁和不可变版本。

完成条件：fake LLM 测试覆盖合法、非法、缺参、修复上限和恢复。

## 里程碑 M4：Harness 与审批

当前进度：M4.4 已完成 PDP、Prepare、独立 Checker 审批、Ed25519 Capability、Commit、ExecutionRun、Transactional Outbox 和 Evidence Ledger；Celery/SeaTunnel Worker 消费在 M5 完成。

- [x] PDP v1 根据环境、写入意图、数据分级和预算输出 P0-P3 风险及 Checker 槽。
- [x] Prepare 校验不可变 PipelineVersion 和 Profile 项目边界，冻结输入指纹、资源范围、预算和有效期。
- [x] Prepare 创建独立审批槽；Approve 校验 Checker 职责、申请人自批、过期和重复决策。
- [x] Ed25519 Capability 绑定主体、工具、环境、Preparation 和制品摘要；Redis Replay Guard 使用 SET NX EX 原子消费。
- [x] Commit 重新校验指纹、校验审批并在事务中创建 ExecutionRun。
- [x] Transactional Outbox、ExecutionRun 查询和项目级 Evidence Ledger 哈希链。

交付：PDP、Prepare/Approve/Commit、Ed25519 Capability、Replay Guard、Outbox、Evidence Ledger。

完成条件：自批、职责混用、指纹漂移、过期/重放和事务失败均被拦截。

## 里程碑 M5：数据面闭环

当前进度：M5.5 真实合成数据面闭环已完成；VM 已启动 MySQL、Doris 和 SeaTunnel 2.3.10，学习项目使用确定性合成数据完成真实链路验收。

- [x] M5.1 Celery 应用工厂、Outbox Tool Broker、Capability/Replay Guard 消费和 SeaTunnel Adapter 提交/状态/取消端口。
- [x] M5.2 SeaTunnel 2.3.10 Zeta REST 契约联调、原生状态/指标转换、影子表/错误表状态和合成 MySQL 数据脚本。
- [x] M5.3 QualityContract、RuntimeBudget 监督、超限取消、失败清理、质量通过 Swap 请求、受管回滚 API 和审计快照。
- [x] M5.4 合成 MySQL 数据、质量分流、取消和回滚的可重复学习验收；单元测试保留 SeaTunnel FakeSource/Mock 目标动作。
- [x] M5.5 真实合成 MySQL → Doris HOCON、Doris 原子 DDL 适配器、影子表、原子 Swap/Rollback 和 Celery/Beat 端到端验收。

交付：Celery Worker、SeaTunnel Adapter、影子表、错误表、QualityContract、RuntimeSupervision、Swap 和回滚。

完成条件：合成数据真实链路可重复执行、质量分流、取消和回滚；生产业务库接入、大数据量压测和高可用部署作为后续扩展。

## 里程碑 M6：前端与 Benchmark

当前进度：M6.1 Benchmark 历史摘要持久化已完成；实时推送、真实 L2 链路和企业 SSO 仍是后续扩展。

- [x] Vue 3 + Vite + TypeScript 控制台：总览、连接/Profile、Pipeline Studio、审批工作台、运行中心和 Benchmark。
- [x] 项目级 Pipeline/Version、Preparation、ExecutionRun 列表查询 API，按项目成员权限过滤。
- [x] L0 基线、L1 故障注入的确定性 Benchmark API 和 CLI；报告绑定制品摘要、策略版本、环境和数据集摘要。
- [x] M6 单元测试与前端生产构建验证。
- [x] M6.1 Benchmark 运行事实表、项目级历史查询 API、单条报告查询和控制台历史列表。
- [ ] 可选扩展：Benchmark MinIO 报告存档、SSE/WebSocket 实时指标、真实 L2 链路、OIDC/SSO。

完成条件：关键用户路径可用，结果关联版本、策略和环境。

## 迭代纪律

- 每个里程碑拆成可测试的小任务，不以“代码写完”作为完成标准。
- 需求、API、数据库、配置、测试和部署变更同一迭代同步。
- 未决技术选择进入 ADR/TBD，不在代码中用隐式默认值掩盖。
