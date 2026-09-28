"""Open API（业务服务调用，TECH_DESIGN §6.5.1）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sqlalchemy import select

from cellflow.api.common import ok
from cellflow.engine.dag import analyze
from cellflow.errors import CFError
from cellflow.meta.db import get_engine
from cellflow.meta.tables import pipeline_revision
from cellflow.services import auth, client_apps, jobs, pipelines, publishing

router = APIRouter(prefix="/open/v1")


async def _authed(request: Request, body: bytes) -> dict:
    key = request.headers.get("X-CF-AppKey")
    app = client_apps.by_key(key) if key else None
    if not app or not app["enabled"]:
        raise CFError("AUTH_INVALID_SIGNATURE", "调用方不存在或已停用", 401)
    auth.verify_signature(app, request.method, request.url.path, request.headers.get("X-CF-Timestamp"),
                          request.headers.get("X-CF-Nonce"), request.headers.get("X-CF-Signature"), body)
    auth.rate_limit(app)
    return app


def _check_job_owner(app: dict, job: dict) -> None:
    if job["client_app_id"] != app["id"]:
        raise CFError("NOT_FOUND", "任务不存在", 404)


@router.post("/jobs")
async def submit(request: Request):
    body = await request.body()
    app = await _authed(request, body)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise CFError("INVALID_REQUEST", "缺少文件（multipart 字段 file）", 400)
    try:
        meta = json.loads(form.get("meta") or "{}")
    except json.JSONDecodeError:
        raise CFError("INVALID_REQUEST", "meta 不是合法的 JSON", 400) from None
    data = await upload.read()
    return ok(jobs.submit(app, meta, data, upload.filename or "upload.xlsx"), status=202)


@router.get("/jobs/{job_id}")
async def job_status(job_id: int, request: Request):
    app = await _authed(request, b"")
    j = jobs.get_job(job_id)
    _check_job_owner(app, j)
    d = jobs.job_detail(job_id)
    result = d.pop("result")
    d.pop("metrics", None)
    d["tables"] = result.get("tables", [])
    if result.get("blockedBy"):
        d["blockedBy"] = result["blockedBy"]
    if result.get("forecast"):
        d["forecast"] = result["forecast"]
    return ok(d)


@router.get("/jobs/{job_id}/issues")
async def job_issues(job_id: int, request: Request, offset: int = 0, limit: int = 100):
    app = await _authed(request, b"")
    _check_job_owner(app, jobs.get_job(job_id))
    return ok(jobs.list_issues(job_id, offset=offset, limit=min(limit, 500)))


@router.get("/pipelines/{code}")
async def pipeline_info(code: str, request: Request):
    app = await _authed(request, b"")
    if code not in (app["allowed_pipelines"] or []):
        raise CFError("AUTH_PIPELINE_FORBIDDEN", f"调用方无权调用方案「{code}」", 403)
    p = pipelines.get_by_code(code)
    if not p["published_rev_id"]:
        raise CFError("PIPELINE_NOT_PUBLISHED", f"方案「{code}」没有已发布版本", 422)
    with get_engine().connect() as c:
        rev = c.execute(select(pipeline_revision).where(pipeline_revision.c.id == p["published_rev_id"])).mappings().first()
    a = analyze(rev["dsl"])
    tables = []
    for n in rev["dsl"].get("nodes", []):
        if n.get("type") != "SINK":
            continue
        b = (n.get("config") or {}).get("binding") or {}
        edge = next((e for e in rev["dsl"].get("edges", []) if e["target"]["nodeId"] == n["id"]), None)
        s = a.schemas.get((edge["source"]["nodeId"], edge["source"]["portId"])) if edge else None
        types = {c.field: c.type for c in (s.columns if s else [])}
        tables.append({"dataset": n["config"].get("dataset"), "table": b.get("table"), "keyFields": b.get("keyFields"),
                       "columns": [{"column": m["column"], "field": m["field"], "type": types.get(m["field"])}
                                   for m in b.get("columnMapping") or []]})
    return ok({"code": code, "name": p["name"], "rev": rev["rev"], "frozen": bool(p["frozen"]), "tables": tables})


@router.get("/pipelines/{code}/live")
async def pipeline_live(code: str, request: Request):
    app = await _authed(request, b"")
    if code not in (app["allowed_pipelines"] or []):
        raise CFError("AUTH_PIPELINE_FORBIDDEN", f"调用方无权调用方案「{code}」", 403)
    p = pipelines.get_by_code(code)
    live = publishing.live_release(p["id"])
    if not live:
        return ok({"releaseId": None})
    tables = []
    for ds, s in live["snapshots"].items():
        meta, _ = publishing.load_snapshot(s["snapshot_id"]) if s else ({}, [])
        tables.append({"dataset": ds, "table": s["table_name"], "rows": meta.get("row_count"),
                       "checksum": (live.get("table_checksums") or {}).get(s["table_name"])})
    return ok({"releaseId": live["id"], "kind": live["kind"],
               "publishedAt": live["published_at"].isoformat() if live["published_at"] else None, "tables": tables})
