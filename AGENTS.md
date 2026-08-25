# AGENTS.md — ETL-Agent 项目入口与 Agent 约定

> 本文件是 ETL-Agent 项目根目录的唯一 Agent 入口，合并了原 `AGENT.md` 的项目入口、技术边界和开发导航，以及原 `AGENTS.md` 的执行与文档约定。新会话开始任何开发、排查或文档任务前先阅读本文件，再按导航进入学习资料和 `docs/`。代码、测试和实际运行结果优先于文档；发现差异时记录并更新文档。

## 1. 项目定位

ETL-Agent 是企业数据集成控制面：自然语言需求经过 LangGraph 生成 EtlPlan/SeaTunnel HOCON，执行确定性门禁、不可变版本、四眼审批、单次 Capability 授权、Celery/SeaTunnel 运行、质量分流、监督、回滚和审计。

首期主链路是 MySQL → Profile → 百炼生成 → 门禁 → Prepare/Approve/Commit → SeaTunnel → Doris 影子表/原子 Swap。

## 2. 当前状态和技术边界

- 仓库处于源码初始化阶段；Python 3.12 + uv 依赖和 `uv.lock` 已准备，当前主要包含工程文档、配置和基础设施 Compose，尚无 `src/`、`tests/`、`migrations/` 或业务 SeaTunnel 配置实现。
- Windows/PyCharm 是后续控制面开发环境；截至 2026-08-25，Ubuntu VM `192.168.181.128` 已由 Compose 启动 PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel，核心基础设施均处于运行状态；SeaTunnel 使用不传 `-r` 的单节点默认 `MASTER_AND_WORKER` 角色。VM 端口已对开发机开放，Windows 到 PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel 的 TCP/协议级检查均通过。
- LLM 只调用远端百炼，不在本地或 VM 部署模型。
- 详细学习、Agent 约定和源码阅读入口见 [项目学习资料](项目学习资料_ProjectLearning/AGENTS.md)。

## 3. 必读导航

- 业务需求：[整理版需求说明](RequirementsDescription/整理版需求说明_RequirementsSpecification.md)。
- 范围和生命周期：[MVP 范围](docs/project/MVP范围与路线图_MVPScopeAndRoadmap.md)、[系统生命周期](docs/lifecycle/系统生命周期_SystemLifecycle.md)。
- 架构和模型：[系统架构](docs/architecture/系统设计架构_SystemArchitecture.md)、[系统详细设计](docs/architecture/系统详细设计_SystemDetailedDesign.md)、[数据模型](docs/data/数据模型与数据库设计_DataModel.md)。
- 实现和运行：[开发手册](docs/development/首期开发手册_DevelopmentHandbook.md)、[API 契约](docs/api/API契约基线_APIContract.md)、[VM 部署](docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md)。
- 质量和安全：[测试策略](docs/quality/测试与质量策略_TestStrategy.md)、[安全设计](docs/security/安全设计与威胁模型_SecurityDesign.md)、[需求追踪](docs/traceability/需求追踪矩阵_RequirementsTraceability.md)。

## 4. 不可绕过的工程规则

- LLM 不直接决定权限、预算、审批或副作用；模型输出必须结构化校验并通过确定性门禁。
- Prepare 无外部副作用；Approve 针对冻结事实；Commit 复核指纹并在同一事务中创建 ExecutionRun/Outbox。
- Maker 不得自批；高风险 Checker 槽不得由同一人占用；授权必须服务端校验。
- Secret 只通过 SecretProvider/SecretRef 使用；不得把真实密钥、未脱敏样本或 Capability 原文写入日志。
- Workflow、Execution、Replay Guard、Outbox 和 Checkpoint 不得只存进程内存。
- PipelineVersion 不可原地修改；修复、策略或 Prompt 变化必须产生可追踪版本。

## 5. 执行约定

- 开始任务先读取根目录 `AGENTS.md`，再按任务读取最小必要文档，不要一次性加载全部资料。
- 搜索优先使用 `rg`；手工修改使用 `apply_patch`；不要覆盖用户已有改动。
- 先验证边界输入、环境变量、网络和依赖健康，再定位下游代码。
- 变更 API、数据库、配置、Prompt、策略、连接器或执行引擎时同步文档、测试和 `.env.example`。
- 修改完成后运行与风险匹配的静态检查和测试，并明确未能运行的验证。

## 6. 文档约定

- 普通文档使用“中文名称_EnglishKeyword.md”；项目根目录和学习资料目录的唯一 Agent 入口均为 `AGENTS.md`，`README.md` 仅在确需对外说明时使用。
- 需求、架构、实现、测试、运维和学习结论分开沉淀；未决事项显式标记为 TBD。
- 重要修复和排查结果按固定格式写入 `项目学习资料_ProjectLearning/变更记录_Changelog.md` 或 `项目学习资料_ProjectLearning/开发问答笔记_LearningNotes.md`。
