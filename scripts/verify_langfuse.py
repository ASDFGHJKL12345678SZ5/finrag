"""FR-8 真实 Langfuse 联通验证：问答 -> flush -> 查 v2 observations。

Langfuse 已移除 v1 `/api/public/traces`（410），改用
`GET /api/public/v2/observations?fromStartTime=...&toStartTime=...`。
用途：确认 `.env` 里的 key/host 真的能收到 trace，而不只是单测里的 fake。
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

os.environ["EMBEDDING_MODE"] = "mock"
os.environ["LLM_MODE"] = "mock"

import httpx

from app.core.config import get_settings
from app.core.winloop import ensure_selector_loop

ensure_selector_loop()

from app.infra.observability import shutdown
from app.rag import store
from app.rag.graph import ask


async def main() -> None:
    s = get_settings()
    if not s.langfuse_enabled:
        print("langfuse 未配置（LANGFUSE_PUBLIC_KEY/SECRET_KEY 为空），跳过")
        return
    await store.init_store()
    final = await ask("宁德润能2025年的营业收入是多少？", session_id="langfuse-check")
    print("ask_status", final.get("status"))
    shutdown()
    await store.close_store()
    await asyncio.sleep(3)

    now = datetime.now(UTC)
    params = {
        "fromStartTime": (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        "toStartTime": (now + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "limit": 50,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            s.langfuse_host + "/api/public/v2/observations",
            params=params,
            auth=(s.langfuse_public_key, s.langfuse_secret_key),
        )
        print("api_status", r.status_code)
        if r.status_code != 200:
            print(r.text[:300])
            return
        rows = r.json().get("data", [])
        names = [(o.get("name"), o.get("traceId")) for o in rows]
        print("observations", len(names))
        print("recent", names[:8])
        print("has_finrag_trace", any(n == "finrag.qa" for n, _ in names))


asyncio.run(main())
