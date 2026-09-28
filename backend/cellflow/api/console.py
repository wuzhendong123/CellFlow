"""控制台接口（TECH_DESIGN §6.5.2；🔒 为需要操作口令的高危操作）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from cellflow.api.common import ok
from cellflow.api.deps import operator
from cellflow.api.security import OpContext, require_op
from cellflow.engine import expr as expr_mod
from cellflow.engine.dag import analyze
from cellflow.errors import CFError, not_found
from cellflow.meta.db import get_engine
from cellflow.meta.tables import parse_job, release
from cellflow.runtime import executor
from cellflow.services import (
    audit,
    callbacks,
    cleanup,
    client_apps,
    datasources,
    files,
    jobs,
    pipelines,
    publishing,
    regression,
    settings,
)

router = APIRouter(prefix="/api")


def _iso(v):
    return v.isoformat() if v else None


# ---------------- 方案 ----------------
class PipelineIn(BaseModel):
    code: str
    name: str
    datasourceId: int
    description: str | None = None
    sampleFileId: int | None = None


@router.get("/pipelines")
def list_pipelines(q: str | None = None, status: str | None = None, datasourceId: int | None = None):
    return ok(pipelines.list_pipelines(q, status, datasourceId))


@router.get("/pipelines/code-available")
def code_available(code: str):
    return ok({"available": pipelines.code_available(code)})


@router.post("/pipelines")
def create_pipeline(body: PipelineIn, who: str = Depends(operator)):
    return ok(pipelines.create(body.model_dump(), who))


@router.get("/pipelines/{pid}")
def get_pipeline(pid: int):
    return ok(pipelines.pipeline_json(pid))


@router.patch("/pipelines/{pid}")
def patch_pipeline(pid: int, body: dict):
    return ok(pipelines.update(pid, body))


class SampleIn(BaseModel):
    fileId: int


@router.put("/pipelines/{pid}/sample-file")
def set_sample(pid: int, body: SampleIn):
    return ok(pipelines.set_sample_file(pid, body.fileId))


class DraftIn(BaseModel):
    dsl: dict
    draftVersion: int


@router.get("/pipelines/{pid}/draft")
def get_draft(pid: int):
    return ok(pipelines.get_draft(pid))


@router.put("/pipelines/{pid}/draft")
def save_draft(pid: int, body: DraftIn, who: str = Depends(operator)):
    return ok(pipelines.save_draft(pid, body.dsl, body.draftVersion, who))


class SchemaIn(BaseModel):
    dsl: dict | None = None


@router.post("/pipelines/{pid}/schema")
def schema(pid: int, body: SchemaIn | None = None):
    return ok(pipelines.schema(pid, body.dsl if body else None))


class ExprIn(BaseModel):
    expr: str
    fields: dict[str, str]
    params: dict[str, dict[str, str]] | None = None
    sampleRows: list[dict] | None = None
    expect: str | None = None


@router.post("/expressions/check")
def check_expression(body: ExprIn):
    fields = {k: expr_mod.FieldInfo(v) for k, v in body.fields.items()}
    params = {a: {k: expr_mod.FieldInfo(v) for k, v in fs.items()} for a, fs in (body.params or {}).items()}
    out: dict[str, Any] = {"ok": True, "functions": expr_mod.function_catalog()}
    try:
        p = expr_mod.compile_expr(body.expr, fields, params)
        out["resultType"] = p.result_type
        if body.expect == "bool" and p.result_type not in ("bool", "null"):
            out.update(ok=False, message=f"结果应为真/假，实际为 {p.result_type}")
        preview = []
        for i, row in enumerate((body.sampleRows or [])[:5]):
            try:
                preview.append({"value": p.evaluate(row, None, {"index": i + 1})})
            except expr_mod.ExprError as e:
                preview.append({"error": e.message})
        out["preview"] = preview
    except expr_mod.ExprError as e:
        out.update(ok=False, message=e.message, hint=e.hint, fix=expr_mod.suggest_cast_fix(body.expr, fields, params) if e.hint else None)
    return ok(out)


@router.get("/expressions/functions")
def functions():
    return ok(expr_mod.function_catalog())


# ---------------- 目标表绑定 ----------------
@router.get("/datasources/{ds_id}/tables")
def tables(ds_id: int):
    return ok(datasources.list_tables(ds_id))


@router.get("/datasources/{ds_id}/tables/{table}")
def table(ds_id: int, table: str):
    return ok(datasources.describe_table(ds_id, table))


class BindingCheckIn(BaseModel):
    nodeId: str
    dsl: dict | None = None


@router.post("/pipelines/{pid}/bindings/check")
def binding_check(pid: int, body: BindingCheckIn):
    p = pipelines.get_pipeline(pid)
    dsl = body.dsl or pipelines.get_draft(pid)["dsl"]
    node = next((n for n in dsl.get("nodes", []) if n["id"] == body.nodeId), None)
    if not node:
        raise not_found("输出节点")
    a = analyze(dsl)
    edge = next((e for e in dsl.get("edges", []) if e["target"]["nodeId"] == body.nodeId), None)
    cols = []
    if edge:
        s = a.schemas.get((edge["source"]["nodeId"], edge["source"]["portId"]))
        cols = [c.to_json() for c in s.columns] if s else []
    return ok(datasources.check_binding(p["datasource_id"], (node.get("config") or {}).get("binding") or {}, cols, pid))


# ---------------- 试跑 ----------------
class TestIn(BaseModel):
    pipelineId: int
    fileId: int | None = None
    sampleRows: int | None = None
    untilNodeId: str | None = None
    dsl: dict | None = None
    preview: bool = False


@router.post("/jobs/test")
def test_run(body: TestIn, who: str = Depends(operator)):
    rows = body.sampleRows or (settings.get("preview.sampleRows") if body.preview else None)
    return ok(jobs.run_test(body.pipelineId, body.fileId, rows, body.untilNodeId, who, body.dsl))


@router.get("/pipelines/{pid}/recent-files")
def recent_files(pid: int, limit: int = 20):
    from cellflow.meta.tables import client_app, source_file

    with get_engine().connect() as c:
        rows = c.execute(select(parse_job.c.id, parse_job.c.file_id, source_file.c.file_name, client_app.c.name, parse_job.c.submitted_at)
                         .join(source_file, source_file.c.id == parse_job.c.file_id)
                         .outerjoin(client_app, client_app.c.id == parse_job.c.client_app_id)
                         .where(parse_job.c.pipeline_id == pid, parse_job.c.mode != "TEST")
                         .order_by(parse_job.c.id.desc()).limit(limit)).all()
    return ok([{"jobId": r[0], "fileId": r[1], "fileName": r[2], "client": r[3], "submittedAt": _iso(r[4])} for r in rows])


@router.get("/jobs/{job_id}/nodes/{node}/ports/{port}/rows")
def node_rows(job_id: int, node: str, port: str, offset: int = 0, limit: int = 50):
    return ok(jobs.output_page(job_id, node, port, offset, min(limit, 500)))


# ---------------- 版本 ----------------
@router.get("/pipelines/{pid}/revisions")
def revisions(pid: int):
    return ok(pipelines.list_revisions(pid))


@router.get("/pipelines/{pid}/revisions/{rev}")
def revision(pid: int, rev: int):
    r = pipelines.get_revision(pid, rev)
    return ok({"rev": r["rev"], "status": r["status"], "dsl": r["dsl"], "note": r["note"], "regression": r["regression_report"]})


@router.get("/pipelines/{pid}/revisions/{rev}/diff")
def revision_diff(pid: int, rev: int, against: str = "live"):
    a = pipelines.get_revision(pid, rev)["dsl"]
    if against == "draft":
        b = pipelines.get_draft(pid)["dsl"]
    elif against == "live":
        pr = pipelines.published_revision(pid)
        b = pr["dsl"] if pr else {"nodes": [], "edges": []}
    else:
        b = pipelines.get_revision(pid, int(against))["dsl"]
    return ok(pipelines.dsl_diff(b, a))


@router.get("/pipelines/{pid}/draft/diff")
def draft_diff(pid: int):
    pr = pipelines.published_revision(pid)
    return ok(pipelines.dsl_diff(pr["dsl"] if pr else {"nodes": [], "edges": []}, pipelines.get_draft(pid)["dsl"]))


@router.post("/pipelines/{pid}/regression")
def start_regression(pid: int, who: str = Depends(operator)):
    return ok(regression.start(pid, who))


@router.get("/regressions/{rid}")
def get_regression(rid: str):
    return ok(regression.get_report(rid))


class PublishIn(BaseModel):
    draftVersion: int
    note: str | None = None
    regressionId: str | None = None


@router.post("/pipelines/{pid}/publish")
def publish(pid: int, body: PublishIn, op: OpContext = Depends(require_op)):
    return ok(pipelines.publish(pid, body.draftVersion, body.note, op.operator, op.ip, body.regressionId))


class ReasonIn(BaseModel):
    reason: str | None = None


@router.post("/pipelines/{pid}/revisions/{rev}/republish")
def republish(pid: int, rev: int, body: ReasonIn, op: OpContext = Depends(require_op)):
    return ok(pipelines.republish(pid, rev, op.operator, op.ip, body.reason))


# ---------------- 任务 ----------------
@router.get("/jobs")
def list_jobs(pipelineId: int | None = None, clientAppId: int | None = None, status: str | None = None, mode: str | None = None,
              view: str | None = None, jobId: int | None = None, since: str | None = None, until: str | None = None,
              offset: int = 0, limit: int = 20):
    return ok(jobs.list_jobs(pipelineId, clientAppId, status, mode, view, jobId, since, until, offset, min(limit, 200)))


@router.get("/jobs/pending-count")
def pending_count():
    return ok({"count": jobs.pending_count()})


@router.get("/jobs/{job_id}")
def job_detail(job_id: int):
    d = jobs.job_detail(job_id)
    p = pipelines.get_pipeline(d["pipelineId"])
    d["pipelineCode"] = p["code"]
    if d["revisionId"]:
        from cellflow.meta.tables import pipeline_revision

        with get_engine().connect() as c:
            d["rev"] = c.execute(select(pipeline_revision.c.rev).where(pipeline_revision.c.id == d["revisionId"])).scalar()
    return ok(d)


@router.get("/jobs/{job_id}/issues")
def job_issues(job_id: int, node: str | None = None, severity: str | None = None, sheet: str | None = None, rule: str | None = None,
               offset: int = 0, limit: int = 100):
    return ok(jobs.list_issues(job_id, node, severity, sheet, rule, offset, min(limit, 500)))


@router.get("/jobs/{job_id}/changes")
def job_changes(job_id: int, table: str | None = None, op: str | None = None, offset: int = 0, limit: int = 100):
    j = jobs.get_job(job_id)
    if not j["release_id"]:
        return ok({"total": 0, "offset": 0, "rows": []})
    with get_engine().connect() as c:
        uri = c.execute(select(release.c.change_uri).where(release.c.id == j["release_id"])).scalar()
    return ok(publishing.read_changes(uri, table, op, offset, min(limit, 500)))


@router.get("/jobs/{job_id}/callbacks")
def job_callbacks(job_id: int):
    return ok(callbacks.attempts_for_job(job_id))


@router.get("/jobs/{job_id}/file")
def job_file(job_id: int):
    j = jobs.get_job(job_id)
    data, name = files.file_bytes(j["file_id"])
    from urllib.parse import quote

    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/jobs/{job_id}/force-publish")
def force_publish(job_id: int, body: ReasonIn, op: OpContext = Depends(require_op)):
    return ok(executor.force_publish(job_id, op.operator, op.ip, body.reason or ""))


# ---------------- 发布历史与回滚 ----------------
@router.get("/pipelines/{pid}/releases")
def releases(pid: int):
    live = publishing.live_release(pid)
    out = []
    for r in executor.rollback_targets(pid):
        out.append({"id": r["id"], "kind": r["kind"], "status": r["status"], "jobId": r["job_id"], "rollbackTo": r["rollback_to"],
                    "prevReleaseId": r["prev_release_id"], "operator": r["operator"], "reason": r["reason"],
                    "changeSummary": r["change_summary"], "tables": [t["table_name"] for t in publishing.release_tables(r["id"])],
                    "createdAt": _iso(r["created_at"]), "publishedAt": _iso(r["published_at"]),
                    "live": bool(live and live["id"] == r["id"]),
                    "available": r["status"] == "PUBLISHED" and cleanup.release_available(r["id"])})
    p = pipelines.pipeline_json(pid)
    return ok({"liveReleaseId": live["id"] if live else None, "frozen": p["frozen"], "rows": out})


@router.get("/releases/{rid}/changes")
def release_changes(rid: int, table: str | None = None, op: str | None = None, offset: int = 0, limit: int = 100):
    with get_engine().connect() as c:
        uri = c.execute(select(release.c.change_uri).where(release.c.id == rid)).scalar()
    return ok(publishing.read_changes(uri, table, op, offset, min(limit, 500)))


class RollbackPreviewIn(BaseModel):
    targetReleaseId: int


@router.post("/pipelines/{pid}/rollback/preview")
def rollback_preview(pid: int, body: RollbackPreviewIn):
    if not cleanup.release_available(body.targetReleaseId):
        raise CFError("SNAPSHOT_EXPIRED", "目标版本已超过保留期", 410)
    return ok(executor.rollback_preview(pid, body.targetReleaseId))


class RollbackIn(BaseModel):
    targetReleaseId: int
    expectedLiveReleaseId: int
    reason: str
    freeze: bool = True
    confirmDrift: bool = False


@router.post("/pipelines/{pid}/rollback")
def rollback(pid: int, body: RollbackIn, op: OpContext = Depends(require_op)):
    if not cleanup.release_available(body.targetReleaseId):
        raise CFError("SNAPSHOT_EXPIRED", "目标版本已超过保留期", 410)
    return ok(executor.rollback(pid, body.targetReleaseId, body.expectedLiveReleaseId, op.operator, op.ip, body.reason,
                                body.freeze, body.confirmDrift))


@router.post("/pipelines/{pid}/freeze")
def freeze(pid: int, body: ReasonIn, op: OpContext = Depends(require_op)):
    return ok(pipelines.freeze(pid, True, op.operator, op.ip, body.reason))


@router.post("/pipelines/{pid}/unfreeze")
def unfreeze(pid: int, body: ReasonIn, op: OpContext = Depends(require_op)):
    return ok(pipelines.freeze(pid, False, op.operator, op.ip, body.reason))


# ---------------- 管理：数据源 / 调用方 / 设置 / 审计 ----------------
class DatasourceIn(BaseModel):
    name: str
    dbName: str
    mode: str = "REF"  # REF：引用环境变量；DIRECT：直接填写（口令加密存储）
    hostRef: str | None = None
    credentialRef: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None  # 只写不读；编辑时留空表示不修改

    def to_service(self) -> dict:
        return {"name": self.name, "db_name": self.dbName, "conn_mode": self.mode, "host_ref": self.hostRef,
                "credential_ref": self.credentialRef, "host": self.host, "port": self.port, "username": self.username,
                "password": self.password}


_ds_json = datasources.public_json


@router.get("/datasources")
def list_datasources():
    return ok([_ds_json(d) for d in datasources.list_all()])


@router.post("/datasources")
def create_datasource(body: DatasourceIn, op: OpContext = Depends(require_op)):
    d = datasources.save(body.to_service())
    with get_engine().begin() as c:
        audit.record(c, op.operator, op.ip, "EDIT_DATASOURCE", f"datasource:{d['name']}", {"created": _ds_json(d)})
    return ok(_ds_json(d))


@router.put("/datasources/{ds_id}")
def update_datasource(ds_id: int, body: DatasourceIn, op: OpContext = Depends(require_op)):
    before = _ds_json(datasources.get(ds_id))
    d = datasources.save(body.to_service(), ds_id)
    with get_engine().begin() as c:
        audit.record(c, op.operator, op.ip, "EDIT_DATASOURCE", f"datasource:{d['name']}",
                     {"before": before, "after": _ds_json(d), "passwordChanged": bool(body.password)})
    return ok(_ds_json(d))


@router.delete("/datasources/{ds_id}")
def delete_datasource(ds_id: int, op: OpContext = Depends(require_op)):
    d = datasources.get(ds_id)
    datasources.delete(ds_id)
    with get_engine().begin() as c:
        audit.record(c, op.operator, op.ip, "EDIT_DATASOURCE", f"datasource:{d['name']}", {"deleted": _ds_json(d)})
    return ok(None)


@router.post("/datasources/{ds_id}/test")
def test_datasource(ds_id: int):
    return ok(datasources.test_connection(ds_id))


class ClientIn(BaseModel):
    name: str
    secretRef: str
    allowedPipelines: list[str] = []
    callbackAllowlist: list[str] = []
    rateLimitPerMin: int | None = None
    enabled: bool = True


@router.get("/client-apps")
def list_clients():
    return ok(client_apps.list_all())


@router.post("/client-apps")
def create_client(body: ClientIn, op: OpContext = Depends(require_op)):
    a = client_apps.save(body.model_dump())
    with get_engine().begin() as c:
        audit.record(c, op.operator, op.ip, "EDIT_CLIENT", f"client:{a['appKey']}", {"created": a})
    return ok(a)


@router.put("/client-apps/{app_id}")
def update_client(app_id: int, body: ClientIn, op: OpContext = Depends(require_op)):
    before = client_apps._json(client_apps.get(app_id))
    a = client_apps.save(body.model_dump(), app_id)
    with get_engine().begin() as c:
        audit.record(c, op.operator, op.ip, "EDIT_CLIENT", f"client:{a['appKey']}", {"before": before, "after": a})
    return ok(a)


@router.get("/settings")
def get_settings():
    return ok(settings.describe())


@router.put("/settings")
def put_settings(body: dict, op: OpContext = Depends(require_op)):
    diff = settings.update(body, op.operator)
    with get_engine().begin() as c:
        audit.record(c, op.operator, op.ip, "EDIT_SETTING", "settings", {k: {"before": a, "after": b} for k, (a, b) in diff.items()})
    return ok(settings.describe())


@router.get("/audit-logs")
def audit_logs(action: str | None = None, target: str | None = None, operator_: str | None = None, offset: int = 0,
               limit: int = 50, request: Request = None):
    op_filter = request.query_params.get("operator") if request else operator_
    return ok(audit.query(action, target, op_filter, offset, min(limit, 200)))
