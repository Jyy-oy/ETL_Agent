# 需求 A-F 真实验收记录

## 1. 记录说明

仓库中没有单独落盘的“需求 A-F”编号文件，本记录按项目测试手册的 U-001～U-008 验收项，将 A-F 归并为六段用户链路。测试使用当前代码、已启动的 Windows 控制面和 Ubuntu 虚拟机真实依赖，不把单元测试结果冒充真实链路结果。

## 2. 测试环境

| 项目 | 实际环境 |
| --- | --- |
| 控制面 | Windows 本地 FastAPI `127.0.0.1:8000`、Celery Worker、Celery Beat |
| 前端 | Vue/Vite `127.0.0.1:5173` |
| 虚拟机 | Ubuntu `192.168.181.128` |
| 基础服务 | PostgreSQL 16、Redis 7.4、MinIO、Vault、SeaTunnel 2.3.10 |
| 数据面 | 合成 MySQL → SeaTunnel → Doris |
| 模型 | 远端百炼 OpenAI-compatible Provider；仅记录模型调用证据，不记录密钥和业务样本 |

## 3. A-F 结果

| 需求 | 覆盖功能 | 真实结果 | 状态 |
| --- | --- | --- | --- |
| A | 环境健康、注册登录、项目创建 | `/health` 依赖均为 `ok/ready`；注册、登录、项目创建成功 | 通过 |
| B | MySQL/Doris 连接、Vault SecretRef、只读 Profile | VM 地址连接测试和单表探查成功；Profile 保存字段、指纹和脱敏摘要 | 通过 |
| C | 自然语言生成、澄清恢复、百炼调用、候选审查 | 订单全量需求真实调用百炼并完成 EtlPlan/HOCON 门禁；缺增量字段时暂停，提交答案后复用同一 Checkpoint；完成后支持审查对话 | 通过 |
| D | Prepare、风险评估、双 Checker、Commit | Maker 自审批被 `SELF_APPROVAL_FORBIDDEN` 拦截；两名独立 Checker 审批后 Commit 创建 ExecutionRun/Outbox | 通过 |
| E | SeaTunnel 执行、质量分流、错误表、影子表、原子 Swap、Rollback | 输入 10,000；输出 10,000；注入 5 条 `amount<=0` 后错误表真实写入 5 条；质量通过、Doris 发布成功、回滚完成 | 通过 |
| F | 运行可观测性、幂等、Benchmark 和中文错误 | 节点进度、Worker 结构化日志、重复 Commit/监督幂等和 L0/L1 结果可查；前端稳定错误码已中文化 | 通过 |

## 4. 本轮发现与修复

### 4.1 FILTER 兼容问题

部分模型把过滤参数写为历史字段 `expression`，运行时只读取 `condition`，会造成过滤条件丢失。编译器现兼容两种格式，提示词统一要求 `condition`。

### 4.2 重命名候选结构不稳定

模型可能把字段重命名写成嵌套 `TransformRule`。校验层现在只对结构无歧义的 `rename/cast` 做归一化，未知结构仍拒绝。

### 4.3 拒绝行没有质量证据

旧流程只查询合规行，拒绝行不会进入错误表。现在根据相同条件生成反向查询，写入 Doris 错误表并更新 `rejected_records`。

### 4.4 未实现复杂能力触发无效修复

真实测试发现“增量 + 脱敏”在澄清后会先进入一次 Repair。现已在生成门禁识别 `增量/CDC/脱敏/mask/fill_null/Join/聚合` 等能力，直接返回 `UNSUPPORTED_DATA_PLANE_FEATURE`（或转换级错误），不冻结版本、不再触发 Repair；新增回归测试确认只产生一次候选调用。

### 4.5 必填字段为 NULL 的三值逻辑

错误查询补充必填字段 `IS NOT NULL` 条件，避免 NULL 行既不进入目标表，也不进入错误表。

### 4.6 过滤字段为 NULL 的三值逻辑

过滤反向查询补充 `IS NULL` 分支，确保过滤表达式结果为 UNKNOWN 的记录也进入错误表，避免质量拒绝数据无证据留存。

## 5. 当前明确边界

- 已真实打通的是单源表、单目标表、直接映射/重命名/白名单 CAST/数值 FILTER。
- 增量水位、CDC、脱敏、空值填充、Join、聚合、调度和多表编排仍未实现；系统会在生成门禁阶段明确拒绝，而不是静默降级。
- 真实业务 MySQL/Doris、高可用部署和生产级压测不在本轮范围内，演示使用合成数据。

## 6. 自动化验证证据

本轮代码修复后执行：

```text
73 passed, 2 skipped
ruff format --check：通过
ruff check：通过
mypy src：通过
alembic check：通过
npm run build：通过
```

已知提示仅为依赖弃用警告和 Windows 工作区 `.pytest_cache` 写权限警告，不影响测试结果。
