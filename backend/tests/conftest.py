"""测试环境：使用环境变量提供的 MySQL / Redis（CF_TEST_MYSQL_ROOT_URL、CF_TEST_REDIS_URL），
每次测试会话创建独立的测试库，对象存储使用临时目录。"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

MYSQL_ROOT = os.environ.get("CF_TEST_MYSQL_URL", "mysql+pymysql://cellflow:cellflow_dev@127.0.0.1:3306")
REDIS_URL = os.environ.get("CF_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
SUFFIX = uuid.uuid4().hex[:6]
META_DB = f"cellflow_test_meta_{SUFFIX}"
BIZ_DB = f"cellflow_test_biz_{SUFFIX}"


def pytest_configure(config):
    os.environ["CF_META_DB_URL"] = f"{MYSQL_ROOT}/{META_DB}?charset=utf8mb4"
    os.environ["CF_REDIS_URL"] = REDIS_URL
    os.environ["CF_STORAGE_BACKEND"] = "local"
    os.environ.setdefault("CF_OP_TOKEN", "test-op-token")
    os.environ["CF_REF_TEST_BIZ"] = MYSQL_ROOT
    os.environ["CF_REF_TEST_APP_SECRET"] = "test-app-secret"


@pytest.fixture(scope="session")
def mysql_root():
    eng = create_engine(MYSQL_ROOT + "/?charset=utf8mb4", future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"测试 MySQL 不可用：{exc}")
    return eng


@pytest.fixture(scope="session")
def meta_db(mysql_root, tmp_path_factory):
    with mysql_root.begin() as c:
        c.execute(text(f"CREATE DATABASE `{META_DB}` CHARACTER SET utf8mb4"))
        c.execute(text(f"CREATE DATABASE `{BIZ_DB}` CHARACTER SET utf8mb4"))
    os.environ["CF_STORAGE_LOCAL_DIR"] = str(tmp_path_factory.mktemp("storage"))
    from alembic.config import Config

    from alembic import command

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    yield {"meta": META_DB, "biz": BIZ_DB}
    with mysql_root.begin() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS `{META_DB}`"))
        c.execute(text(f"DROP DATABASE IF EXISTS `{BIZ_DB}`"))


@pytest.fixture
def clean_meta(meta_db):
    """每个用例开始前清空元数据表与测试 Redis 库。"""
    from cellflow.meta.db import get_engine
    from cellflow.meta.tables import metadata
    from cellflow.redis_client import get_redis

    with get_engine().begin() as c:
        for t in metadata.sorted_tables:
            c.execute(t.delete())
    get_redis().flushdb()
    yield meta_db


@pytest.fixture
def client(clean_meta):
    from fastapi.testclient import TestClient

    from cellflow.api.main import create_app

    return TestClient(create_app())
