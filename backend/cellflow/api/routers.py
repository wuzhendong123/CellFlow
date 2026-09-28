"""各业务路由在此注册。"""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from cellflow.api import console, console_files, open_api

    app.include_router(console_files.router)
    app.include_router(console.router)
    app.include_router(open_api.router)
