# ETL-Agent 项目使用手册

本文面向项目开发、学习和演示使用者，描述如何启动 ETL-Agent、配置远端百炼、使用 Vue 控制台完成一条合成 ETL 流程，并通过模拟故障验证关键控制面能力。

本文中的数据均为学习项目数据，不要求真实业务 MySQL、Doris 或生产级密钥。涉及真实百炼调用时，只能使用脱敏 Profile 和非生产 API Key。

## 1. 当前运行边界

| 组件 | 当前运行位置 | 说明 |
| --- | --- | --- |
| Vue 控制台 | Windows/PyCharm，`127.0.0.1:5173` | 浏览器入口，Vite 将 `/api` 转发到本机 FastAPI |
| FastAPI 控制面 | Windows/PyCharm，`127.0.0.1:8000` | 认证、连接、Profile、生成、审批和运行事实 |
| PostgreSQL 16 | Ubuntu VM `192.168.181.128:5432` | 业务事实、Checkpoint、Outbox 和审计账本 |
| Redis 7 | Ubuntu VM `192.168.181.128:6379` | Celery、结果和短时 Replay Guard |
| MinIO | Ubuntu VM `192.168.181.128:9000` | 文件资产和制品对象 |
| Vault | Ubuntu VM `192.168.181.128:8200` | 连接凭据，业务表只保存 SecretRef |
| 合成 MySQL | Ubuntu VM `192.168.181.128:3306` | 学习用源库，数据可由脚本生成 |
| SeaTunnel 2.3.10 | Ubuntu VM，按 profile 启动 | 真实合成 MySQL → Doris 影子表数据面；单元测试另有 FakeSource/Mock |
| 百炼 LLM | 远端 HTTPS | 只生成结构化候选，不在本机或 VM 部署模型 |

地址要区分：`127.0.0.1:5173/8000` 是 Windows 的前端和控制面；连接表单里的 MySQL 主机是数据库地址，应填写 `192.168.181.128`。已有旧连接若显示 `127.0.0.1`，请使用“编辑”改为 VM 地址。

## 2. 百炼配置确认

当前 `.env` 已检测到以下配置项：

| 配置项 | 当前状态 | 用途 |
| --- | --- | --- |
| `LLM_PROVIDER` | `openai_compatible` | 使用 OpenAI 兼容适配器 |
| `LLM_BASE_URL` | 百炼兼容接口地址 | 请求入口，末尾不需要重复 `/chat/completions` |
| `LLM_MODEL` | 已填写模型名 | 例如当前开发配置中的 Qwen 模型 |
| `LLM_API_KEY` | 已填写，本文不显示 | 访问百炼的密钥，禁止提交 Git 或写入日志 |
| `LLM_REAL_SMOKE_ENABLED` | `false` | 是否允许集成测试实际调用百炼，不影响控制面正常启动 |

`GET /health` 中 LLM 显示 `configured` 只代表三项必要配置非空，不代表远端调用已经成功。真实调用需使用下面的“百炼真实烟测”。

### 2.1 百炼真实烟测

该测试只发送虚拟 Profile，验证网络、鉴权、模型响应和 JSON 解析，不发送真实业务数据。

```powershell
# 在项目根目录执行
$env:LLM_REAL_SMOKE_ENABLED = "true"
uv run pytest tests/integration/test_m3_runtime.py -m integration -k real_bailian

# 测试完成后恢复默认关闭状态
$env:LLM_REAL_SMOKE_ENABLED = "false"
```

预期结果：测试通过，返回结构化 JSON 对象和 64 位响应摘要。若出现 `LLM_REQUEST_REJECTED`，优先检查模型名称、百炼账号权限、地域 Base URL 和额度；若出现 `LLM_TIMEOUT` 或 `LLM_NETWORK_ERROR`，检查代理、防火墙和网络连通性。不要把 API Key 粘贴到 Issue、日志或截图中。

## 2.2 全流程产物与上下游

