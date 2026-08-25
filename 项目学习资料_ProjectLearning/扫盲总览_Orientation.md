# ETL-Agent 扫盲总览

本文面向第一次接触企业 ETL、Agent 和数据治理的开发者。先建立词汇和系统边界，再进入代码和详细设计。

## 1. ETL 是什么

ETL 是 Extract（抽取）、Transform（转换）、Load（加载）：从源系统读取数据，按规则清洗/转换，再写入目标系统。传统 ETL 的难点不是“把数据复制过去”，而是连接权限、字段映射、质量规则、失败重跑、审计和回滚。

ETL-Agent 的 Agent 只负责把自然语言需求转换成结构化候选方案；它不应该直接拿到数据库写权限，也不应该绕过审批自动执行。

## 2. 控制面和数据面

- 控制面像交通调度中心：保存连接、Profile、Pipeline 版本、审批、预算、状态和审计证据。
- 数据面像运输车辆：实际读取和搬运大规模业务数据。
- FastAPI/LangGraph/Celery/PostgreSQL 主要属于控制面；SeaTunnel 属于数据面。
- 控制面只传递 Profile、制品和受管命令，不把海量业务数据穿过 API。

## 3. Agent、Workflow 和普通 API 的区别

- 普通 API 是确定性的请求/响应。
- Agent 是受约束的模型能力，适合解析自然语言、提出澄清问题和生成候选。
- Workflow 是可恢复的确定性流程，规定 Agent 何时调用模型、何时暂停、何时校验、何时进入审批。
- LangGraph 用节点和状态保存 Workflow；它不是权限系统，也不是数据搬运引擎。

## 4. Harness 是什么

Harness 是不可绕过的安全执行内核。可以把它理解为“所有危险工具调用前的闸门”：

1. `Prepare`：冻结输入指纹、资源范围、风险、预算和回滚方案。
2. `Approve`：独立 Checker 针对冻结事实审批。
3. `Commit`：复核指纹和审批，签发单次 Capability，再创建执行事实和 Outbox。

任何直接从 HTTP 路由调用 SeaTunnel、数据库写操作或清理逻辑的实现，都绕过了 Harness。

## 5. Profile、制品和不可变版本

- Metadata Profile 是受管只读探查结果，包含 Schema、字段类型、近似统计和脱敏样本。
- EtlPlan 是结构化 ETL 设计；HOCON 是 SeaTunnel 配置候选。
- PipelineVersion 是冻结制品。内容、Schema、质量规则或 HOCON 改变，都必须产生新版本和新 SHA-256 摘要。

## 6. 四眼原则和职责分离

Maker 提交方案，Checker 1 审查数据映射和质量，Checker 2 审查安全、资源和 Secret，Operator 执行 Commit，Auditor 查询证据。申请人不能审批自己的申请；高风险操作的多个审批槽不能被同一人占用。

## 7. 质量分流与回滚

数据先写入影子表。通过质量契约的数据准备发布，不合格数据写入错误表并记录错误码。全部检查通过后执行原子 Swap；失败时清理影子表或恢复目标表。这样避免半成品直接覆盖正式数据。

## 8. 为什么需要 PostgreSQL、Redis、MinIO、Vault

- PostgreSQL：业务事实、Checkpoint、Outbox、审批和审计。
- Redis：Celery 队列、任务结果、Capability 防重放的短时记录。
- MinIO：文件、HOCON、Benchmark 和其他大对象；数据库只保存 URI 和摘要。
- Vault：连接密码和运行时 Secret；业务表只保存 SecretRef。

## 9. 初学者最容易混淆的点

- LLM 生成了方案，不等于方案安全或可执行；门禁必须由代码完成。
- Celery 投递成功，不等于 SeaTunnel 作业成功；需要独立的 ExecutionRun 状态机。
- Redis 中的状态不是永久业务事实；关键事实必须落 PostgreSQL。
- 取消任务不等于数据回滚；回滚需要影子表、清理和目标发布策略。
- `.env` 能让本地跑起来，不代表生产 Secret 管理合格。
