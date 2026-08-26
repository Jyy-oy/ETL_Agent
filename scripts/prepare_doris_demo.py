"""初始化真实 Doris 合成目标表，供 MySQL -> SeaTunnel -> Doris 演示使用。

脚本只创建学习项目的目标数据库和订单表，不读取或写入控制面 PostgreSQL。
生产环境应使用受审计的数据库迁移或发布流程替代本脚本。
"""

import os
import re

import pymysql  # type: ignore[import-untyped]


def _identifier(value: str, label: str) -> str:
    """校验 Doris 标识符，避免环境变量被拼接为任意 SQL。"""
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", normalized):
        raise ValueError(f"{label}只能包含字母、数字和下划线")
    return f"`{normalized}`"


def main() -> int:
    """创建 Doris 数据库和与合成 MySQL 字段对应的 Unique Key 目标表。"""
    host = os.getenv("DORIS_HOST", "192.168.181.128")
    port = int(os.getenv("DORIS_PORT", "9030"))
    user = os.getenv("DORIS_USER", "root")
    password = os.getenv("DORIS_PASSWORD", "")
    database = os.getenv("DORIS_DATABASE", "etl_demo_dw")
    table = os.getenv("DORIS_TABLE", "orders_current")
    database_sql = _identifier(database, "DORIS_DATABASE")
    table_sql = _identifier(table, "DORIS_TABLE")
    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        connect_timeout=10,
        read_timeout=10,
        write_timeout=10,
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database_sql}")
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {database_sql}.{table_sql} (
                    order_id BIGINT NOT NULL,
                    customer_id BIGINT NOT NULL,
                    order_status VARCHAR(32) NOT NULL,
                    amount DECIMAL(18, 2) NOT NULL,
                    ordered_at DATETIME NOT NULL,
                    source_batch VARCHAR(64) NOT NULL
                ) ENGINE=OLAP
                UNIQUE KEY(order_id)
                DISTRIBUTED BY HASH(order_id) BUCKETS 1
                PROPERTIES ("replication_num" = "1")
                """
            )
    finally:
        connection.close()
    print(f"Doris 目标表已就绪: {database}.{table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
