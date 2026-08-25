# ETL-Agent 代码阅读指南

当前仓库仍处于源码初始化阶段；本指南先定义目标目录和阅读顺序，避免未来代码增长后从路由文件盲目跳读。

## 1. 推荐目标目录

```text
src/etl_agent/
  api/             # HTTP 路由、认证、错误映射
  application/     # 用例、事务、幂等和权限上下文
  domain/          # 实体、值对象、状态机和端口接口
  harness/         # PDP、Capability、Broker、Replay Guard、Ledger
  workflows/       # LangGraph 图、节点和 Checkpoint
  infrastructure/  # PostgreSQL、Redis、MinIO、Vault、LLM、SeaTunnel 适配器
  workers/         # Celery 任务入口
tests/
  unit/
  integration/
  e2e/
```

## 2. 阅读顺序

### 第一步：配置和启动

先看配置模型、应用 lifespan、`/health` 和依赖注入，确认环境变量如何进入对象，以及哪些客户端是懒加载/预热的。

### 第二步：领域模型

阅读 PipelineVersion、Preparation、ApprovalRequest、ExecutionRun、OutboxEvent 和 AuditEvent。先理解状态和不变量，再看 API。

### 第三步：用例层

按 `generate → prepare → approve → commit → cancel/rollback` 顺序阅读。每个用例标出：输入、事务边界、权限检查、外部副作用和审计事件。

### 第四步：Workflow 和 LLM

从 Graph State、节点注册、Checkpoint、Provider Adapter 读起，确认模型输出如何被 Schema 验证、门禁和人工中断处理。

### 第五步：Harness 和 Worker

追踪 Outbox 创建、消费、Capability 验签、Replay Guard、Tool Broker 和 SeaTunnel Adapter。重点寻找是否存在绕过 Broker 的直接调用。

### 第六步：数据和前端

最后阅读连接器、QualityContract、RuntimeSupervision、前端状态和运行中心；将页面动作映射到 API 和领域状态，不只看组件样式。

## 3. 每个模块的观察问题

| 观察项 | 检查问题 |
| --- | --- |
| 边界 | 这个模块是否越过了自己的层级？ |
| 状态 | 状态保存在哪里？是否可跨进程恢复？ |
| 权限 | 是服务端校验还是只依赖前端按钮？ |
| 幂等 | 重试、重复请求、迟到事件会发生什么？ |
| Secret | 是否只使用引用？日志和异常是否脱敏？ |
| 外部调用 | 是否有超时、重试、熔断、错误映射和审计？ |
| 测试 | 正常、失败、超限、取消和恢复路径是否都有测试？ |

## 4. 不建议的阅读方式

- 不要先从 LLM Prompt 读起，再倒推业务规则；先看 Domain 和 Gate。
- 不要把 Celery 任务当成业务用例；任务只是受管命令的执行载体。
- 不要把 PostgreSQL JSON 字段当成没有约束的垃圾桶；关键查询字段应有明确模型和索引。
- 不要用进程内字典模拟生产级共享状态，除非测试明确标注为 fake。
