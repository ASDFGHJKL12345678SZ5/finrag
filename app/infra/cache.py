"""Redis 缓存与限流（FR-8）：Lua 原子令牌桶 + 失败降级。

与 DataCrew 的 app/infra/cache.py 同构，但 key 前缀独立（finrag:），
两个项目共用同一个 Redis 实例也不会互相踩 key。

为什么限流必须原子：读-改-写三个命令分开执行时，并发请求会读到同一个
旧 token 数，限流形同虚设。Lua 脚本在 Redis 单线程里整体执行，
GetTokens→扣减→写回 不可分割。
"""
from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("cache")

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


_TOKEN_BUCKET_LUA = """
local k = KEYS[1]
local now = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
local rate = tonumber(ARGV[3]) / 60.0
local d = redis.call('HMGET', k, 'tokens', 'ts')
local tokens = tonumber(d[1]) or cap
local ts = tonumber(d[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
if tokens < 1 then
  redis.call('HMSET', k, 'tokens', tokens, 'ts', now)
  redis.call('EXPIRE', k, 3600)
  return 0
end
tokens = tokens - 1
redis.call('HMSET', k, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', k, 3600)
return 1
"""


async def token_bucket(key: str, capacity: int, refill_per_min: int) -> bool:
    """令牌桶：True 放行 / False 拒绝。Redis 故障时 **fail-open**（放行）。

    限流器挂掉就拒掉所有请求，比不限流更糟；可见性由日志与 /health 的
    redis 字段保证。与 DataCrew 同一取舍。
    """
    try:
        ok = await get_redis().eval(
            _TOKEN_BUCKET_LUA, 1, "finrag:rl:" + key, time.time(), capacity, refill_per_min
        )
        return bool(ok)
    except Exception as e:  # noqa: BLE001 - 降级路径必须吞掉所有 Redis 异常
        log.warning("ratelimit.degraded_open", extra={"context": {"reason": str(e)[:100]}})
        return True


async def cache_get(key: str) -> Any | None:
    try:
        raw = await get_redis().get("finrag:cache:" + key)
        return json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001
        log.warning("cache.get_degraded", extra={"context": {"reason": str(e)[:100]}})
        return None


async def cache_set(key: str, value: Any, ttl_s: int = 600) -> None:
    try:
        await get_redis().set(
            "finrag:cache:" + key,
            json.dumps(value, ensure_ascii=False, default=str),
            ex=ttl_s,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("cache.set_degraded", extra={"context": {"reason": str(e)[:100]}})


async def healthcheck() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:  # noqa: BLE001
        return False
