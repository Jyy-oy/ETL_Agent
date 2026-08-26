"""向 Compose MySQL 写入可重复的大批量演示数据。

这个脚本只生成脱敏的合成数据，不能替代真实业务数据质量验收。
"""

import argparse
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pymysql


def create_table(connection: pymysql.Connection) -> None:
    """创建一张用于 MySQL -> SeaTunnel -> Doris 演示的订单表。"""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_orders (
                order_id BIGINT PRIMARY KEY,
                customer_id BIGINT NOT NULL,
                order_status VARCHAR(32) NOT NULL,
                amount DECIMAL(18, 2) NOT NULL,
                ordered_at DATETIME NOT NULL,
                email VARCHAR(255) NOT NULL,
                source_batch VARCHAR(64) NOT NULL,
                INDEX ix_demo_orders_ordered_at (ordered_at),
                INDEX ix_demo_orders_customer_id (customer_id)
            ) ENGINE=InnoDB
            """
        )
    connection.commit()


def seed_rows(connection: pymysql.Connection, row_count: int, batch_size: int) -> int:
    """按批次插入确定性订单数据，重复执行同一范围不会产生重复主键。"""
    base_time = datetime(2025, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    sql = """
        INSERT INTO demo_orders
            (order_id, customer_id, order_status, amount, ordered_at, email, source_batch)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            customer_id = VALUES(customer_id), order_status = VALUES(order_status),
            amount = VALUES(amount), ordered_at = VALUES(ordered_at),
            email = VALUES(email), source_batch = VALUES(source_batch)
    """
    statuses = ("paid", "shipped", "cancelled", "refunded")
    inserted = 0
    with connection.cursor() as cursor:
        for start in range(1, row_count + 1, batch_size):
            rows = []
            end = min(start + batch_size, row_count + 1)
            for order_id in range(start, end):
                customer_id = 10_000 + (order_id % 25_000)
                status = statuses[order_id % len(statuses)]
                amount = Decimal(order_id % 100_000) / Decimal("100") + Decimal("1.00")
                ordered_at = base_time + timedelta(seconds=order_id)
                rows.append(
                    (
                        order_id,
                        customer_id,
                        status,
                        amount,
                        ordered_at,
                        f"customer-{customer_id}@example.invalid",
                        f"seed-{row_count}",
                    )
                )
            cursor.executemany(sql, rows)
            connection.commit()
            inserted += len(rows)
    return inserted


def main() -> int:
    """读取环境变量并执行合成数据初始化。"""
    parser = argparse.ArgumentParser(description="初始化 ETL-Agent 合成 MySQL 数据")
    parser.add_argument("--rows", type=int, default=100_000, help="写入行数")
    parser.add_argument("--batch-size", type=int, default=2_000, help="每批写入行数")
    args = parser.parse_args()
    if args.rows <= 0 or args.batch_size <= 0:
        parser.error("--rows 和 --batch-size 必须为正数")
    connection = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("SOURCE_MYSQL_USER", "etl_demo"),
        password=os.getenv("SOURCE_MYSQL_PASSWORD", "etl_demo_dev"),
        database=os.getenv("SOURCE_MYSQL_DATABASE", "etl_demo"),
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        create_table(connection)
        inserted = seed_rows(connection, args.rows, args.batch_size)
        print(f"已写入合成订单行数: {inserted}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
