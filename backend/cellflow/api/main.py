"""FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from cellflow.api.common import install_error_handlers, ok
from cellflow.meta.db import get_engine
from cellflow.redis_client import get_redis
from cellflow.storage import get_storage


def create_app() -> FastAPI:
    app = FastAPI(title="CellFlow", version="0.1.0")
    install_error_handlers(app)

    def meta_db_ok() -> bool:
        with get_engine().connect() as c:  # 必须归还连接，否则每次健康检查泄漏一个，连接池很快耗尽
            return c.execute(text("SELECT 1")).scalar() == 1

    @app.get("/healthz")
    def healthz():
        checks = {}
        for name, fn in {
            "metaDb": meta_db_ok,
            "redis": lambda: get_redis().ping(),
            "storage": lambda: get_storage().ping(),
        }.items():
            try:
                checks[name] = bool(fn())
            except Exception as exc:  # 健康检查需要吞掉异常并报告
                checks[name] = False
                checks[name + "Error"] = type(exc).__name__
        return ok({"healthy": all(v for k, v in checks.items() if not k.endswith("Error")), "checks": checks})

    from cellflow.api import routers

    routers.register(app)
    _mount_console(app)
    return app


def _mount_console(app: FastAPI) -> None:
    """生产部署时由后端直接托管前端构建产物（CF_CONSOLE_DIR，默认 ../frontend/dist，不存在则跳过）。"""
    import os
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    d = Path(os.environ.get("CF_CONSOLE_DIR") or Path(__file__).resolve().parents[3] / "frontend" / "dist")
    if (d / "index.html").is_file():
        app.mount("/", StaticFiles(directory=d, html=True), name="console")


app = create_app()
