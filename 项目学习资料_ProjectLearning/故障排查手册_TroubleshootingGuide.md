# ETL-Agent 故障排查手册

## 1. 排查顺序

```text
确认输入和环境变量
  -> DNS/网络
  -> Docker 服务状态
  -> 依赖健康检查
  -> 应用日志和 request_id
  -> 数据库/队列/对象存储状态
  -> 业务状态机和审计事件
```

不要一开始就怀疑 LangGraph 或 LLM；先确认真实输入、配置和边界服务是否可达。

## 2. Docker/VM 基础命令

```bash
docker compose ps
docker compose logs --tail=100 postgres redis minio vault
docker system df
df -h /var/lib/docker
free -h
```

核心检查：

```bash
docker compose exec postgres pg_isready -U "${POSTGRES_USER:-etl_agent}" -d "${POSTGRES_DB:-etl_agent}"
docker compose exec redis redis-cli ping
curl http://127.0.0.1:9000/minio/health/live
curl http://127.0.0.1:8200/v1/sys/health
```

## 3. Windows/PyCharm 连接 VM

如果后端运行在 Windows、依赖运行在 `192.168.181.128`：

- VM 的 `COMPOSE_BIND_IP` 不能是 `127.0.0.1`，否则 Windows 无法访问端口。
- Windows `.env` 中 PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel 地址使用 `192.168.181.128`。
- 使用 `Test-NetConnection 192.168.181.128 -Port 5432` 检查端口。
- 使用 `curl http://192.168.181.128:9000/minio/health/live` 检查 MinIO。
- 只开放可信来源，不能为了开发把 Redis/PostgreSQL/Vault 暴露到公网。

## 4. 常见问题

| 现象 | 优先检查 | 处理 |
| --- | --- | --- |
| Compose 服务拉取慢 | Registry、DNS、磁盘、镜像大小 | 等待/换可信镜像/离线 `save/load` |
| PostgreSQL refused | `docker compose ps`、端口绑定、当前工作目录 | 确认项目根 `.env` 中使用 `192.168.181.128`；不要从未加载配置的目录覆盖环境变量，也不要重建数据卷 |
| Redis ping 失败 | 服务状态、端口、逻辑库 URL | 检查 `REDIS_URL` 和容器日志 |
| MinIO 访问失败 | endpoint 是主机地址还是服务名 | VM 主机用 VM IP，Compose 内用 `minio` |
| Vault 401 | Token、KV mount、namespace | 开发模式只用于本地；检查 `VAULT_KV_MOUNT` |
| 只读 Profile 探查失败 | 连接主机、SecretRef、`information_schema` 返回键名 | Windows 访问 VM 时把旧连接的 `127.0.0.1` 改为 `192.168.181.128`；若连接测试通过但 Profile 失败，确认 Profile 代码兼容 MySQL 大写元数据列名，并重启 FastAPI |
| LLM timeout | Base URL、DNS、API Key、百炼配额 | 先用 curl/最小客户端验证，再看 Workflow |
| Agent 无法恢复 | thread_id、Checkpoint DB、状态版本 | 查 AgentRun 和 PostgreSQL checkpoint |
| Commit 被拒绝 | 指纹、审批槽、Capability、角色 | 查稳定错误码和 AuditEvent，不绕过检查 |
| Worker 无任务 | Redis Broker、队列名、Outbox 状态 | 查队列、Outbox 和 Worker 日志 |
| 作业失败 | SeaTunnel job ID、连接器、质量快照 | 先保留影子表和日志，再执行受管回滚 |

## 5. 数据安全注意

- 不要把 `.env`、Vault Token、数据库密码、LLM Key 或业务数据贴到 Issue/聊天中。
- 不要未经确认执行 `docker compose down -v`、`docker system prune -a` 或删除 MinIO bucket。
- 诊断日志只保留 request ID、资源 ID、摘要和错误码。