系统把一次 ETL 请求拆成可审查的阶段。每个阶段只消费上游已经确认的事实，并把产物交给下一阶段：

| 阶段 | 上游输入 | 本阶段产物 | 下游用途 |
| --- | --- | --- | --- |
| 项目与连接 | 用户、项目和 SecretRef | 项目成员关系、连接登记 | Profile 探查按项目和凭据边界执行 |
| Profile | 只读连接、表名范围 | 字段类型、主键、行数估计、脱敏样本、指纹 | Agent 判断字段是否存在、类型是否兼容 |
| 需求与澄清 | 业务描述、源/目标 Profile | 完整需求参数和澄清答案 | 生成图复用同一 `thread_id` 继续运行 |
| Agent 生成 | 需求上下文、Profile 摘要、JSON Schema | EtlPlan、HOCON、校验问题、响应摘要 | 确定性门禁和版本冻结 |
| Prepare | 已通过门禁的不可变版本 | 风险级别、审批槽、冻结 Profile 指纹、过期时间 | Checker 只审批这组冻结事实 |
| Approve | Preparation 和 Checker 职责槽 | 每个审批槽的决定 | 全部通过后才允许 Operator Commit |
| Commit | 审批事实、制品摘要和权限 | ExecutionRun、单次 Capability、Outbox 事件 | Worker 验签后投递 SeaTunnel |
| 数据面与监督 | ExecutionRun、SeaTunnel 作业 | 输入/输出/拒绝指标、影子表、错误表、质量结论 | 达标原子 Swap，失败清理或回滚 |
| 审计与 Benchmark | 运行事件和固定测试参数 | Evidence Ledger、Benchmark 报告 | 复盘、回归和后续策略评审 |

Pipeline Studio 的 Agent 面板会把上述生成阶段显示为真实节点轨迹：每一行都标出“上游输入、本阶段动作、已产出、下游用途”。“已完成”只表示后端 `AgentRun.node_trace` 已落库，不是前端估算的百分比；候选 EtlPlan/HOCON 和审查对话可在 Prepare 前检查。

### 2.3 当前能力边界

当前真实数据面首期支持单源表到单目标表、字段直接映射/重命名、白名单 `CAST` 和数值比较 `FILTER`，并完成影子表、拒绝行错误表、质量监督、原子 Swap/Rollback。`mask`、`fill_null`、CDC/增量水位执行、Join、聚合、多表编排、文件/API 数据面和完整异构连接器仍属于后续扩展；Agent 可以识别并澄清这些需求，但不会把未实现能力伪装成可执行作业。

## 3. 启动项目

### 3.1 启动 Ubuntu VM 基础设施

在 Ubuntu VM 的 `/home/oyjy/vibecoding_docker` 执行：

```bash
docker compose up -d
docker compose ps
```

需要合成 MySQL 和 Doris profile 时再执行：

```bash
docker compose --profile source-target up -d mysql doris-fe doris-be
docker compose --profile data-plane up -d seatunnel
```

### 3.2 启动 Windows 控制面

在项目根目录执行数据库迁移和 API：

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

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 5
```

PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel 应显示 `ok/ready`；LLM 应显示 `configured`。

### 3.3 启动 Celery Worker 和 Beat

异步 Agent 生成、Outbox 投递和运行监督由独立的 Celery 进程执行，FastAPI 不会代替它们运行。在项目根目录分别打开两个终端：

```powershell
# 终端 A：消费 Agent/Outbox/监督任务
uv run celery -A etl_agent.workers.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

```powershell
# 终端 B：按周期发布待处理 Outbox
uv run celery -A etl_agent.workers.celery_app.celery_app beat --loglevel=INFO
```

Windows Worker 必须使用仓库中的最新代码；修改 `src/etl_agent/workers/tasks.py` 后需要停止并重新执行上面的 Worker 命令。生成失败时优先查看 Worker 终端，FastAPI 终端通常只会看到 `202 Accepted` 和 AgentRun 轮询请求；控制台中的 `AgentRun.error_code`、`error_detail` 和 `node_trace` 才是任务结果。

