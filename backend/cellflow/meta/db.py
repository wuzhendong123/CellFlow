"""元数据库连接。"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from cellflow.config import ConfigError, get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().meta_db_url
    if not url:
        raise ConfigError("未配置 CF_META_DB_URL")
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600, future=True)


def reset_engine_cache() -> None:
    get_engine.cache_clear()
