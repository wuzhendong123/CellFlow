"""运行配置：全部来自环境变量（KICKOFF §3.3），代码中不出现真实值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


class ConfigError(RuntimeError):
    pass


def resolve_ref(ref: str) -> str:
    """把数据源 / 调用方中登记的「引用名」解析为真实值：CF_REF_<引用名大写>。"""
    key = "CF_REF_" + ref.upper().replace("-", "_").replace(".", "_")
    value = os.environ.get(key)
    if not value:
        raise ConfigError(f"未配置引用 {ref}（环境变量 {key}）")
    return value


@dataclass(frozen=True)
class Settings:
    meta_db_url: str
    redis_url: str
    storage_backend: str
    storage_local_dir: str
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    op_token: str


@lru_cache
def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        meta_db_url=env("CF_META_DB_URL", ""),
        redis_url=env("CF_REDIS_URL", "redis://127.0.0.1:6379/0"),
        storage_backend=env("CF_STORAGE_BACKEND", "local"),
        storage_local_dir=env("CF_STORAGE_LOCAL_DIR", "./data/storage"),
        s3_endpoint=env("CF_S3_ENDPOINT", ""),
        s3_bucket=env("CF_S3_BUCKET", "cellflow"),
        s3_access_key=env("CF_S3_ACCESS_KEY", ""),
        s3_secret_key=env("CF_S3_SECRET_KEY", ""),
        op_token=env("CF_OP_TOKEN", ""),
    )
