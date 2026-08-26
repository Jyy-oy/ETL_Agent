# ETL-Agent 开发问答笔记

本文是持续沉淀区。概念解释、排查结论和经过验证的工程经验追加到这里；临时猜测应标注为未验证。

## 记录格式

```text
日期：YYYY-MM-DD
问题：
结论：
证据/命令：
影响范围：
关联文档或变更：
```

## 初始笔记

### 2026-08-25：Windows/PyCharm 是否可以直接调用百炼？

结论：可以。开发阶段 Windows 运行 FastAPI/LangGraph，通过 HTTPS 调用远端百炼；PostgreSQL、Redis、MinIO、Vault 和 SeaTunnel 运行在 Ubuntu VM。Windows 后端的连接串必须使用 `192.168.181.128`，不能继续使用只对 VM 本机可见的 `localhost`。

### 2026-08-25：为什么 SeaTunnel 镜像没有随默认 Compose 拉取？

结论：SeaTunnel 使用 `data-plane` profile，默认 `docker compose pull` 不包含它。使用 `docker compose --profile data-plane pull seatunnel` 单独拉取。

### 2026-08-25：为什么普通镜像源不一定让 SeaTunnel 更快？

结论：SeaTunnel 镜像约 3 GB，镜像源可能没有缓存大 Layer，需要回源；也可能是 VM 网络或磁盘 I/O 瓶颈。先检查 DNS、带宽、磁盘和镜像源可达性，不要盲目增加不明来源镜像。

### 2026-08-26：为什么 MySQL 连接测试通过但 Profile 探查失败？

结论：连接测试只执行 `SELECT 1`，而 Profile 还会读取 `information_schema.columns` 和 `information_schema.tables`。本次 VM MySQL 的驱动返回大写元数据键（`TABLE_SCHEMA`、`TABLE_NAME`、`TABLE_ROWS`），旧代码按小写键读取导致 `KeyError('table_schema')`，最终页面只显示“只读 Profile 探查失败”。现已增加大小写兼容读取并补回归测试。

证据：修复前同一 VM 连接复现 `ProfileError`，根因为 `KeyError('table_schema')`；修复后识别 `demo_orders` 1 张表并读取样本，MySQL 精确行数为 10,000。
