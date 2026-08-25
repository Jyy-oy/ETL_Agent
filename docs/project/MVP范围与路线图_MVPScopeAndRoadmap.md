# ETL-Agent MVP 范围与可扩展路线图

状态：首期范围基线

## 1. MVP 目标

MVP 不是临时 Demo，而是一个边界清楚、可以演进的最小生产闭环。首期只验证一条受管链路：

```text
MySQL 源 → 只读 Metadata Profile → 百炼生成 EtlPlan/HOCON
→ 门禁 → 不可变版本 → 双 Checker 审批
→ Operator Commit → Celery/SeaTunnel → Doris 影子表
→ 质量报告 → 原子 Swap / 回滚
```

## 2. MVP 必须交付

| 优先级 | 能力 | 验收证据 |
| --- | --- | --- |
| P0 | 本地 JWT、项目、成员、五类角色和职责分离 | 自批/职责混用被服务端拒绝 |
| P0 | MySQL/Doris 连接登记、测试和 SecretRef | 密码不入业务表和日志 |
| P0 | 只读 Schema/字段/近似统计/脱敏 Profile | Profile 可复用且有摘要 |
| P0 | LangGraph 生成、缺参中断、回答恢复 | Checkpoint 跨请求恢复 |
| P0 | EtlPlan/HOCON 结构化校验和确定性门禁 | 非法配置不能冻结 |
| P0 | SHA-256 不可变 PipelineVersion | 内容变更生成新版本 |
| P0 | Prepare/Approve/Commit、Capability、Replay Guard | 指纹漂移和重放被拒绝 |
| P0 | Outbox + Celery + SeaTunnel 受管执行 | 事务事实和投递可重试 |
| P0 | 影子表、错误表、质量契约、原子 Swap | 通过/失败路径均可验证 |
| P0 | 运行监督和受管回滚 | 超预算可取消、清理并审计 |
| P1 | Vue 连接/Profile、Studio、审批、运行中心 | 四条关键用户路径可用 |
| P1 | L0/L1 Benchmark | 结果关联版本和策略 |

## 3. 明确不放入 MVP

- 全部异构数据库和文件格式同时交付。
- 生产级 OIDC/LDAP/SSO、KMS/HSM、Vault HA。
- 多区域部署、Redis Cluster、PostgreSQL 读写分离。
- 任意自然语言直接执行、自动审批或模型自主扩大资源范围。
- 独立向量数据库和复杂 RAG 平台。
- 实时日志 WebSocket（首期允许轮询）。
- 完整安全进化自动上线闭环。

不放入 MVP 不代表取消，而是通过接口和数据模型预留，不在首期增加不可验证的复杂度。

## 4. 扩展路线

### R1：连接器和 Provider 扩展

通过 `SourceConnector`、`TargetConnector`、`LLMProvider` 注册机制添加 PostgreSQL、Oracle、ClickHouse、Parquet、REST、DeepSeek、Qwen 和企业 LLM Gateway。

### R2：平台化部署

将控制面 API、Worker、Beat、SeaTunnel、Frontend 拆成独立镜像和 Helm Chart；引入 OIDC、托管 PostgreSQL/Redis/对象存储、Vault/KMS 和集中日志指标。

### R3：高可用和规模化

按租户和项目分片/分区，拆分 Checkpoint 数据库，增加 Redis Cluster、Outbox 分区、Worker 队列隔离、SeaTunnel 集群和容量自动扩展。

### R4：治理和安全进化

增加策略包版本、Prompt 评审、影子授权、L2 真实链路评测、模型成本预算、供应链签名和合规报表。

## 5. 每阶段的扩展保护栏

- 数据库迁移保持向后兼容，禁止把 MVP 临时字段变成不可替换的公共契约。
- 连接器、Provider、执行引擎必须实现端口接口，不把具体名称写入领域核心。
- Prompt、策略、模型和 Benchmark 都有版本号和回滚指针。
- 所有异步事件使用稳定事件 ID，未来可迁移到消息总线而不改变业务状态。
- API 从 `/api/v1` 开始，破坏性变更通过 `/api/v2` 或兼容期完成。
