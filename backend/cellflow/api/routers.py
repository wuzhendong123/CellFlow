"""各业务路由在此注册。"""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from cellflow.api import console_files

    app.include_router(console_files.router)
