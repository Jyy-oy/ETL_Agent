# ETL-Agent 项目测试手册

本文给出从“确认环境”到“前端完整演练”的实际测试顺序。首期默认使用合成数据，不要求真实 MySQL、Doris 或百炼 API Key。

## 1. 测试分层

| 层级 | 目标 | 是否需要 VM |
| --- | --- | --- |
| 静态检查 | 格式、规则、类型、迁移和锁文件 | 否 |
| 单元/API 测试 | 验证权限、门禁、Capability、质量和 Benchmark | 否；使用 fake |
| 组件集成 | 验证 PostgreSQL、Redis、MinIO、Vault 和 Checkpoint | 是 |
| 数据面演练 | 合成 MySQL → SeaTunnel 2.3.10 → Doris 影子表/原子 Swap/Rollback；单元测试使用 FakeSource/Mock | 是；按需启动 profile |
| 浏览器验收 | 注册、项目、连接、Studio、审批、运行中心和 Benchmark | API + VM |

## 2. 启动开发环境

### 2.1 VM 基础设施

在 Ubuntu VM 的 Compose 目录执行：

```bash
docker compose up -d
docker compose ps
docker compose --profile data-plane up -d seatunnel
```

确认 PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel 为健康或运行状态。SeaTunnel 首次启动前必须完成配置目录复制，详见 [Ubuntu 虚拟机部署](../operations/Ubuntu虚拟机部署_LocalVMDeployment.md)。

### 2.2 Windows/PyCharm 控制面

在项目根目录确认 `.env` 的数据库、Redis、MinIO、Vault 和 SeaTunnel 地址指向 `192.168.181.128`，然后执行：

```powershell
uv run alembic upgrade head
uv run uvicorn etl_agent.main:app --loop etl_agent.main:selector_event_loop_factory --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm install
npm run dev
```

