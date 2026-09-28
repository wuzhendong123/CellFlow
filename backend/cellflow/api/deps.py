"""请求上下文：操作人、来源 IP。"""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import Request


def operator(request: Request) -> str:
    """v1 无登录（D19）：操作人来自请求头 X-CF-Operator（前端保存在浏览器本地），URL 编码以支持中文。"""
    raw = request.headers.get("X-CF-Operator", "")
    name = unquote(raw).strip()
    return name[:64] or "anonymous"


def client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "unknown")[:45]
