"""调用方管理（F15-3）。"""

from __future__ import annotations

import secrets

from sqlalchemy import select

from cellflow.errors import CFError, not_found
from cellflow.meta.db import get_engine
from cellflow.meta.tables import client_app
from cellflow.services import settings


def _json(r: dict) -> dict:
    return {"id": r["id"], "appKey": r["app_key"], "name": r["name"], "secretRef": r["secret_ref"],
            "allowedPipelines": r["allowed_pipelines"], "callbackAllowlist": r["callback_allowlist"],
            "rateLimitPerMin": r["rate_limit_per_min"], "enabled": bool(r["enabled"])}


def list_all() -> list[dict]:
    with get_engine().connect() as c:
        return [_json(dict(r)) for r in c.execute(select(client_app).order_by(client_app.c.id)).mappings()]


def get(app_id: int) -> dict:
    with get_engine().connect() as c:
        r = c.execute(select(client_app).where(client_app.c.id == app_id)).mappings().first()
    if not r:
        raise not_found("调用方")
    return dict(r)


def by_key(app_key: str) -> dict | None:
    with get_engine().connect() as c:
        r = c.execute(select(client_app).where(client_app.c.app_key == app_key)).mappings().first()
    return dict(r) if r else None


def save(data: dict, app_id: int | None = None) -> dict:
    ref = data.get("secretRef") or ""
    if not ref or any(ch in ref for ch in ":/@ "):
        raise CFError("INVALID_REQUEST", "签名密钥只能填写引用名（真实值通过环境变量配置）", 400)
    vals = {
        "name": (data.get("name") or "")[:128], "secret_ref": ref,
        "allowed_pipelines": list(data.get("allowedPipelines") or []),
        "callback_allowlist": list(data.get("callbackAllowlist") or []),
        "rate_limit_per_min": int(data.get("rateLimitPerMin") or settings.get("openapi.defaultRateLimitPerMin")),
        "enabled": 1 if data.get("enabled", True) else 0,
    }
    if not vals["name"]:
        raise CFError("INVALID_REQUEST", "名称必填", 400)
    with get_engine().begin() as c:
        if app_id is None:
            vals["app_key"] = "cf_" + secrets.token_hex(8)
            app_id = c.execute(client_app.insert().values(**vals)).inserted_primary_key[0]
        else:
            get(app_id)
            c.execute(client_app.update().where(client_app.c.id == app_id).values(**vals))
    return _json(get(app_id))
