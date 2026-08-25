# ADR-0001：首期控制面与本地基础设施技术基线

状态：Proposed

日期：2026-08-25

## 背景

需求要求同时具备 FastAPI、LangGraph、Celery、PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel 能力；当前项目刚建立，尚无应用源码，开发环境是 Ubuntu 虚拟机，LLM 必须调用远端百炼。

## 决策

1. 使用 Python 3.12 + uv 管理控制面依赖，并提交 `uv.lock`。
2. 使用 PostgreSQL 16 作为业务数据库、LangGraph Checkpoint、Outbox 和 Evidence Ledger 的持久化基础。
3. 使用 Redis 7 承担 Celery Broker、结果后端和短时 Replay Guard，按逻辑库隔离用途。
4. 使用 MinIO S3 API 保存文件资产、制品大对象和 Benchmark 结果。
5. 使用 Vault KV v2 抽象 SecretProvider；Compose 只运行开发模式 Vault，不代表生产安全配置。
6. 使用 Apache SeaTunnel Zeta 作为数据面，和控制面通过受管命令交互。
7. 使用远端百炼 OpenAI 兼容接口，不在本地或服务器部署 LLM。
8. 使用根目录 Compose 启动基础设施；SeaTunnel 使用 `data-plane` profile，应用镜像等源码形成后再加入。

## 未采用方案

- 不在首期引入向量数据库：原始需求没有向量检索，先以结构化 Profile 和 Checkpoint 验证核心闭环。
- 不默认部署 MySQL/Doris：源端和目标端应体现真实连接权限，避免把演示数据库和平台依赖混在一起。
- 不把 LLM 作为 Compose 服务：与用户的远端百炼约束冲突，也会增加 GPU/模型运维范围。

## 后果

- 本地 VM 可以先完成控制面依赖的真实集成测试，不需要 GPU。
- PostgreSQL、Redis、MinIO 和 Vault 的数据卷必须纳入备份；开发模式 Vault 不可直接用于生产。
- SeaTunnel 镜像版本、端口和启动脚本可能随版本变化，必须在 VM 首次启动时记录验证结果。
- 如果后续采用托管 PostgreSQL/Redis/对象存储，只需替换环境变量和部署清单，不改变业务边界。

## 复审条件

当首期验收链路、部署规模、SSO、KMS/HSM、Redis 高可用或 SeaTunnel 集群要求明确后，重新评审本 ADR。
