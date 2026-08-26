# ETL-Agent 端到端流程演练

本文用“从合成 MySQL 抽取订单到 VM Doris”解释一条完整链路。表名、字段和数据均为学习项目生成的合成内容，不使用真实业务库；数据面在 VM 上真实运行 SeaTunnel 2.3.10、Doris 影子表、原子 Swap 和 Rollback，单元测试才使用 Mock 适配器。

## 1. 连接与 Profile

1. Maker 在项目中登记 MySQL 源连接和 Doris 目标连接。
2. 密码写入 Vault，业务表只保存 `secret_ref`。
3. 服务端使用只读账号探查源表 Schema、字段类型、主键和近似统计。
4. 手机号、邮箱等样本脱敏后生成 MetadataProfile，并计算 Profile 摘要。

此阶段不能读取全量业务数据，也不能调用目标表写接口。

## 2. 自然语言需求和 Agent

Maker 输入：把订单表中的有效订单同步到 Doris，每天按更新时间增量同步，金额必须为正数，手机号脱敏，错误数据进入错误表。

LangGraph 依次执行：

```text
IntentParse
  -> ProfileEnrichment
  -> CandidateGeneration（百炼）
  -> StructuredValidation
  -> HoconCompile
  -> DeterministicGate
```

如果没有提供增量字段或目标表，Workflow 通过 Checkpoint 暂停并返回澄清问题。Maker 提交回答后，系统从同一个 Thread/Checkpoint 恢复，而不是重新丢失上下文。

## 3. 候选、门禁和版本

模型只返回结构化候选：源/目标 Profile 引用、字段映射、转换、质量规则、调度信息和 HOCON。代码随后检查：

- 源字段是否存在，类型是否可转换。
- 连接器是否声明支持该转换。
- 质量规则和错误分流是否完整。
- 读取行数、写入字节、时长、放大比和拒绝率是否在预算内。
- HOCON 是否能被解析，禁止配置未授权的写入或外部工具。

通过后计算 EtlPlan/HOCON 的 SHA-256，生成不可变 PipelineVersion。模型文本本身不能绕过门禁。

## 4. Prepare/Approve/Commit

### Prepare

服务端冻结版本摘要、Profile 摘要、资源范围、数据分级、风险级别、预算、影子表名和回滚方案，生成 Preparation。这个动作不启动 SeaTunnel。

### Approve

- Checker 1 审查字段映射、清洗规则、错误码和 DAG。
- Checker 2 审查连接资源、数据分级、Secret 引用、预算和回滚。

申请人不能审批自己的 Preparation，两个高风险槽不能由同一用户占用。

### Commit

Operator 提交 Commit 后，服务端重新计算指纹并检查审批事实。通过后签发短时单次 Capability，在同一个 PostgreSQL 事务中创建 ExecutionRun 和 OutboxEvent。

## 5. Worker 和 SeaTunnel

Celery Worker 消费 Outbox，验证 Capability 和 Replay Guard，通过 Tool Broker 调用 SeaTunnel。VM 演示使用合成 MySQL 作为真实源端，SeaTunnel 通过容器网络读取数据并写入 Doris 影子表；违反质量契约的数据进入错误表并记录错误码。FakeSource/Mock 仅用于不依赖 VM 的单元测试。

控制面记录 Engine Job ID、读取/写入/拒绝行数、字节、耗时、吞吐、放大比和错误引用，不把海量数据经过 API 返回。

## 6. 发布、监督和回滚

- 质量通过：Doris 适配器执行 `ALTER TABLE ... REPLACE WITH TABLE ...` 原子 Swap，将影子表发布为正式表。
- 预算超限：RuntimeSupervision 触发预警或 Kill Job。
- 作业失败：保存失败快照，Operator 发起受管清理/回滚。
- 重复取消或回滚：返回稳定状态，不重复破坏目标表。

## 7. 学习检查点

读完本流程后，应能回答：哪一步调用 LLM？哪一步产生外部副作用？为什么需要不可变版本？为什么 Commit 要重新校验指纹？为什么错误数据不能只记录日志而要分流到错误表？
