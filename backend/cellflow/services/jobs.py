"""解析任务：试跑（T07）、执行与结果存取（T09~T11）。"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from typing import Any

from sqlalchemy import and_, func, select

from cellflow.engine.dag import RunResult, execute
from cellflow.engine.dataset import Dataset, split_addr
from cellflow.engine.types import to_jsonable
from cellflow.errors import CFError, not_found
from cellflow.meta.db import get_engine
from cellflow.meta.tables import job_issue, parse_job
from cellflow.services import files, pipelines, settings
from cellflow.storage import get_storage

TERMINAL = {"PUBLISHED", "NO_CHANGE", "SUPERSEDED", "VALIDATED", "FAILED_VALIDATION", "FAILED_GUARD", "FAILED_WRITE",
            "FAILED", "CANCELLED"}
PENDING_STATUSES = {"FAILED_VALIDATION", "FAILED_GUARD", "FAILED_WRITE", "FAILED"}


def _iso(v):
    return v.isoformat() if v else None


def get_job(job_id: int) -> dict:
    with get_engine().connect() as c:
        r = c.execute(select(parse_job).where(parse_job.c.id == job_id)).mappings().first()
    if not r:
        raise not_found("任务")
    return dict(r)


def update_job(job_id: int, **vals) -> None:
    with get_engine().begin() as c:
        c.execute(parse_job.update().where(parse_job.c.id == job_id).values(**vals))


def save_issues(job_id: int, issues: list[dict]) -> None:
    rows = []
    for i in issues:
        sheet, cell = split_addr(i.get("cell"))
        v = i.get("value")
        rows.append({
            "job_id": job_id, "node_id": (i.get("node") or "")[:64], "rule_id": i.get("rule"), "severity": i["severity"],
            "code": i["code"][:32], "row_id": (i.get("rid") or None) and str(i["rid"])[:64], "sheet": i.get("sheet") or sheet,
            "cell": cell, "field": i.get("field"), "value_text": None if v is None else str(to_jsonable(v))[:1024],
            "message": str(i["message"])[:1024],
            "related_cells": [x for x in (i.get("related") or []) if isinstance(x, str)][:50] or None,
        })
    if rows:
        with get_engine().begin() as c:
            for k in range(0, len(rows), 500):
                c.execute(job_issue.insert(), rows[k:k + 500])


def issue_json(r: dict) -> dict:
    return {"id": r["id"], "node": r["node_id"], "rule": r["rule_id"], "severity": r["severity"], "code": r["code"],
            "rid": r["row_id"], "sheet": r["sheet"], "cell": r["cell"], "field": r["field"], "value": r["value_text"],
            "message": r["message"], "relatedCells": r["related_cells"]}


def list_issues(job_id: int, node: str | None = None, severity: str | None = None, sheet: str | None = None,
                rule: str | None = None, offset: int = 0, limit: int = 100) -> dict:
    q = select(job_issue).where(job_issue.c.job_id == job_id)
    cq = select(func.count()).select_from(job_issue).where(job_issue.c.job_id == job_id)
    for col, v in ((job_issue.c.node_id, node), (job_issue.c.severity, severity), (job_issue.c.sheet, sheet),
                   (job_issue.c.rule_id, rule)):
        if v:
            q, cq = q.where(col == v), cq.where(col == v)
    order = func.field(job_issue.c.severity, "ERROR", "WARN", "INFO")
    with get_engine().connect() as c:
        total = c.execute(cq).scalar()
        rows = c.execute(q.order_by(order, job_issue.c.id).offset(offset).limit(limit)).mappings().all()
    return {"total": total, "offset": offset, "rows": [issue_json(dict(r)) for r in rows]}


def _output_key(job_id: int, node: str, port: str) -> str:
    return f"jobs/{job_id}/outputs/{node}/{port}.json.gz"


def save_outputs(job_id: int, res: RunResult) -> None:
    st = get_storage()
    for (node, port), ds in res.outputs.items():
        payload = {"columns": ds.schema_json(), "rows": [to_jsonable(r) for r in ds.rows], "lineage": ds.lineage, "meta": ds.meta}
        st.put(_output_key(job_id, node, port), gzip.compress(json.dumps(payload, ensure_ascii=False).encode()))


def output_page(job_id: int, node: str, port: str, offset: int, limit: int) -> dict:
    key = _output_key(job_id, node, port)
    st = get_storage()
    if not st.exists(key):
        raise not_found("节点输出")
    data = json.loads(gzip.decompress(st.get(key)))
    rows = [{"_rid": data["meta"][i].get("rid"), "data": data["rows"][i], "_lineage": data["lineage"][i]}
            for i in range(offset, min(offset + limit, len(data["rows"])))]
    return {"total": len(data["rows"]), "offset": offset, "columns": data["columns"], "rows": rows}


def summarize(res: RunResult) -> dict:
    return {"error": res.error_count, "warn": res.warn_count,
            "info": sum(1 for i in res.issues if i["severity"] == "INFO")}


def run_test(pid: int, file_id: int | None, sample_rows: int | None, until_node: str | None, who: str,
             dsl: dict | None = None) -> dict:
    """试跑草稿（mode=TEST，永不写表）：记录 DSL 快照以便复现；完整试跑还给出与线上的变更预判（C10）。"""
    p = pipelines.get_pipeline(pid)
    fid = file_id or p["sample_file_id"]
    if not fid:
        raise CFError("NO_SAMPLE_FILE", "请先上传样例文件", 422)
    files.get_file(fid)
    dsl = dsl or pipelines.get_draft(pid)["dsl"]
    dsl = {k: v for k, v in dsl.items() if k != "sampleFileId"}
    with get_engine().begin() as c:
        jid = c.execute(parse_job.insert().values(
            pipeline_id=pid, revision_id=None, dsl_snapshot=dsl, file_id=fid, mode="TEST", status="RUNNING",
            operator=who, started_at=func.now(3),
        )).inserted_primary_key[0]
    try:
        s = settings.get_all()
        rows = sample_rows if sample_rows else None
        res = execute(dsl, files.load_workbook(fid), s, sample_rows=rows, until_node=until_node)
        save_outputs(jid, res)
        save_issues(jid, res.issues)
        result: dict[str, Any] = {"sampled": bool(rows), "sampleRows": rows}
        if not rows and not until_node:
            from cellflow.services import publishing

            result["forecast"] = publishing.forecast(p, res)
        update_job(jid, status="VALIDATED" if res.error_count == 0 else "FAILED_VALIDATION", error_summary=summarize(res),
                   metrics={"nodes": res.metrics, "locateReport": res.locate_report, "skipped": res.skipped},
                   result=to_jsonable(result), finished_at=func.now(3))
    except CFError:
        update_job(jid, status="FAILED", finished_at=func.now(3))
        raise
    except Exception as e:
        update_job(jid, status="FAILED", error_summary={"error": 1, "exception": type(e).__name__, "message": str(e)[:500]},
                   finished_at=func.now(3))
        raise
    return job_detail(jid)


def job_detail(job_id: int) -> dict:
    j = get_job(job_id)
    return {
        "jobId": j["id"], "pipelineId": j["pipeline_id"], "revisionId": j["revision_id"], "fileId": j["file_id"],
        "mode": j["mode"], "status": j["status"], "operator": j["operator"], "clientAppId": j["client_app_id"],
        "supersededBy": j["superseded_by"], "releaseId": j["release_id"], "issues": j["error_summary"] or {},
        "metrics": j["metrics"] or {}, "result": j["result"] or {}, "submittedAt": _iso(j["submitted_at"]),
        "startedAt": _iso(j["started_at"]), "finishedAt": _iso(j["finished_at"]),
    }


def ds_from_json(payload: dict) -> Dataset:
    from cellflow.engine.dataset import ColumnSchema

    cols = [ColumnSchema(c["field"], c["type"], c.get("isKey", False)) for c in payload["columns"]]
    return Dataset(cols, payload["rows"], payload["lineage"], payload["meta"])


def now() -> dt.datetime:
    return dt.datetime.now()


# ======================= Open API 提交（T11） =======================
def submit(app: dict, meta: dict, data: bytes, file_name: str) -> dict:
    from sqlalchemy import and_

    from cellflow.meta.tables import pipeline_revision
    from cellflow.runtime.queue import dispatch
    from cellflow.services.callbacks import allowed_callback, enqueue_job_callback

    code = meta.get("pipelineCode") or ""
    if code not in (app["allowed_pipelines"] or []):
        raise CFError("AUTH_PIPELINE_FORBIDDEN", f"调用方无权调用方案「{code}」", 403)
    mode = meta.get("mode", "EXECUTE")
    if mode not in ("EXECUTE", "VALIDATE_ONLY"):
        raise CFError("INVALID_REQUEST", "mode 只能是 EXECUTE 或 VALIDATE_ONLY", 400)
    idem = (meta.get("idempotencyKey") or "").strip()[:128] or None
    if idem:
        with get_engine().connect() as c:
            existing = c.execute(select(parse_job.c.id).where(and_(parse_job.c.client_app_id == app["id"],
                                                                   parse_job.c.idempotency_key == idem))).scalar()
        if existing:
            return {"jobId": existing, "status": get_job(existing)["status"], "duplicate": True}
    cb = meta.get("callbackUrl")
    if cb and not allowed_callback(cb, app["callback_allowlist"]):
        raise CFError("CALLBACK_NOT_ALLOWED", "回调地址不在白名单中", 400)
    p = pipelines.get_by_code(code)
    if not p["published_rev_id"]:
        raise CFError("PIPELINE_NOT_PUBLISHED", f"方案「{code}」没有已发布版本", 422)
    if mode == "EXECUTE" and p["frozen"]:
        raise CFError("PIPELINE_FROZEN", f"方案「{code}」已冻结，暂不接收写表任务", 422)
    rev_id = p["published_rev_id"]
    if meta.get("pinRevision"):
        with get_engine().connect() as c:
            rev_id = c.execute(select(pipeline_revision.c.id).where(and_(
                pipeline_revision.c.pipeline_id == p["id"], pipeline_revision.c.rev == int(meta["pinRevision"])))).scalar()
        if not rev_id:
            raise CFError("INVALID_REQUEST", "指定的方案版本不存在", 400)
    f = files.save_upload(data, file_name, app["app_key"])
    superseded: list[int] = []
    with get_engine().begin() as c:
        jid = c.execute(parse_job.insert().values(
            pipeline_id=p["id"], revision_id=rev_id, client_app_id=app["id"], idempotency_key=idem, file_id=f["fileId"],
            mode=mode, status="QUEUED", operator=(meta.get("operator") or "")[:64] or None, callback_url=cb,
        )).inserted_primary_key[0]
        if mode == "EXECUTE":  # D12：同方案只执行最新的一个
            superseded = [r[0] for r in c.execute(select(parse_job.c.id).where(and_(
                parse_job.c.pipeline_id == p["id"], parse_job.c.mode == "EXECUTE", parse_job.c.status == "QUEUED",
                parse_job.c.id < jid))).all()]
            if superseded:
                c.execute(parse_job.update().where(parse_job.c.id.in_(superseded)).values(
                    status="SUPERSEDED", superseded_by=jid, finished_at=func.now(3)))
    for s in superseded:
        enqueue_job_callback(s)
    dispatch(jid)
    return {"jobId": jid, "status": get_job(jid)["status"]}


def list_jobs(pipeline_id: int | None = None, client_app_id: int | None = None, status: str | None = None,
              mode: str | None = None, view: str | None = None, job_id: int | None = None, since: str | None = None,
              until: str | None = None, offset: int = 0, limit: int = 20) -> dict:
    from cellflow.meta.tables import client_app, pipeline

    q = select(parse_job, pipeline.c.code.label("pipeline_code"), client_app.c.name.label("client_name")).join(
        pipeline, pipeline.c.id == parse_job.c.pipeline_id).outerjoin(client_app, client_app.c.id == parse_job.c.client_app_id)
    conds = []
    if pipeline_id:
        conds.append(parse_job.c.pipeline_id == pipeline_id)
    if client_app_id:
        conds.append(parse_job.c.client_app_id == client_app_id)
    if status:
        conds.append(parse_job.c.status == status)
    if mode:
        conds.append(parse_job.c.mode == mode)
    if job_id:
        conds.append(parse_job.c.id == job_id)
    if since:
        conds.append(parse_job.c.submitted_at >= since)
    if until:
        conds.append(parse_job.c.submitted_at <= until)
    if view == "pending":
        conds.append(pending_condition())
    for cnd in conds:
        q = q.where(cnd)
    with get_engine().connect() as c:
        total = c.execute(select(func.count()).select_from(q.subquery())).scalar()
        rows = [dict(r) for r in c.execute(q.order_by(parse_job.c.id.desc()).offset(offset).limit(limit)).mappings()]
        cq = select(parse_job.c.status, func.count()).where(parse_job.c.mode != "TEST").group_by(parse_job.c.status)
        if pipeline_id:
            cq = cq.where(parse_job.c.pipeline_id == pipeline_id)
        counts = dict(c.execute(cq).all())
    out = []
    for r in rows:
        es = r["error_summary"] or {}
        res = r["result"] or {}
        out.append({
            "jobId": r["id"], "pipelineId": r["pipeline_id"], "pipelineCode": r["pipeline_code"],
            "client": r["client_name"] or ("控制台" if r["mode"] == "TEST" else None), "mode": r["mode"],
            "revisionId": r["revision_id"], "status": r["status"], "supersededBy": r["superseded_by"],
            "changes": {k: sum((t.get("changes") or {}).get(k, 0) for t in res.get("tables", [])) for k in ("c", "u", "d")},
            "errors": es.get("error", 0), "warns": es.get("warn", 0), "submittedAt": _iso(r["submitted_at"]),
            "finishedAt": _iso(r["finished_at"]), "releaseId": r["release_id"],
            "callbackFailed": False,
        })
    if out:
        from cellflow.meta.tables import outbox

        with get_engine().connect() as c:
            failed = {x[0] for x in c.execute(select(outbox.c.job_id).where(and_(
                outbox.c.job_id.in_([o["jobId"] for o in out]), outbox.c.status == "FAILED"))).all()}
        for o in out:
            o["callbackFailed"] = o["jobId"] in failed
    return {"total": total, "offset": offset, "rows": out, "statusCounts": counts}


def pending_condition():
    """待处理（W4）：被拦截 / 校验失败 / 写入失败 / 系统失败 / 回调多次失败，且之后没有同方案的成功写表任务。"""
    from sqlalchemy import and_, exists, or_
    from sqlalchemy.orm import aliased

    from cellflow.meta.tables import outbox

    later = aliased(parse_job)
    no_later_success = ~exists().where(and_(later.c.pipeline_id == parse_job.c.pipeline_id, later.c.id > parse_job.c.id,
                                            later.c.status.in_(["PUBLISHED", "NO_CHANGE"])))
    cb_failed = exists().where(and_(outbox.c.job_id == parse_job.c.id, outbox.c.status == "FAILED"))
    return and_(parse_job.c.mode != "TEST", or_(parse_job.c.status.in_(sorted(PENDING_STATUSES)), cb_failed), no_later_success)


def pending_count() -> int:
    with get_engine().connect() as c:
        return c.execute(select(func.count()).select_from(parse_job).where(pending_condition())).scalar()
