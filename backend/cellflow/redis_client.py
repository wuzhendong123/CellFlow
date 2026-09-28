"""Redis 连接（任务队列、锁、限流、nonce 去重、口令错误计数）。"""

from __future__ import annotations

from functools import lru_cache

import redis

from cellflow.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
