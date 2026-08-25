# ETL-Agent 项目学习路线

目标是从“能看懂配置”逐步达到“能安全实现和评审一条受管 ETL 链路”。每阶段都有建议产出，不要求一次读完所有文档。

## 阶段 0：环境和项目地图

阅读：`AGENTS.md`、[开发环境与依赖](../docs/development/开发环境与依赖_DevelopmentEnvironment.md)、[Ubuntu 虚拟机部署](../docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md)。

掌握：uv、Docker Compose、Windows/PyCharm 与 Ubuntu VM 的边界、PostgreSQL/Redis/MinIO/Vault/SeaTunnel 的用途。

练习：启动核心 Compose 服务，检查健康状态；在 `.env` 中区分 VM 主机地址和 Compose 服务名。

## 阶段 1：ETL 和平台领域

阅读：[扫盲总览](扫盲总览_Orientation.md)、[核心概念词典](核心概念词典_Glossary.md)、[MVP 范围](../docs/project/MVP范围与路线图_MVPScopeAndRoadmap.md)。

掌握：Profile、EtlPlan、PipelineVersion、QualityContract、影子表、四眼审批和三阶段协议。

练习：用一张纸画出 MySQL → Doris 主链路，标注每一步的控制面事实和数据面动作。

## 阶段 2：后端和状态

阅读：[系统设计架构](../docs/architecture/系统设计架构_SystemArchitecture.md)、[系统详细设计](../docs/architecture/系统详细设计_SystemDetailedDesign.md)、[数据模型](../docs/data/数据模型与数据库设计_DataModel.md)。

掌握：FastAPI、SQLAlchemy/Alembic、LangGraph Checkpoint、Celery Outbox、状态机和幂等。

练习：实现一个不调用真实数据库的 Preparation 状态迁移，并为非法迁移写测试。

## 阶段 3：LLM 和结构化生成

阅读：[LLM 模块学习指南](LLM模块学习指南_LLMModuleGuide.md)、[端到端流程演练](端到端流程演练_EndToEndWalkthrough.md)。

掌握：Provider Adapter、Prompt 版本、结构化输出、Schema 校验、有限修复和人工中断。

练习：让 fake LLM 返回合法/非法 EtlPlan，观察门禁、修复和中断分支。

## 阶段 4：安全执行和数据面

阅读：[安全设计](../docs/security/安全设计与威胁模型_SecurityDesign.md)、UML 类图/时序图、[测试策略](../docs/quality/测试与质量策略_TestStrategy.md)。

掌握：PDP、Capability、Replay Guard、Tool Broker、影子表、质量分流、取消和回滚。

练习：验证篡改 Capability、重复 Commit、Outbox 重试和预算超限都不会产生未授权副作用。

## 阶段 5：企业级交付

阅读：[系统生命周期](../docs/lifecycle/系统生命周期_SystemLifecycle.md)、[API 契约](../docs/api/API契约基线_APIContract.md)、[需求追踪](../docs/traceability/需求追踪矩阵_RequirementsTraceability.md)。

掌握：需求到测试追踪、发布/回滚、备份恢复、监控、审计、变更和 ADR。

练习：为一个新增连接器写需求 ID、架构决策、API 变化、测试证据和回滚说明。
