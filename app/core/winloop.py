"""Windows 下强制 Selector 事件循环。

asyncpg 在 ProactorEventLoop 上会报错（Windows 上 uvicorn 默认 Proactor）。
Linux/macOS 默认即 Selector，本函数在那边是无操作。
"""
import asyncio
import sys


def ensure_selector_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
