"""Selector 事件循环工厂（Windows 兼容，Linux 无操作）。"""
import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()
