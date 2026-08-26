# Ubuntu 虚拟机部署手册

本文针对 Ubuntu 虚拟机 `192.168.181.128`，部署 ETL-Agent 首期基础设施。当前 Compose 仍不启动 FastAPI、Celery 或 Vue 应用容器；控制面 API 和 M5.2 Worker/质量监督代码已存在，待应用镜像、运行用户和生产级密钥配置确认后再加入 Compose。

## 1. 目标拓扑

```text
开发机 / 浏览器
        |
        | 可选开放管理端口
        v
Ubuntu VM 192.168.181.128
  Docker Compose
  ├─ postgres:5432
  ├─ redis:6379
  ├─ minio:9000 / console:9001
  ├─ vault:8200 (开发模式)
  └─ seatunnel:5801-5803 (可选 profile)
  └─ source-target profile：MySQL 8.0.36 + Doris 2.1.11 FE/BE（可选）
        |
        └─ 远端百炼 LLM API（HTTPS）
```

## 2. VM 前置条件

建议 Ubuntu 22.04/24.04、4 vCPU、8 GB RAM、50 GB 可用磁盘。只启动 PostgreSQL、Redis、MinIO 和 Vault 时 4 GB 可用于开发验证；同时启动 SeaTunnel、MySQL 和 Doris FE/BE 建议至少 4 vCPU、12 GB RAM、80 GB 可用磁盘，性能测试建议 16 GB RAM 以上。

在 VM 执行：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
docker --version
docker compose version
```

生产环境应按 Ubuntu 官方 Docker 文档安装并固定 Docker 版本；上面的安装脚本适合一次性的开发虚拟机。

## 3. 获取项目并配置

GitHub 仓库创建后，在 VM 执行：

```bash
git clone <your-repository-url> ETL_Agent
cd ETL_Agent
cp .env.example .env
```

编辑 `.env`：

- 默认 `COMPOSE_BIND_IP=127.0.0.1`，仅允许 VM 本机访问。
- 若需要从开发机访问 MinIO Console，将其改为 `0.0.0.0`，并用 UFW 限制来源。
- 保留本地开发账号只用于验证；任何共享环境必须替换 PostgreSQL、MinIO、Vault、JWT 和 LLM 密钥。
- `MINIO_ENDPOINT` 在 VM 上直接运行的应用使用 `http://127.0.0.1:9000`；未来加入 Compose 的应用使用 `http://minio:9000`。
- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 填百炼提供的 OpenAI 兼容配置。

## 4. 启动和验证基础设施

首次启动核心服务：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

预期服务：`postgres`、`redis`、`minio`、`minio-init`、`vault`。`minio-init` 完成 bucket 创建后会退出，这是正常状态。

基础检查：

```bash
docker compose exec postgres pg_isready -U "${POSTGRES_USER:-etl_agent}" -d "${POSTGRES_DB:-etl_agent}"
docker compose exec redis redis-cli ping
curl http://127.0.0.1:9000/minio/health/live
curl http://127.0.0.1:8200/v1/sys/health
```

如果 `COMPOSE_BIND_IP=0.0.0.0`，可从开发机访问：

- MinIO Console：`http://192.168.181.128:9001`
- MinIO S3：`http://192.168.181.128:9000`
- Vault：`http://192.168.181.128:8200`

## 5. 启动 SeaTunnel（可选）

SeaTunnel 镜像和 Zeta 启动命令必须与最终选定版本一致。Compose 会把宿主机的 `./seatunnel/config` 挂载到容器配置目录；首次启动前必须先把镜像默认配置复制出来，否则空目录会覆盖镜像内置配置并导致 `jvm_options` 等文件缺失。

```bash
mkdir -p seatunnel/config
docker rm seatunnel-config 2>/dev/null || true
config_container=$(docker create apache/seatunnel:2.3.10)
docker cp "$config_container:/opt/seatunnel/config/." ./seatunnel/config/
docker rm "$config_container"
sudo chown -R "$USER":"$USER" seatunnel/config
chmod -R u+rwX seatunnel/config
test -f seatunnel/config/jvm_options
```

确认 `jvm_options` 存在后再启动并查看日志：

```bash
docker compose --profile data-plane pull seatunnel
docker compose --profile data-plane up -d seatunnel
docker compose --profile data-plane logs -f seatunnel
```

当前 Compose 使用单节点 `master_and_worker` 角色，启动时不传 `-r` 参数即可使用 SeaTunnel 2.3.10 的默认角色。该版本显式传入 `master_and_worker` 会被 Java 引擎拒绝；多节点部署时才分别使用 `-r master` 和 `-r worker`。如果镜像版本的启动脚本路径、角色参数或端口不同，应只修改 Compose 的 `seatunnel` 服务和本文件，不要改变控制面与数据面的边界。SeaTunnel 需要独立的作业配置和插件目录，业务连接凭据仍由 Vault/SecretProvider 注入。

