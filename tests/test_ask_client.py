"""ask.py 命令行客户端的「连不上 API」路径。

事故记录：ask.py 曾把 ``httpx.HTTPError`` 误写成 ``httpx.HTError``（拼写错误），
异常类不存在，于是 ``except`` 子句本身在运行时抛 AttributeError——
API 没启动时用户看到的是一整屏堆栈，而不是「连不上 API」的提示。
本测试把 httpx.post 换成必然抛 HTTPError 的探针，锁死友好提示这条路径。

为什么测 main() 而不是只测 ask()：修复点就在 main() 的异常捕获边界，
只测 ask() 会得到一个「HTTPError 确实会抛出」的假绿测试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ask as ask_cli  # 必须在 sys.path 调整后导入（pytest 从 tests/ 收集）


def _run_main(monkeypatch, fake_post) -> str:
    """以单问题模式跑 main()，返回 stdout。"""
    monkeypatch.setattr(ask_cli.httpx, "post", fake_post)
    monkeypatch.setattr(sys, "argv", ["ask.py", "青蓝动力2025年的营业收入是多少？"])
    ask_cli.main()
    return ""


def test_api_unreachable_prints_friendly_message(monkeypatch, capsys):
    """连接失败（ConnectError 是 HTTPError 子类）必须被捕获，不得抛栈。"""

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    _run_main(monkeypatch, _boom)
    out = capsys.readouterr().out
    assert "连不上 API" in out
    assert "app.main" in out  # 提示里给出启动命令


def test_http_status_error_also_caught(monkeypatch, capsys):
    """服务起了但返回 5xx（raise_for_status）同样走友好提示，而不是裸栈。"""

    def _server_error(*args, **kwargs):
        request = httpx.Request("POST", ask_cli.BASE + "/ask/sync")
        response = httpx.Response(500, request=request)
        return response

    # raise_for_status 由 ask() 调用；返回 500 的 Response 会抛 HTTPStatusError
    _run_main(monkeypatch, _server_error)
    out = capsys.readouterr().out
    assert "连不上 API" in out
