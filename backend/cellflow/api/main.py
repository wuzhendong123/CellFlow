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

    @app.get("/healthz")
    def healthz():
        checks = {}
        for name, fn in {
            "metaDb": lambda: get_engine().connect().execute(text("SELECT 1")).scalar() == 1,
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
    return app


app = create_app()
