"""统一响应：{"code", "message", "data", "traceId"}。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from cellflow.errors import CFError


def ok(data: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse({"code": "OK", "message": "", "data": data, "traceId": uuid.uuid4().hex}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CFError)
    async def _cf(_: Request, exc: CFError):
        return JSONResponse(
            {"code": exc.code, "message": exc.message, "data": exc.data, "traceId": uuid.uuid4().hex},
            status_code=exc.http_status,
        )

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, exc: RequestValidationError):
        return JSONResponse(
            {"code": "INVALID_REQUEST", "message": "请求参数不合法", "data": exc.errors(), "traceId": uuid.uuid4().hex},
            status_code=400,
        )