## 4. 推荐演示数据

启动合成 MySQL 后，在 Windows 项目根目录执行：

```powershell
$env:MYSQL_HOST = "192.168.181.128"
$env:MYSQL_PORT = "3306"
uv run python scripts/seed_synthetic_mysql.py --rows 10000 --batch-size 1000
```

脚本生成确定性的订单类数据，便于重复 Profile、质量和 Benchmark 测试。学习项目不使用真实业务数据，但 VM 上的 Doris 目标表和 SeaTunnel 作业是真实运行的。

## 5. 控制台主流程

下表是建议按顺序执行的最小闭环。每一步都标注了正在验证的功能。

| 步骤 | 页面操作 | 测试的功能 | 预期结果 |
| --- | --- | --- | --- |
| 1 | 选择“开发环境注册账号”，注册并登录 | 用户注册、密码校验、JWT 登录 | 进入控制台并保存访问令牌 |
| 2 | 首次进入创建“ETL 学习项目” | 项目创建、初始 Maker/Operator 职责 | 项目出现在顶部项目选择器，并显示“项目名称（项目编码）” |
| 3 | 进入“连接与 Profile”，登记或编辑合成 MySQL | 连接元数据登记、SecretRef 约束、VM 地址 | 主机为 `192.168.181.128:3306`，不保存密码明文 |
| 4 | 点击连接“测试” | Vault SecretProvider、MySQL `SELECT 1` 只读探针 | 显示“连接成功且只读探针通过” |
| 5 | 点击“探查” | Schema、近似行数、脱敏样本和 Profile 指纹 | 生成 Profile，页面显示行数和 Profile ID |
| 6 | Pipeline Studio 创建草稿版本 | Pipeline 和不可变版本基础 | 创建草稿版本，状态为草稿 |
| 7 | 在 Studio 中从下拉框选择源/目标 Profile，填写业务需求并点击“运行生成” | LangGraph、百炼结构化候选、确定性门禁 | Studio 显示 Agent 节点实时进度；若缺参数出现澄清对话，完成后版本变为已就绪/不可变 |
| 8 | 查看 Agent 生成面板，必要时提交澄清答案，再点击“Prepare” | AgentRun 持久化、Checkpoint 恢复、风险评估和 Profile 指纹冻结 | 只有生成完成才可 Prepare；页面明确风险级别和所需 Checker |
| 9 | 审批工作台查看并处理审批 | 四眼原则、Maker 自批拦截 | 未完成审批不能 Commit |
| 10 | 审批完成后点击“Commit” | Capability、指纹复核、ExecutionRun、Outbox | 生成受管执行记录，不直接操作目标库 |
| 11 | 运行中心查看状态并测试取消/回滚 | Worker、监督、质量分流和回滚入口 | 状态和动作可追踪，重复请求保持幂等 |
| 12 | Benchmark 页面运行 L0/L1 | 确定性数据质量基线和故障注入 | L0 通过；L1 出现质量拒绝和 P0 拦截 |

## 6. 模拟测试用例

### U-001：环境和依赖健康

- 测试功能：配置加载、依赖健康探针、请求 ID。
- 前置条件：VM Compose 已启动，FastAPI 已启动。
- 操作：访问 `http://127.0.0.1:8000/health`。
- 预期：HTTP `200`；PostgreSQL、Redis、MinIO、Vault、SeaTunnel 为 `ok/ready`；LLM 为 `configured`。
- 失败排查：若提示无法连接 API，检查 FastAPI 进程；若某个依赖为 `not_ready`，检查 VM 容器日志和 Windows 到 VM 的端口连通性。

### U-002：注册、登录和项目隔离

