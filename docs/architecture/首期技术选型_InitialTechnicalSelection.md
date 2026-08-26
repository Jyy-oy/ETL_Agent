# ETL-Agent 首期技术选型

状态：首期开发基线（待第 8 节确认）

## 1. 选型原则

- 优先使用需求文档已经指定的组件，减少架构漂移。
- 控制面和数据面分离：控制面保存事实、生成计划和发出受管命令，SeaTunnel 承担海量搬运。
- 本地开发不部署 LLM。模型请求统一发送到远端百炼 OpenAI 兼容接口。
- 首期优先选择可在 Ubuntu 虚拟机 Docker 中稳定运行、方便备份和迁移的组件。
- 所有副作用必须经过 Harness 的 Prepare/Approve/Commit 和 Tool Broker，不在业务代码中绕过授权。

## 2. 首期组件矩阵

| 能力 | 选型 | 首期用途 | 首期部署位置 |
| --- | --- | --- | --- |
| API 控制面 | FastAPI + Uvicorn | REST API、健康检查、权限和业务编排 | 后续运行在 VM 容器或 VM Python 进程 |
| 数据访问 | SQLAlchemy 2 + Alembic | 业务表、事务和迁移 | VM PostgreSQL |
| 结构化数据库 | PostgreSQL 16 | 业务事实、Checkpoint、Outbox、审计账本 | VM Docker |
| 工作流 | LangGraph + PostgreSQL Checkpoint | 澄清、生成、门禁和恢复 | 控制面进程 |
| 缓存/队列 | Redis 7 + Celery 5 | Broker、结果后端、Replay Guard 短时状态 | VM Docker |
| 对象存储 | MinIO S3 API | 文件资产、制品大对象和 Benchmark 结果 | VM Docker |
| 密钥管理 | Vault KV v2 | 连接凭据和运行时 Secret | VM Docker（开发模式）；生产改用受管 Vault/KMS |
| LLM | 远端百炼 OpenAI 兼容 API | 需求解析、计划和 HOCON 候选生成 | 百炼平台，不在 VM 部署 |
| 数据面 | Apache SeaTunnel Zeta | 受管 ETL 作业执行 | VM Docker 可选 profile，生产独立部署 |
| 前端 | Vue 3 + Vite + TypeScript | 控制台、Studio、运行监控 | 开发机 Node 进程，后续静态容器 |
| 认证 | 首期 JWT；OIDC/SSO 待确认 | 本地账号和 API 访问令牌 | 控制面 |
| 签名 | Ed25519 + `cryptography` | Capability 签发/验签 | 控制面；私钥进入 Vault 或部署 Secret |
| 测试 | pytest + pytest-asyncio + respx | 单元、API 和外部调用契约测试 | 开发机/CI |
| 质量工具 | Ruff + Mypy + pre-commit | 格式、静态检查和提交门禁 | 开发机/CI |

## 3. 首期部署边界

### 3.1 可以部署在 Ubuntu 虚拟机 Docker 中

- PostgreSQL 16：`postgres` 服务，保存控制面状态。
- Redis 7：`redis` 服务，分库承载 Celery、结果和 Replay Guard。
- MinIO：`minio` 服务，保存文件和制品；`minio-init` 自动创建开发 bucket。
- Vault：`vault` 开发模式，仅用于本地验证 SecretProvider 合约。
- SeaTunnel Zeta：Compose 的 `data-plane` profile，可在资源足够时启动。
- 后续 FastAPI、Celery Worker/Beat、Vue 静态站点：实现源码后再加入应用服务镜像。

### 3.2 不在本地部署

- 百炼 LLM：通过 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL` 调用远端服务。
- 生产级 Vault、KMS/HSM、SSO、监控平台：本地只保留开发替身或接口契约。
- 业务源库和目标库：首条演示链路使用 `source-target` Compose profile 提供 MySQL 8.0.36 和 Doris 2.1.11 FE/BE 合成实例；不加入默认基础设施启动，避免未准备时占用大量资源。

## 4. 网络与地址约定

虚拟机地址为 `192.168.181.128`。Compose 默认将端口绑定到 `127.0.0.1`，这样不会直接暴露数据库和管理端口。需要从宿主机或其他机器访问时，在 VM 的 `.env` 设置：

```dotenv
COMPOSE_BIND_IP=0.0.0.0
```

同时使用 UFW 只放行可信网段。容器之间使用 Compose 服务名通信，例如 PostgreSQL 使用 `postgres:5432`、Redis 使用 `redis:6379`、MinIO 使用 `http://minio:9000`；VM 上直接运行的 FastAPI 则使用 `127.0.0.1` 或 VM 地址。

## 5. 关键取舍

- 首期不引入独立向量数据库：需求没有规定向量检索，Agent 先依靠元数据 Profile 和结构化 Checkpoint。
- 首期不把 Celery 状态放内存：Broker、结果和重放保护均使用 Redis，满足多 Worker 扩展前提。
- 首期不直接连接海量业务表：元数据探查走受管只读连接，数据搬运交给 SeaTunnel。
- Compose 只承载基础设施，不伪造尚不存在的 API/Worker 镜像；应用服务加入后再按同一网络和环境变量契约扩展。

## 6. 待确认决策

1. 百炼具体模型、API Base URL、超时、重试和数据出境限制。
2. PostgreSQL/Redis 是否使用现有托管服务，或长期使用 VM Docker。
3. Vault 是继续使用开发模式，还是接入独立 Vault/KMS。
4. 首期是否必须启动 SeaTunnel，以及 Zeta 版本、作业提交和日志采集方式。
5. 身份认证是否首期就接入企业 OIDC/LDAP/SSO。
6. 首条验收链路的源/目标数据库、数据规模和运行预算阈值。
