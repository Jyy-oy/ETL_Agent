# AGENTS.md — ETL-Agent 项目学习与开发入口

> 本文件是 `项目学习资料_ProjectLearning/` 的唯一入口，合并了原学习资料 `README.md`、项目入口 `AGENT.md` 和增量约定 `AGENTS.md` 的职责。新会话开始任何学习、开发、排查或文档任务前先阅读本文件，再按任务进入 `../docs/` 和需求文档。代码、测试和实际运行结果优先于文档；发现差异时记录并更新文档。

## 1. 项目定位

ETL-Agent 是面向企业数据集成的控制面平台：用自然语言生成结构化 EtlPlan/SeaTunnel HOCON，经过确定性门禁、四眼审批和单次 Capability 授权后，由 Celery/SeaTunnel 执行数据搬运，并提供质量分流、运行监督、回滚、审计和 Benchmark。

首期主链路：MySQL → 只读 Metadata Profile → 远端百炼生成 → EtlPlan/HOCON 门禁 → PipelineVersion → Prepare → Checker 1/2 → Operator Commit → SeaTunnel → Doris 影子表/原子 Swap。

## 2. 首期技术基线和运行边界

以下内容是已确定的首期技术基线，不代表对应业务代码已经实现。

- Python 3.12 + uv；依赖和锁文件在根目录 `pyproject.toml`、`uv.lock`。
- FastAPI + LangGraph + Celery 是控制面；PostgreSQL 16 保存业务事实、Checkpoint、Outbox 和审计；Redis 7 保存队列和短时防重放状态；MinIO 保存大对象；Vault 保存 SecretRef 对应凭据。
- LLM 不在本地或 VM 部署，通过 `.env` 中的百炼 OpenAI 兼容配置远程调用。
- Ubuntu VM 地址为 `192.168.181.128`；截至 2026-08-25，VM 已由 Docker Compose 启动 PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel，核心基础设施均处于运行状态；SeaTunnel 使用不传 `-r` 的单节点默认 `MASTER_AND_WORKER` 角色。VM 端口已对开发机开放，Windows 到 PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel 的 TCP/协议级检查均通过；仓库已有 `/health`、Identity/Project、连接/Profile、文件资产和 M3.1 生成 API，Worker、前端、澄清回答 API 和 Harness 执行协议仍待实现。
- Windows/PyCharm 是后续控制面开发环境；应用进入 Compose 后改用服务名通信。

## 3. 学习资料使用顺序

1. 先读本文件，了解项目入口、环境、运行边界和工程规则。
2. 再读 [扫盲总览](扫盲总览_Orientation.md)，建立 ETL、控制面、数据面、Agent 和 Harness 的基本概念。
3. 按 [项目学习路线](项目学习路线_LearningRoadmap.md) 从 MVP 主链路开始，不要一开始阅读全部高级设计。
4. 进入实现前阅读 [代码阅读指南](代码阅读指南_CodeReadingGuide.md)、[端到端流程演练](端到端流程演练_EndToEndWalkthrough.md) 和 [首期开发手册](../docs/development/首期开发手册_DevelopmentHandbook.md)。
5. 遇到问题先查 [故障排查手册](故障排查手册_TroubleshootingGuide.md)，重要结论追加到 [开发问答笔记](开发问答笔记_LearningNotes.md) 或 [变更记录](变更记录_Changelog.md)。

## 4. 项目文档导航

业务需求：

- [整理版需求说明](../RequirementsDescription/整理版需求说明_RequirementsSpecification.md)
- [主要需求](../RequirementsDescription/主要需求_MainRequirements.md)

范围与架构：

- [MVP 范围与路线图](../docs/project/MVP范围与路线图_MVPScopeAndRoadmap.md)
- [系统生命周期](../docs/lifecycle/系统生命周期_SystemLifecycle.md)
- [系统设计架构](../docs/architecture/系统设计架构_SystemArchitecture.md)
- [系统详细设计](../docs/architecture/系统详细设计_SystemDetailedDesign.md)
- [数据模型与数据库设计](../docs/data/数据模型与数据库设计_DataModel.md)
- [UML 类图说明](../docs/architecture/UML类图说明_UMLClassDiagram.md)

