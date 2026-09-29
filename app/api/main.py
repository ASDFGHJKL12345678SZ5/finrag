"""FastAPI 应用骨架（D4：仅健康检查；入库走 CLI，问答图 D5 接入）。"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.rag import store

log = get_logger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging(get_settings().log_level)
    await store.init_store()
    log.info("api.started")
    yield
    await store.close_store()
    log.info("api.stopped")


app = FastAPI(title="FinRAG API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "finrag"}
