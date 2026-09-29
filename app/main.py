"""FinRAG 服务入口（与 DataCrew 同款 Windows 循环处理）。"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host="0.0.0.0",
        port=8001,
        loop="app.loops:selector_loop_factory",
    )
