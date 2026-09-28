"""高危操作依赖：校验口令并返回审计上下文。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from cellflow.api.deps import client_ip, operator
from cellflow.services.auth import check_op_token


@dataclass
class OpContext:
    operator: str
    ip: str


def require_op(request: Request) -> OpContext:
    who, ip = operator(request), client_ip(request)
    check_op_token(request.headers.get("X-CF-Op-Token"), who, ip)
    return OpContext(who, ip)