- 测试功能：开发环境注册、密码哈希、JWT、项目成员边界。
- 操作：注册用户名 `tester_demo`，密码使用不少于 8 位的临时密码；登录后创建项目编码 `etl_learning_demo`。
- 预期：注册后自动登录；项目总览显示 1 个项目、0 个连接；创建者拥有 Maker 和 Operator。
- 负例：再次注册同名用户，页面应显示“用户名已存在”；输入短密码，页面应显示中文长度校验。
- 注册 Checker：退出当前账号，重新选择“开发环境注册账号”，在“注册后的项目职责”选择 `Checker 1` 或 `Checker 2`，填写已存在的项目编码；注册并登录后，该账号会直接看到项目并进入审批工作台。

### U-003：合成 MySQL 连接和 Profile

- 测试功能：Vault KV v2、SecretRef、MySQL 只读探针、Profile 脱敏。
- 前置条件：Vault 中存在 `secret/data/etl-agent/mysql`，且包含 `username`、`password`、`database` 三个字段；合成 MySQL 已启动。
- 操作：填写 `192.168.181.128`、`3306`、`etl_demo`、`etl_demo` 和 `secret/data/etl-agent/mysql`，登记连接；依次点击“测试”和“探查”。如果 Doris 连接包含多张业务表，在连接行的“表名，可逗号分隔”输入框填写目标表，例如 `orders_current`，再点击“探查”，为真实数据面生成单表 Profile。
- 预期：连接测试通过；Profile 包含字段结构、估算行数、脱敏样本和指纹；页面不显示密码。
- 负例 A：把主机改成 `127.0.0.1`，应显示连接失败；这表示请求到了 Windows 本机，而不是 VM MySQL。
- 负例 B：把 SecretRef 改成不存在的路径，连接测试显示“SecretRef 无法解析或凭据不完整”，Profile 显示“连接凭据暂时不可用”。

### U-004：Pipeline 草稿和百炼生成

- 测试功能：PipelineVersion、LangGraph 工作流、远端百炼结构化输出、Schema/HOCON 门禁。
- 操作：先在“连接与 Profile”点击“读取最近 Profile”或完成一次“探查”，再进入 Studio 创建 `orders_sync` 草稿；业务需求填写“同步订单到目标表，保留 id、amount 和 updated_at”；源 Profile 和目标 Profile 从下拉框选择。学习阶段没有 Doris Profile 时，可以暂时选择同一合成 MySQL Profile 演示控制面流程，并在记录中标注为模拟目标。
- 预期：Studio 的 Agent 面板按顺序显示“意图解析 → Profile 上下文整理 → LLM 候选生成 → 结构化校验 → HOCON 编译 → 确定性门禁”；百炼返回候选后，服务端完成 Pydantic、字段引用、预算和 HOCON 校验；通过后版本变为 `ready` 且 `immutable=true`。
- 澄清场景：业务需求写“做增量同步”但不指定增量字段时，状态显示“等待澄清”，在对话框填写 `incremental_field` 后点击“提交澄清并继续”；服务端复用原 AgentRun 的 Checkpoint，不会新建一条无关任务。
- 无百炼调用时：使用 `uv run pytest -m "not integration"` 验证 Fake Provider 和全部确定性门禁；前端真实生成失败属于未启用真实 Provider 的预期结果。
- 负例：未读取 Profile 时“运行生成”按钮应禁用；后端收到不存在的 Profile UUID 时应显示“Profile 不存在或不属于当前项目”；非法候选不能冻结版本。

### U-005：Prepare 和审批

- 测试功能：PDP 风险评级、Preparation 不可变事实、Checker 槽和 Maker 自批拦截。
- 操作：对已就绪版本点击“Prepare”，查看风险级别、策略版本、资源范围和审批槽。
- 预期：Prepare 只写入冻结事实，不访问 MySQL/Doris，不产生外部写操作；风险较高时出现 `checker_1`、`checker_2` 审批槽。
- 负例：使用创建者账号点击自己的批准按钮，服务端应拒绝并显示“制作人不能审批自己的申请”。
- 完整双人演示：使用登录页分别注册两个账号，分别选择 `Checker 1`、`Checker 2` 并填写项目编码；两名账号登录后分别处理对应审批槽。Checker 不得同时拥有 Maker/Operator。

