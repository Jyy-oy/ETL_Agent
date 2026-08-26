# ETL-Agent 工程文档索引

文档按用途分组，需求文档仍以 `RequirementsDescription/` 为业务基线。

## 学习资料

- [项目学习资料入口](../项目学习资料_ProjectLearning/AGENTS.md)：扫盲、学习路线、代码阅读、LLM、排障、工程亮点和 Agent 工作约定。

## 架构

- [首期技术选型](architecture/首期技术选型_InitialTechnicalSelection.md)：组件矩阵、本地部署边界、网络约定和待确认决策。
- [系统设计架构](architecture/系统设计架构_SystemArchitecture.md)：逻辑分层、运行拓扑、模块边界、状态模型和扩展点。
- [系统详细设计](architecture/系统详细设计_SystemDetailedDesign.md)：领域聚合、端口接口、LLM 生成管线、Harness 协议和失败恢复。
- [UML 类图说明](architecture/UML类图说明_UMLClassDiagram.md)：核心实体关系、扩展接口和实现顺序。
- [UML 类图源文件](architecture/diagrams/ETLAgent领域类图_ETLAgentDomainClass.puml)
- [UML 受管执行时序图](architecture/diagrams/ETLAgent受管执行时序图_ETLAgentLifecycleSequence.puml)
- [UML 部署拓扑图](architecture/diagrams/ETLAgent部署拓扑图_ETLAgentDeployment.puml)
- [逻辑 ER 图](architecture/diagrams/ETLAgent逻辑ER图_ETLAgentER.puml)

## 项目治理

- [系统生命周期](lifecycle/系统生命周期_SystemLifecycle.md)：需求、设计、开发、测试、发布、运行和退役门禁。
- [MVP 范围与路线图](project/MVP范围与路线图_MVPScopeAndRoadmap.md)：首条验收链路、明确不做项和扩展阶段。

## 开发

- [开发环境与依赖](development/开发环境与依赖_DevelopmentEnvironment.md)：uv、依赖分层、服务地址和质量检查。
- [首期开发手册](development/首期开发手册_DevelopmentHandbook.md)：实施顺序、Harness 规则、测试矩阵和完成定义。
- [项目使用手册](development/项目使用手册_ProjectUserGuide.md)：启动、百炼配置、控制台操作和带功能标注的模拟测试步骤。
- [项目测试手册](development/项目测试手册_ProjectTestingGuide.md)：自动化、集成、浏览器、API 冒烟和合成数据面验收步骤。
- [M6 控制台源码](../frontend/)：Vue 3 + Vite 控制台，覆盖连接/Profile、Studio、审批、运行中心和 Benchmark。

## 运维

- [Ubuntu 虚拟机部署](operations/Ubuntu虚拟机部署_LocalVMDeployment.md)：针对 `192.168.181.128` 的 Docker Compose 部署、验证、防火墙和备份起点。

## API、质量与安全

- [API 契约基线](api/API契约基线_APIContract.md)：通用约定、端点矩阵、错误结构和版本兼容。
- [安全设计与威胁模型](security/安全设计与威胁模型_SecurityDesign.md)：信任边界、威胁控制、LLM 安全和 Secret 管理。
- [测试与质量策略](quality/测试与质量策略_TestStrategy.md)：测试分层、必测场景、质量门禁和测试证据。
- [需求追踪矩阵](traceability/需求追踪矩阵_RequirementsTraceability.md)：需求到设计、模块、测试和验收证据的映射。
- [数据模型与数据库设计](data/数据模型与数据库设计_DataModel.md)：数据分层、核心表、关系约束、索引、迁移和保留策略。

## 决策沉淀

- [ADR-0001 首期技术基线](decisions/ADR-0001-首期技术基线_InitialStack.md)：记录选型背景、取舍、后果和复审条件。
