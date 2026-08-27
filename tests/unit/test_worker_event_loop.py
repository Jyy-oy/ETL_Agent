"""Celery Worker 事件循环兼容性测试。"""

import asyncio
import sys

from etl_agent.workers.tasks import _run_async


async def _current_loop_type() -> type[asyncio.AbstractEventLoop]:
    """返回当前协程实际运行的事件循环类型，供平台兼容性断言使用。"""
    return type(asyncio.get_running_loop())


def test_worker_uses_selector_loop_on_windows(monkeypatch) -> None:
    """验证模拟 Windows 时 Worker 不会重新启用 psycopg 不支持的 Proactor。"""
    monkeypatch.setattr(sys, "platform", "win32")

    loop_type = _run_async(_current_loop_type())

    assert issubclass(loop_type, asyncio.SelectorEventLoop)