开发与运行：

- [开发环境与依赖](../docs/development/开发环境与依赖_DevelopmentEnvironment.md)
- [首期开发手册](../docs/development/首期开发手册_DevelopmentHandbook.md)
- [API 契约基线](../docs/api/API契约基线_APIContract.md)
- [Ubuntu 虚拟机部署](../docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md)

质量与安全：

- [测试与质量策略](../docs/quality/测试与质量策略_TestStrategy.md)
- [安全设计与威胁模型](../docs/security/安全设计与威胁模型_SecurityDesign.md)
- [需求追踪矩阵](../docs/traceability/需求追踪矩阵_RequirementsTraceability.md)

## 5. 学习资料速查

| 文档 | 用途 |
| --- | --- |
| `扫盲总览_Orientation.md` | 面向初学者解释 ETL、Agent、Workflow、Harness、Profile 和质量分流 |
| `项目学习路线_LearningRoadmap.md` | 从基础环境到生产级实现的学习顺序 |
| `核心概念词典_Glossary.md` | 项目术语和缩写速查 |
| `代码阅读指南_CodeReadingGuide.md` | 源码完成后的模块阅读顺序和观察点 |
| `端到端流程演练_EndToEndWalkthrough.md` | 用一条 MySQL → Doris 链路理解全系统 |
| `LLM模块学习指南_LLMModuleGuide.md` | 百炼调用、LangGraph 和结构化输出边界 |
| `故障排查手册_TroubleshootingGuide.md` | Windows、VM、Docker 和依赖故障排查 |
| `架构亮点与工程实践_ArchitectureHighlights.md` | 设计模式、工程取舍和评审要点 |
| `开发计划与里程碑_DevelopmentPlan.md` | MVP 迭代计划、里程碑和完成定义 |
| `开发问答笔记_LearningNotes.md` | 持续记录概念问答和排查结论 |
| `变更记录_Changelog.md` | 重要修复、文档和工程决策变更记录 |

## 6. 不可绕过的工程规则

- LLM 只生成候选、澄清问题和诊断文本；Schema、权限、预算、风险、审批要求和副作用由确定性代码控制。
- Prepare 不产生外部副作用；Approve 针对冻结事实；Commit 重新校验指纹，并在一个 PostgreSQL 事务中创建 ExecutionRun 和 Outbox。
- Maker 不得自批；高风险 Checker 职责不得由同一人占用；前端隐藏按钮不能替代服务端授权。
- Secret 只通过 SecretProvider/SecretRef 使用；日志、Prompt、API 响应不能出现密码、Token、LLM Key 或未脱敏业务样本。
- 不把任务状态、Checkpoint、Replay Guard 或 Outbox 可靠性依赖进程内存。
- PipelineVersion 不可原地修改；任何修复都产生新版本和新摘要。

## 7. Agent 执行约定

- 先确认任务属于需求、架构、实现、测试、部署还是学习解释，再按导航读取最小必要文档。
- 搜索优先使用 `rg`；修改文件使用 `apply_patch`；不要覆盖用户已有改动。
- 先验证输入和外部依赖，再推断下游逻辑；记录失败假设和证据。
- 涉及配置、接口、数据模型、运行协议的变更，必须同步对应文档和 `.env.example`。
- 运行时状态、队列、Replay Guard、Checkpoint 不得只保存在单进程内存。
- 运行与测试后明确记录通过项、未运行项和环境限制。

## 8. 文档写作约定

- 普通文档文件名使用“中文名称_EnglishKeyword.md”；本文件和项目根目录的 `AGENTS.md` 是固定 Agent 入口名称，`README.md` 仅在确需对外说明时使用。
- 用渐进式披露：入口保持简短，细节放专题文档；未决项单独标记，不混入完成历史。
- 代码引用优先给出模块/类/接口和验证命令；源码尚不存在时明确写“待实现”。
- 学习解释先给直观概念，再连接到 ETL-Agent 的真实边界和示例。
- 重要修复、学习结论和外部依赖问题按固定格式写入 `变更记录_Changelog.md` 或 `开发问答笔记_LearningNotes.md`。