访问 [http://127.0.0.1:5173](http://127.0.0.1:5173)。如果页面提示“无法连接控制面 API”，先检查 `http://127.0.0.1:8000/health`。

## 3. 自动化测试

每次代码修改后执行：

```powershell
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run alembic check
uv run pytest -m "not integration"
cd frontend
npm run build
```

预期：当前首期非集成测试全部通过；集成测试默认不计入本地快速反馈。

## 4. 组件集成测试

PostgreSQL Checkpoint 测试需要 VM 可达，并显式打开开关：

```powershell
$env:CHECKPOINT_INTEGRATION_ENABLED="true"
uv run pytest tests/integration/test_m3_runtime.py -m integration -k checkpoint
```

真实百炼测试默认关闭。只有准备好非生产密钥、确认数据脱敏和出境范围后，才允许执行：

```powershell
$env:LLM_REAL_SMOKE_ENABLED="true"
uv run pytest tests/integration/test_m3_runtime.py -m integration -k real_bailian
```

该测试只发送虚拟 Profile，不应使用真实业务样本。

## 5. 浏览器最小验收

1. 打开控制台，选择“开发环境注册账号”；用户名至少 3 位，只允许字母、数字、下划线、点和短横线；密码至少 8 位。
2. 注册成功后，首次进入会显示“创建学习项目”；输入项目编码和名称并创建项目。
3. 进入“连接与 Profile”，登记合成 MySQL 连接。Windows/PyCharm 访问 VM 上的 MySQL 时，主机必须填写 `192.168.181.128`，不能填写 `127.0.0.1`；已有旧连接可点击“编辑”修正主机和 SecretRef 后再点击“测试”。首期可以只验证 API 表单和脱敏 SecretRef；真实探查前需在 Vault 写入对应 Secret。
4. 进入“Pipeline Studio”，创建草稿版本；先在“连接与 Profile”读取最近 Profile，再从源/目标下拉框选择 Profile，运行生成。没有真实百炼 Key 时使用后端单元测试或 Fake Provider，前端真实生成会因 LLM 配置不可用而失败，这是预期限制。
5. 进入“审批工作台”，验证 Preparation 状态和 Checker 槽。创建者不能审批自己的申请，服务端拒绝属于预期结果。
6. 进入“运行中心”，查看 ExecutionRun、质量、发布、回滚状态；取消和回滚只能登记 Outbox 动作，不在浏览器直接操作数据库。若目标 Profile 包含多张表，运行中心应显示中文编译错误和具体原因，不应创建 SeaTunnel 作业。
7. 进入“Benchmark”，使用固定参数运行 L0 和 L1。L0 的拒绝率应为 `0`，L1 应出现质量拒绝且 P0 拦截率为 `1.0`。

## 6. API 冒烟测试

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

注册、登录和创建项目：

```powershell
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$register = @{
  username = "tester_$suffix"
  display_name = "测试用户"
  password = "Test1234!"
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/register -Method Post -ContentType "application/json" -Body $register

$login = @{ username = "tester_$suffix"; password = "Test1234!" } | ConvertTo-Json
$token = (Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/login -Method Post -ContentType "application/json" -Body $login).access_token
$headers = @{ Authorization = "Bearer $token" }

$projectBody = @{ code = "learning_$suffix"; name = "ETL 学习项目" } | ConvertTo-Json
$project = Invoke-RestMethod http://127.0.0.1:8000/api/v1/projects -Method Post -Headers $headers -ContentType "application/json" -Body $projectBody
$project.id
```

运行确定性 Benchmark：

```powershell
$benchmarkBody = @{
  project_id = $project.id
  level = "l1"
  dataset_rows = 1000
  repeat = 2
  seed = 7
  artifact_digest = "synthetic-etl-plan-v1"
  policy_version = "pdp-v1"
  environment = "development"
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/benchmarks/run -Method Post -Headers $headers -ContentType "application/json" -Body $benchmarkBody
```

Benchmark 返回后验证历史摘要已落库：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/projects/$($project.id)/benchmarks" -Headers $headers
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/benchmarks/<benchmark_id>" -Headers $headers
```

预期：项目历史列表包含刚才的运行，单条查询的 `benchmark_id`、`dataset_digest` 和指标与 POST 响应一致；数据库只保存摘要，不保存业务样本。

## 7. 合成数据面演练

需要 MySQL/Doris 合成服务时，在 VM 执行：

```bash
docker compose --profile source-target up -d mysql doris-fe doris-be
```

在能够访问 VM MySQL `3306` 的开发环境写入固定订单数据：

```powershell
$env:MYSQL_HOST = "192.168.181.128"
$env:MYSQL_PORT = "3306"
uv run python scripts/seed_synthetic_mysql.py --rows 10000 --batch-size 1000
```

M5.5 已使用 VM 上的合成 MySQL、SeaTunnel 2.3.10 和 Doris 单机实例完成真实数据面验收：输入/输出各 10,000 行，质量通过后执行影子表原子 Swap，并验证受管 Rollback。真实业务库接入、生产级大规模压测和高可用部署属于后续生产化扩展。

真实合成数据面验收步骤：

1. 确认 MySQL/Doris/SeaTunnel/Vault 容器健康，且 `secret/etl-agent/mysql`、`secret/etl-agent/doris` 存在。
2. 运行 `scripts/seed_synthetic_mysql.py --rows 10000 --batch-size 1000`，登记 VM MySQL `192.168.181.128:3306` 和 Doris `192.168.181.128:9030` 连接。
3. 分别执行连接测试和 Profile 探查，源表选 `demo_orders`，目标表选 `orders_current`。
4. 运行百炼生成，完成 Prepare、Checker 审批和 Operator Commit。
5. 在运行中心等待 `succeeded/published`，确认输入/输出各 10,000、拒绝数为 0。
6. 对终态执行发起 Rollback，确认 `rollback=completed`、`publish=cleaned`，再查询 Doris 正式表仍有 10,000 行。

## 8. 失败场景清单

- 输入少于 8 位密码：前端应显示“密码至少需要 8 个字符”。
- 重复用户名：应显示“用户名已存在”，HTTP 状态为 `409`。
- 未启动 FastAPI：前端应显示“无法连接控制面 API”，而不是笼统的英文网络错误。
- Maker 自审批：应显示“制作人不能审批自己的申请”，HTTP 状态为 `403`。
- 版本未通过生成门禁直接 Prepare：应显示“版本尚未通过生成门禁”，HTTP 状态为 `409`。
- L1 Benchmark：应出现拒绝记录，且 P0 拦截率保持 `1.0`。

## 9. 测试证据

提交代码时保留测试命令、通过数量、迁移版本、Benchmark 参数和已知限制。报告中不得包含 API Key、密码、Capability 原文或未脱敏业务数据。