### U-006：Commit、运行监督和回滚

- 测试功能：Commit 指纹复核、Ed25519 Capability、Redis Replay Guard、Transactional Outbox、ExecutionRun。
- 操作：全部必需 Checker 批准后，在审批工作台点击“Commit”；进入运行中心查看执行状态；对排队/运行中的记录点击“取消”，对终态记录点击“回滚”。
- 预期：Commit 创建 ExecutionRun 和 Outbox 事实；取消、清理、Swap、回滚均登记受管动作，不能由浏览器直接写目标表；重复点击不会创建重复动作。
- 当前边界：学习数据面已支持真实合成 MySQL → SeaTunnel → Doris 影子表 → 原子 Swap/Rollback；真实数据面首期要求源、目标 Profile 各只包含一张表。若运行中心显示 `OUTBOX_DISPATCH_FAILED` 且详情为“目标 Profile 只包含一张表”，请按 U-003 重新探查单表 Profile，创建新版本后重新生成、Prepare 和审批；该错误发生在 SeaTunnel 提交前，不会修改目标表。

### U-007：L0/L1 Benchmark

- 测试功能：确定性合成数据、质量契约、故障注入、P0 拦截率和报告摘要。
- 操作：进入 Benchmark，先运行 `L0 基线`，再运行 `L1 故障注入`；建议数据行数 `1000`、重复次数 `1`、随机种子 `20260826`。
- 预期：L0 拒绝率为 `0`；L1 出现质量拒绝，P0 拦截率为 `1.0`；相同参数重复运行时数据摘要和关键指标一致；报告会出现在 Benchmark 历史列表。
- 脚本方式：

  ```powershell
  uv run python scripts/run_benchmark.py --project-id <项目UUID> --level l0 --rows 1000 --seed 20260826
  uv run python scripts/run_benchmark.py --project-id <项目UUID> --level l1 --rows 1000 --seed 20260826
  ```

### U-008：前端中文错误提示

- 测试功能：统一错误结构、稳定错误码和前端中文化。
- 操作：停止 FastAPI 后刷新页面；输入短密码；重复注册用户名；访问错误资源；使用错误 SecretRef 测试连接。
- 预期：页面分别显示“无法连接控制面 API”“密码至少需要 8 个字符”“用户名已存在”“请求的资源不存在”“SecretRef 无法解析或凭据不完整”等中文提示，不展示 Python 堆栈、密码或 API Key。

## 7. 常用 API 冒烟命令

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 5
```

查看 OpenAPI 是否已加载连接编辑接口：

```powershell
$openapi = Invoke-RestMethod http://127.0.0.1:8000/openapi.json
$openapi.paths.'/api/v1/connections/{connection_id}'
```

如果没有看到 `put`，说明 FastAPI 仍运行旧进程，需要停止旧进程后重新启动。

## 8. 每次开发后的自动化验证

```powershell
uv lock --check
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src
uv run alembic check
uv run pytest -m "not integration"
cd frontend
npm run build
```

真实百炼烟测和 VM Checkpoint 集成测试默认关闭，必须在确认非生产数据、网络和费用边界后显式打开。测试报告只记录通过数量、版本、参数和错误码，不记录 API Key、密码、Capability 原文或未脱敏业务数据。

## 9. 已知限制和后续扩展

- 首期目标库使用 VM Doris 单机实例和合成数据，真实业务库连接、生产连接器扩展和高可用部署属于后续阶段。
- LLM 生成依赖远端百炼网络、模型权限和配额；Fake Provider 负责离线确定性测试。
- 当前 Benchmark 报告会写入 PostgreSQL `benchmark_runs`，控制台可查询最近历史摘要；实时推送仍待后续扩展。
- VM Docker 是单机开发环境，不等同于生产高可用部署；生产环境必须替换开发 Token、密码、JWT 密钥和 Vault 方案。
