"""Uvicorn entry point for the control-plane API。"""

import asyncio
import sys


def selector_event_loop_factory(*, use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    """为 Windows Uvicorn 提供 psycopg 异步 Checkpoint 所需的 Selector 事件循环。"""
    del use_subprocess
    return asyncio.SelectorEventLoop()


def _create_app():
    """设置 Windows 事件循环后创建 FastAPI 应用，确保 psycopg Checkpoint 可用。"""
    if sys.platform == "win32":
        # psycopg 异步连接不支持 Windows Proactor，需使用 Selector 才能运行 Checkpoint。
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from etl_agent.api.app import create_app

    return create_app()


app = _create_app()
