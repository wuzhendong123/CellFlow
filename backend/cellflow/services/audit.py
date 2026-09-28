"""审计日志（F16，TECH_DESIGN §3 cf_audit_log）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from cellflow.engine.types import to_jsonable
from cellflow.meta.db import get_engine
from cellflow.meta.tables import audit_log


def record(conn, operator: str, ip: str, action: str, target: str, detail: Any = None, reason: str | None = None) -> None:
    conn.execute(audit_log.insert().values(
        operator=operator[:64], source_ip=ip[:45], action=action, target=target[:128],
        detail=to_jsonable(detail) if detail is not None else None, reason=(reason or None) and reason[:512],
    ))


def query(action: str | None = None, target: str | None = None, operator: str | None = None,
          offset: int = 0, limit: int = 50) -> dict:
    q = select(audit_log).order_by(audit_log.c.id.desc())
    if action:
        q = q.where(audit_log.c.action == action)
    if target:
        q = q.where(audit_log.c.target.like(f"{target}%"))
    if operator:
        q = q.where(audit_log.c.operator == operator)
    with get_engine().connect() as c:
        rows = [dict(r) for r in c.execute(q.offset(offset).limit(limit)).mappings()]
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    return {"rows": rows, "offset": offset}
