# 首期开发环境与依赖

## 1. 环境基线

- Python 3.12 或更高版本。
- `uv` 管理 Python 环境、依赖和 `uv.lock`。
- Docker Compose 只负责本地依赖：PostgreSQL 16、Redis 7、MinIO、Vault；SeaTunnel 按需启动。
- LLM 通过远端百炼 OpenAI 兼容接口访问，不安装本地模型和 GPU 依赖。
- 前端预留 Vue 3 + Vite + TypeScript，尚未创建前端目录。

## 2. Python 依赖分层

根目录 `pyproject.toml` 已写入首期运行依赖，并由 `uv.lock` 锁定：

- Web/API：FastAPI、Uvicorn、Pydantic Settings、HTTPX、python-multipart、orjson。
- 数据与迁移：SQLAlchemy、asyncpg、psycopg、Alembic。
- 状态与队列：LangGraph、LangGraph PostgreSQL Checkpoint、Redis、Celery。
- 存储与 Secret：boto3（S3/MinIO）、HVAC（Vault）、cryptography、PyJWT。
- ETL 辅助：PyHOCON、JSON Schema、Polars、PyArrow、OpenPyXL、PyMySQL、oracledb、ClickHouse Connect。
- 工程质量：pytest、pytest-asyncio、pytest-cov、respx、Ruff、Mypy、pre-commit。

依赖版本采用“主版本上限 + 小版本下限”，避免无约束升级破坏 Harness 或连接器契约。新增依赖必须同步 `pyproject.toml`、`uv.lock` 和本文件的用途说明。

## 3. 安装步骤

在开发机或 Ubuntu VM 的源码目录执行：

```bash
uv python install 3.12
uv sync --dev
uv run python --version
uv run pytest
uv run ruff check .
uv run mypy src
```

当前仓库尚无 `src` 和 `tests` 实现目录，因此最后两条在源码创建前可能提示路径不存在；依赖解析和锁文件已准备好。

如果开发机尚未安装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec "$SHELL"
```

Windows 开发机可使用官方 PowerShell 安装方式，团队提交的依赖命令保持为 `uv sync --dev`。

## 4. 本地依赖服务

```bash
docker compose up -d
docker compose ps
```

应用从 VM 外部运行时使用 `.env` 中的 `localhost` 或 `192.168.181.128` 地址；应用加入 Compose 后改用服务名：

| 组件 | VM 主机进程 | Compose 内应用 |
| --- | --- | --- |
| PostgreSQL | `127.0.0.1:5432` | `postgres:5432` |
| Redis | `127.0.0.1:6379` | `redis:6379` |
| MinIO | `http://127.0.0.1:9000` | `http://minio:9000` |
| Vault | `http://127.0.0.1:8200` | `http://vault:8200` |

## 5. 环境变量管理

- `.env.example` 只保存变量名和开发占位值，可提交。
- `.env` 只保存本机值，已忽略，不得提交真实 API Key、JWT 密钥或 Ed25519 私钥。
- 百炼配置只填 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`；不要把 API Key 写入源码、测试固件或日志。
- `CAPABILITY_PRIVATE_KEY_PATH` 指向 `secrets/` 下的忽略文件；生产环境优先使用 Vault Transit/KMS，而不是挂载 PEM 文件。

## 6. 推荐目录约定

实现阶段采用以下边界：

```text
src/etl_agent/
  api/             # FastAPI 路由和错误边界
  application/     # 用例、事务和幂等入口
  domain/          # EtlPlan、状态、权限和契约
  harness/         # PDP、Capability、Broker、Replay Guard、Ledger
  infrastructure/  # PostgreSQL、Redis、MinIO、Vault、SeaTunnel 适配器
  workflows/       # LangGraph 图和 Checkpoint
  workers/         # Celery 任务，只调用受管用例
tests/
  unit/
  integration/
docs/
migrations/
seatunnel/config/
```

## 7. 开发检查顺序

每次提交前至少执行：

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv lock --check
```

涉及外部依赖的测试使用 respx 或 fake adapter；涉及 PostgreSQL/Redis/MinIO 的集成测试必须明确标记，并在 Compose 服务健康后运行。不要在单元测试中调用真实百炼或真实业务数据库。
