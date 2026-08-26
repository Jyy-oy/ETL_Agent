# ETL-Agent 测试与质量策略

状态：MVP 测试基线

## 1. 质量目标

测试不仅验证“能运行”，还必须验证权限、不可变版本、受管执行、失败恢复和审计证据。所有关键业务路径至少有一个确定性自动化测试和一个集成测试证据。

## 2. 测试层次

| 层次 | 范围 | 运行方式 |
| --- | --- | --- |
| 静态检查 | Ruff、Mypy、依赖锁、配置 Schema | 每次提交 |
| 单元测试 | Domain 值对象、PDP、门禁、摘要、状态迁移、哈希链 | 无外部服务，快速执行 |
| API 测试 | 鉴权、租户隔离、错误结构、幂等、Prepare/Approve/Commit | FastAPI TestClient + fake adapter |
| 组件集成 | PostgreSQL 事务/迁移、Redis Replay Guard、MinIO、Vault | Docker Compose |
| 工作流集成 | LangGraph 中断/恢复、Checkpoint、结构化模型输出 | fake LLM + PostgreSQL |
| 数据面集成 | Celery、Outbox、SeaTunnel 状态、影子表、错误表、Swap、回滚 | 单元测试使用 FakeSource/Mock；VM 验收使用合成 MySQL → SeaTunnel → Doris |
| E2E | 合成 MySQL → SeaTunnel → Doris 影子表/原子 Swap/Rollback 和前端关键路径 | Ubuntu VM Compose + Windows 控制面 |
| Benchmark | L0 静态注入、L1 模拟故障、合成数据链路；真实 L2 可选 | 固定数据集和策略版本 |

## 3. 必测场景

### 安全与权限

- 用户访问非成员项目被拒绝。
- Maker 不能批准自己的 Preparation。
- 同一用户不能同时满足两个高风险 Checker 槽。
- 未签名、错误主体、错误工具、错误环境、错误制品摘要的 Capability 被拒绝。
- Capability 过期和重复消费均被拒绝。

### 生成与门禁

- 缺少源表/目标表/关键字段时 LangGraph 中断，提交回答后从 Checkpoint 恢复。
- LLM 返回非法 JSON、未知字段、错误枚举或不可编译 HOCON 时不能冻结版本。
- Schema 漂移、超预算、无权限连接器和禁止操作被确定性门禁拒绝。
- 自动修复次数有上限，超限后转人工。

### 事务与执行

- Commit 事务失败时不能产生孤立 ExecutionRun 或 Outbox。
- Outbox 重试不会重复创建外部作业。
- SeaTunnel 失败、取消和迟到状态不会覆盖已完成终态。
- 质量规则不通过时错误数据进入错误表，正式表不被替换。
- 超过行数、字节、时长、放大比或拒绝率预算时触发预期动作。
- 回滚重复调用保持幂等，影子表和临时资源最终清理。

### 安全与隐私

- 日志、API 响应、AgentRun 和错误报告中不出现 Secret 或未脱敏样本。
- 百炼请求只包含允许字段；Prompt 注入不能扩大工具或资源权限。
- 审计哈希链可验证，修改历史载荷会被发现。

## 4. 测试数据策略

- 单元测试使用固定最小 Fixture，不连接真实业务库。
- 集成测试使用合成或脱敏数据，所有数据源账号默认只读。
- Benchmark 数据集版本化，记录数据集摘要、模型、Prompt、策略和运行环境。
- 学习验收使用固定合成 MySQL 数据，并在 VM 真实运行 SeaTunnel 2.3.10 和 Doris 影子表、原子 Swap/Rollback；单元测试仍使用 FakeSource/Mock 隔离外部依赖。生产业务库、真实 L2 链路不是首期必需，若启用必须使用独立租户、独立连接和可清理的目标表。
- 测试结束清理对象存储、影子表、错误表和 Vault 临时 Secret。

## 5. 质量门禁

合并前：

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -m "not integration"
uv lock --check
```

发布前还必须通过：迁移升级/回滚、Compose 集成、Harness 安全、E2E 主流程、备份恢复和 Benchmark 基线。P0/P1 缺陷不得带入生产；P2/P3 必须有负责人和目标版本。

## 6. 测试证据

每次发布保留提交 SHA、制品摘要、测试报告、迁移版本、环境配置摘要、Benchmark 结果和已知风险。报告不得包含 API Key、密码或未脱敏业务数据。

## 7. 当前自动化证据

`tests/unit/test_m3_generation.py` 已覆盖合法候选、缺参人工中断、未知字段/错误枚举、预算越权、非法 HOCON、有限修复、预算裁剪、Prompt 大小上限、瞬态重试和 OpenAI-compatible Provider。`tests/unit/test_m5_quality.py` 覆盖质量拒绝、必填字段缺失和预算硬中断；`tests/unit/test_m5_engine.py` 覆盖提交、SeaTunnel 原生状态/指标转换、取消、清理、原子切换、回滚和错误脱敏；`tests/unit/test_m6_benchmark.py` 覆盖 L0 重复性、L1 故障注入、历史报告转换和 P0 拦截率；前端 `npm run build` 验证 Vue/TypeScript 产物。`tests/integration/test_m3_runtime.py` 提供 VM Checkpoint 和真实百炼 smoke test，默认通过配置开关跳过。当前 M6/M6.1 后端全量非集成测试为 56 项通过、2 项显式跳过；Ruff、Mypy、Alembic check 和 `uv lock --check` 均通过。SeaTunnel 2.3.10 已用 VM 合成 MySQL → Doris 影子表 → 原子 Swap/Rollback 验收；真实业务库、生产压测和百炼调用属于后续扩展。