SeaTunnel 2.3.10 默认关闭 Hazelcast REST API，日志会出现 `REST API is not enabled`。Compose 已通过 `HZ_NETWORK_RESTAPI_ENABLED=true` 开启该开关，并把宿主 `5802` 映射到容器 REST 端口 `8080`；修改后需执行 `docker compose --profile data-plane up -d --force-recreate seatunnel`，再确认日志包含 `SeaTunnel REST service will start on port 8080`。可用以下命令验证 REST 服务：

```bash
curl -fsS http://127.0.0.1:5802/running-jobs
curl -fsS http://127.0.0.1:5802/finished-jobs
```

SeaTunnel 2.3.10 的已验证契约是：提交 `POST /submit-job?format=hocon`，请求体为 `text/plain` HOCON；状态 `GET /job-info/{job_id}`，响应状态字段为 `jobStatus`、作业 ID 字段为 `jobId`；取消 `POST /stop-job`，JSON 请求体为 `{"jobId":"<id>"}`。这些差异由 `SeaTunnelAdapter` 统一转换，路径仍通过 `.env` 的 `SEATUNNEL_*_PATH` 配置。清理、Swap 和回滚不是 SeaTunnel 2.3.10 原生动作，仍需由 MySQL/Doris 目标适配器提供。

## 6. 启动 MySQL/Doris 合成数据面（可选）

当前基线固定使用 `mysql:8.0.36`、`apache/doris:fe-2.1.11` 和 `apache/doris:be-2.1.11`。Doris FE/BE 必须保持同一版本；SeaTunnel 2.3.10 的 Doris Connector 文档声明 Doris `>=1.1.x`，2.1.11 用于本地兼容性验证。MySQL/Doris 只作为合成源端和目标端，不会加入默认基础设施启动。

截至 2026-08-26，VM 已完成三张镜像拉取并启动 MySQL、Doris FE、Doris BE；三者 Docker healthcheck 均为 `healthy`。只读验证已确认 MySQL `mysqld is alive`，Doris `SHOW BACKENDS` 返回 `Alive=true`。FE 启动早于 BE 注册时可能短暂出现 `available backend num is 0`，待 BE 心跳注册后会恢复，不能仅凭启动早期日志判断失败。

先检查 VM 资源、Compose 展开结果和 Docker 网段是否冲突：

```bash
nproc
free -h
df -h .
docker compose --profile source-target config >/tmp/etl-agent-source-target.yml
docker network ls
```

确认没有网段冲突后，先只拉取镜像：

```bash
docker compose --profile source-target pull mysql doris-fe doris-be
```

拉取完成后再启动：

```bash
docker compose --profile source-target up -d mysql doris-fe doris-be
docker compose --profile source-target ps
```

基础检查：

```bash
docker compose --profile source-target exec mysql \
  sh -c 'mysqladmin ping -h localhost -uroot -p"$MYSQL_ROOT_PASSWORD" --silent'
curl -fsS http://127.0.0.1:8030/api/bootstrap
docker compose --profile source-target exec doris-fe \
  mysql -h 127.0.0.1 -P 9030 -uroot -e 'SHOW FRONTENDS; SHOW BACKENDS;'
```

SeaTunnel 作业容器访问 MySQL 使用 `mysql:3306`，访问 Doris 使用 `doris-fe:8030`（Stream Load）和查询端口 `9030`，不能在容器内填写 `localhost`。如果 `172.30.0.0/24` 与 VM 现有 Docker 网络冲突，需要同时修改 `DORIS_DOCKER_SUBNET`、`DORIS_FE_IP`、`DORIS_BE_IP`、`DORIS_FE_SERVERS` 和 `DORIS_BE_ADDR`，然后再创建网络。

## 7. 日常操作

```bash
# 查看状态和最近日志
docker compose ps
docker compose logs --tail=100 postgres redis minio vault

# 重启单个依赖
docker compose restart postgres

# 停止服务但保留数据卷
docker compose stop

# 更新镜像后重建
docker compose pull
docker compose up -d
```

不要在没有备份确认的情况下执行 `docker compose down -v`，该命令会删除数据库、Redis、MinIO 和 Vault 的命名卷。

## 8. 防火墙建议

如果只在 VM 内运行应用，保持 `COMPOSE_BIND_IP=127.0.0.1`，不开放数据库端口。若需远程访问管理端口，可使用：

```bash
sudo ufw allow from <trusted-subnet> to any port 9001 proto tcp
sudo ufw allow from <trusted-subnet> to any port 8200 proto tcp
sudo ufw enable
```

不要把 PostgreSQL、Redis 或 Vault 开发端口开放到不受信任的网络。

## 9. 数据备份起点

首期至少建立 PostgreSQL `pg_dump` 和 MinIO bucket 备份策略。备份脚本应放在运维目录，不把导出的业务数据、Secret 或备份压缩包提交到 Git。
