"""统一错误（TECH_DESIGN §6.5、§6.6）。"""

from __future__ import annotations

from typing import Any


class CFError(Exception):
    """业务错误：code 为 §6.6 错误码，http_status 为传输层语义。"""

    def __init__(self, code: str, message: str, http_status: int = 400, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data


def not_found(what: str) -> CFError:
    return CFError("NOT_FOUND", f"{what}不存在", 404)
