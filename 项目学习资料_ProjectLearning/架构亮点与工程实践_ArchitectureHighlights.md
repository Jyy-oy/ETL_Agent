# ETL-Agent 架构亮点与工程实践

本文用于学习、评审和面试准备，说明项目为什么这样设计，以及这些设计解决了什么生产问题。

## 1. LLM 与确定性策略分工

自然语言理解交给 LLM，权限、Schema、预算、审批和副作用交给代码。这样既获得自然语言交互能力，又避免模型输出直接改变系统事实。

## 2. Prepare/Approve/Commit 三阶段协议

把“准备执行”和“真正执行”拆开，使审批人面对冻结事实，而不是面对执行时实时变化的配置。Commit 再次校验指纹，避免审批后配置被偷偷替换。

## 3. Capability + Tool Broker

Capability 把主体、工具、环境和制品摘要绑定在短时签名令牌中；Tool Broker 作为唯一副作用出口。即使某个 API 或 Worker 写错，也不能直接调用未授权工具。

## 4. Outbox 与业务事实同事务

ExecutionRun 和 OutboxEvent 在同一个 PostgreSQL 事务中落库，避免出现“数据库显示已执行但队列没有命令”或“队列重复执行但业务事实不存在”。

## 5. 三层状态隔离

Conversation、Workflow、Execution 分开建模，避免聊天历史、Agent 节点状态和实际 SeaTunnel 作业互相覆盖，也让恢复、审计和扩展更清楚。

## 6. 不可变制品和可回滚执行

PipelineVersion 用摘要冻结，修复产生新版本；数据先写影子表，质量通过后原子 Swap。模型迭代、审批和运行结果都能回溯到精确制品。

## 7. 适配器和端口隔离外部变化

LLM Provider、SourceConnector、ExecutionEngine、SecretProvider 通过接口接入。这样新增 DeepSeek、Oracle 或其他数据引擎不会把供应商分支塞进领域核心。

## 8. 这些亮点的代价

- 状态、摘要、审计和版本较多，初期建模成本高。
- Outbox、重试、状态机和回滚需要更多测试。
- 远端 LLM 带来网络、成本、配额和数据出境治理。
- 单 PostgreSQL 适合 MVP，但规模扩大后需要拆分 Checkpoint、队列和审计容量。
