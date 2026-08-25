# ETL-Agent 核心概念词典

| 术语 | 通俗解释 | 项目中的边界 |
| --- | --- | --- |
| Agent | 能理解自然语言并生成候选的模型驱动能力 | 不能直接决定权限或执行副作用 |
| AgentRun | 一次 Agent/Workflow 执行记录 | 保存状态、节点、模型和错误摘要 |
| ApprovalRequest | 一个具体审批职责槽的请求 | Checker 1/2 分开建模 |
| Capability | 绑定主体、工具、环境和制品的短时能力令牌 | Ed25519 签名且只能消费一次 |
| Checkpoint | Workflow 暂停后恢复所需的状态快照 | PostgreSQL 持久化，不放进程内存 |
| Commit | 三阶段协议的最终提交动作 | 重新校验指纹并创建 ExecutionRun/Outbox |
| Control Plane | 保存事实、策略、审批、状态和审计的控制面 | 不搬运海量业务数据 |
| Data Plane | 执行实际数据读取、转换和写入的数据面 | 首期由 SeaTunnel 承担 |
| EtlPlan | 结构化 ETL 设计 | 必须过 Schema 和确定性门禁 |
| Evidence Ledger | 通过哈希链保存关键证据 | 追加写，支持完整性校验 |
| HOCON | SeaTunnel 使用的配置格式 | 生成后需编译/解析校验 |
| Idempotency | 重复请求产生同一结果而不是重复副作用 | Commit、Outbox、回滚必须具备 |
| Metadata Profile | 受管只读元数据探查结果 | 包含 Schema、统计和脱敏样本 |
| Outbox | 与业务事实同事务落库的待投递事件 | Worker 可重试且按事件 ID 去重 |
| PDP | Policy Decision Point，策略决策点 | 输出 P0-P3 风险和审批要求 |
| PipelineVersion | 不可变 ETL 制品版本 | 内容变更必须产生新摘要 |
| Prepare | 冻结一次执行所依据的事实 | 不产生外部副作用 |
| Profile | 对连接或文件的结构化描述 | 供门禁和 LLM 使用，不等于业务数据全量 |
| QualityContract | 字段清洗、过滤和错误分流规则 | 失败数据进入错误表 |
| Replay Guard | 防止 Capability 被重复消费的机制 | Redis 原子操作 + TTL |
| RuntimeSupervision | 对行数、字节、时长、放大比和拒绝率的监督 | 超限预警、终止或隔离 |
| SecretRef | 指向 Vault/KMS 中凭据的引用 | 业务表不保存密码 |
| Shadow Table | 正式发布前写入的临时目标表 | 通过质量检查后原子 Swap |
| Tool Broker | 所有副作用工具的唯一出口 | 拦截未授权和重放命令 |
| Workflow State | LangGraph 节点、澄清和门禁状态 | 与对话/执行状态隔离 |
