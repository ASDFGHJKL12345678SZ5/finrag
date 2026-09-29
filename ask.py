# -*- coding: utf-8 -*-
r"""FinRAG 问答客户端：交互式问问题，展示答案/引用/校验明细/耗时。

用法：
    .venv\Scripts\python.exe ask.py                        # 交互模式（推荐）
    .venv\Scripts\python.exe ask.py "宁德润能的毛利率是多少？"   # 单次提问

依赖 API 已在 8001 端口运行（另开窗口跑 python -m app.main）。
Windows cmd 控制台默认 GBK：reconfigure 让中文按控制台编码输出，不乱码。
"""
import sys

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stdin.reconfigure(errors="replace")
except Exception:
    pass

import httpx

BASE = "http://127.0.0.1:8001"


def ask(question: str) -> dict:
    r = httpx.post(BASE + "/ask/sync",
                   json={"question": question, "session_id": "ask.py"},
                   timeout=60)
    r.raise_for_status()
    return r.json()


def show(d: dict, trace: bool = False) -> None:
    status = d.get("status")
    # 不用 emoji：Windows cmd 默认 GBK 代码页，emoji 会变问号
    mark = "[通过]" if status == "done" else "[拒答]"
    print(f"{mark} 状态: {status}    耗时: {d.get('total_ms')}ms")
    print(f"答案: {d.get('answer')}")
    cites = d.get("citations") or []
    if cites:
        print("引用来源:")
        seen = set()
        for c in cites:
            key = (c["citation_id"], c["source"])
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{c['citation_id']}] {c['source']}  ({c.get('section_path') or '-'})")
            if trace:
                # 溯源模式：打印被引 chunk 全文——答案里每个数字都能在这里找到，
                # 这是用户自查答案正确性的依据（语料是合成的，现实无法验证）
                print(f"      原文: {c.get('text', '')}")
    verdicts = d.get("verdicts") or []
    bad = [v for v in verdicts if not v.get("supported")]
    if bad:
        print("被拦截的论断（引用校验不过）:")
        for v in bad:
            print(f"  x {v.get('claim', '')[:60]}  缺失: {v.get('missing')}")
    print(f"重试轮次: {d.get('retry_count', 0)}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    trace = "--trace" in sys.argv
    if args:
        show(ask(" ".join(args)), trace=trace)
        return
    print("FinRAG 问答客户端（直接回车退出）")
    print("试试: 青蓝动力2025年的归母净利润是多少？")
    print("     预测一下宁德润能未来三年的股价目标   <- 这句会被拒答")
    print("    --trace 参数：同时打印被引 chunk 全文，可逐字核对答案里的数字")
    while True:
        try:
            q = input(chr(10) + "问: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            break
        try:
            show(ask(q), trace=trace)
        except httpx.HTError as e:
            print(f"连不上 API（{e}）——先另开窗口跑: .venv/Scripts/python.exe -m app.main")


if __name__ == "__main__":
    main()
