"""方案、草稿、版本（T06、T16；TECH_DESIGN §3、§6.5.2）。"""

from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy import and_, func, select

from cellflow.engine.dag import DSL_VERSION, analyze
from cellflow.errors import CFError, not_found
from cellflow.meta.db import get_engine
from cellflow.meta.tables import (
    datasource,
    live_state,
    parse_job,
    pipeline,
    pipeline_draft,
    pipeline_revision,
    table_binding,
    table_owner,
)
from cellflow.services import audit

CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def empty_dsl(code: str) -> dict:
    return {"dslVersion": DSL_VERSION, "pipelineCode": code, "nodes": [], "edges": []}


def _iso(v):
    return v.isoformat() if v else None


def list_pipelines(q: str | None = None, status: str | None = None, ds_id: int | None = None) -> list[dict]:
    with get_engine().connect() as c:
        rows = [dict(r) for r in c.execute(select(pipeline).order_by(pipeline.c.id.desc())).mappings()]
        drafts = {r["pipeline_id"]: r for r in c.execute(select(pipeline_draft)).mappings()}
        revs = {r["id"]: r for r in c.execute(select(pipeline_revision.c.id, pipeline_revision.c.rev, pipeline_revision.c.dsl)
                                             .where(pipeline_revision.c.status == "PUBLISHED")).mappings()}
        last_jobs = {}
        sub = select(parse_job.c.pipeline_id, func.max(parse_job.c.id).label("mid")).where(parse_job.c.mode != "TEST").group_by(parse_job.c.pipeline_id).subquery()
        for r in c.execute(select(parse_job.c.pipeline_id, parse_job.c.status, parse_job.c.submitted_at).join(
                sub, and_(sub.c.mid == parse_job.c.id))).all():
            last_jobs[r[0]] = {"status": r[1], "submittedAt": _iso(r[2])}
        dsn = {r[0]: r[1] for r in c.execute(select(datasource.c.id, datasource.c.name)).all()}
    out = []
    for r in rows:
        pub = revs.get(r["published_rev_id"])
        d = drafts.get(r["id"])
        st = "FROZEN" if r["frozen"] else ("NORMAL" if pub else "UNPUBLISHED")
        if q and q.lower() not in (r["code"] + r["name"]).lower():
            continue
        if status and status != st:
            continue
        if ds_id and ds_id != r["datasource_id"]:
            continue
        out.append({
            "id": r["id"], "code": r["code"], "name": r["name"], "datasourceId": r["datasource_id"],
            "datasourceName": dsn.get(r["datasource_id"]), "publishedRev": pub["rev"] if pub else None,
            "draftChanged": bool(d and d["dsl"].get("nodes") and (not pub or _norm_dsl(d["dsl"]) != _norm_dsl(pub["dsl"]))),
            "status": st, "lastJob": last_jobs.get(r["id"]), "updatedAt": _iso(r["updated_at"]),
        })
    return out


def _norm_dsl(dsl: dict) -> dict:
    x = copy.deepcopy(dsl)
    x.pop("sampleFileId", None)
    for n in x.get("nodes", []):
        n.pop("position", None)
    return x


def get_pipeline(pid: int) -> dict:
    with get_engine().connect() as c:
        r = c.execute(select(pipeline).where(pipeline.c.id == pid)).mappings().first()
    if not r:
        raise not_found("方案")
    return dict(r)


def get_by_code(code: str) -> dict:
    with get_engine().connect() as c:
        r = c.execute(select(pipeline).where(pipeline.c.code == code)).mappings().first()
    if not r:
        raise CFError("PIPELINE_NOT_FOUND", f"方案「{code}」不存在", 404)
    return dict(r)


def pipeline_json(pid: int) -> dict:
    p = get_pipeline(pid)
    with get_engine().connect() as c:
        rev = None
        if p["published_rev_id"]:
            rev = c.execute(select(pipeline_revision.c.rev, pipeline_revision.c.published_at, pipeline_revision.c.published_by)
                            .where(pipeline_revision.c.id == p["published_rev_id"])).first()
        live = c.execute(select(live_state.c.release_id).where(live_state.c.pipeline_id == pid)).scalar()
    return {
        "id": p["id"], "code": p["code"], "name": p["name"], "description": p["description"], "datasourceId": p["datasource_id"],
        "sampleFileId": p["sample_file_id"], "frozen": bool(p["frozen"]), "owner": p["owner"],
        "publishedRev": rev[0] if rev else None, "publishedAt": _iso(rev[1]) if rev else None,
        "publishedBy": rev[2] if rev else None, "liveReleaseId": live,
    }


def create(data: dict, who: str) -> dict:
    code = (data.get("code") or "").strip()
    if not CODE.match(code):
        raise CFError("INVALID_REQUEST", "方案编码只能包含小写字母、数字、下划线，以字母开头，2~64 位", 400)
    if not data.get("name"):
        raise CFError("INVALID_REQUEST", "方案名称必填", 400)
    with get_engine().begin() as c:
        if not c.execute(select(datasource.c.id).where(datasource.c.id == data.get("datasourceId"))).first():
            raise CFError("INVALID_REQUEST", "数据源不存在", 400)
        if c.execute(select(pipeline.c.id).where(pipeline.c.code == code)).first():
            raise CFError("PIPELINE_CODE_EXISTS", f"方案编码「{code}」已存在", 409)
        pid = c.execute(pipeline.insert().values(
            code=code, name=data["name"][:128], datasource_id=data["datasourceId"], description=data.get("description"),
            owner=who, sample_file_id=data.get("sampleFileId"),
        )).inserted_primary_key[0]
        c.execute(pipeline_draft.insert().values(pipeline_id=pid, dsl=empty_dsl(code), base_rev=None, version=1, updated_by=who))
    return pipeline_json(pid)


def code_available(code: str) -> bool:
    with get_engine().connect() as c:
        return CODE.match(code or "") is not None and not c.execute(select(pipeline.c.id).where(pipeline.c.code == code)).first()


def update(pid: int, data: dict) -> dict:
    get_pipeline(pid)
    vals = {k: data[k] for k in ("name", "description") if k in data}
    if vals:
        with get_engine().begin() as c:
            c.execute(pipeline.update().where(pipeline.c.id == pid).values(**vals))
    return pipeline_json(pid)


def set_sample_file(pid: int, file_id: int) -> dict:
    from cellflow.engine.dag import execute
    from cellflow.services import files, settings

    get_pipeline(pid)
    files.get_file(file_id)
    with get_engine().begin() as c:
        c.execute(pipeline.update().where(pipeline.c.id == pid).values(sample_file_id=file_id))
    draft = get_draft(pid)
    report = []
    if draft["dsl"].get("nodes"):
        res = execute(_sources_only(draft["dsl"]), files.load_workbook(file_id), settings.get_all(), sample_rows=1)
        report = res.locate_report
    return {"sampleFileId": file_id, "locateReport": report}


def _sources_only(dsl: dict) -> dict:
    return {"nodes": [n for n in dsl.get("nodes", []) if n.get("type") == "EXCEL_SOURCE"], "edges": []}


def get_draft(pid: int) -> dict:
    p = get_pipeline(pid)
    with get_engine().connect() as c:
        d = c.execute(select(pipeline_draft).where(pipeline_draft.c.pipeline_id == pid)).mappings().first()
    dsl = copy.deepcopy(d["dsl"]) if d else empty_dsl(p["code"])
    dsl["sampleFileId"] = p["sample_file_id"]
    return {"dsl": dsl, "draftVersion": d["version"] if d else 0, "baseRev": d["base_rev"] if d else None,
            "updatedBy": d["updated_by"] if d else None, "updatedAt": _iso(d["updated_at"]) if d else None}


def _sink_tables(dsl: dict) -> set[str]:
    return {((n.get("config") or {}).get("binding") or {}).get("table") for n in dsl.get("nodes", [])
            if n.get("type") == "SINK" and ((n.get("config") or {}).get("binding") or {}).get("table")}


def save_draft(pid: int, dsl: dict, draft_version: int, who: str) -> dict:
    p = get_pipeline(pid)
    dsl = copy.deepcopy(dsl)
    dsl.pop("sampleFileId", None)
    dsl["pipelineCode"] = p["code"]
    dsl.setdefault("dslVersion", DSL_VERSION)
    with get_engine().begin() as c:
        cur = c.execute(select(pipeline_draft).where(pipeline_draft.c.pipeline_id == pid).with_for_update()).mappings().first()
        if cur["version"] != draft_version:
            raise CFError("DSL_REV_CONFLICT", f"草稿已被 {cur['updated_by']} 于 {_iso(cur['updated_at'])} 更新", 409,
                          {"updatedBy": cur["updated_by"], "updatedAt": _iso(cur["updated_at"]), "draftVersion": cur["version"]})
        c.execute(pipeline_draft.update().where(pipeline_draft.c.pipeline_id == pid).values(
            dsl=dsl, version=cur["version"] + 1, updated_by=who))
        conflicts = _claim_tables(c, p, dsl)
    analysis = analyze(dsl).to_json()
    analysis["errors"] += conflicts
    analysis["ok"] = analysis["ok"] and not conflicts
    return {"draftVersion": draft_version + 1, "analysis": analysis}


def _claim_tables(c, p: dict, dsl: dict) -> list[dict]:
    """草稿保存时登记表占用；草稿与生效版本都不再引用的表释放（C5、D9）。"""
    wanted = _sink_tables(dsl)
    pub_tables: set[str] = set()
    if p["published_rev_id"]:
        rev = c.execute(select(pipeline_revision.c.dsl).where(pipeline_revision.c.id == p["published_rev_id"])).scalar()
        pub_tables = _sink_tables(rev or {})
    owners = {r[0]: r[1] for r in c.execute(select(table_owner.c.table_name, table_owner.c.pipeline_id).where(
        table_owner.c.datasource_id == p["datasource_id"])).all()}
    errors = []
    for t in wanted:
        o = owners.get(t)
        if o is None:
            c.execute(table_owner.insert().values(datasource_id=p["datasource_id"], table_name=t, pipeline_id=p["id"]))
        elif o != p["id"]:
            code = c.execute(select(pipeline.c.code).where(pipeline.c.id == o)).scalar()
            errors.append({"code": "TABLE_OWNED", "message": f"目标表「{t}」已被方案「{code}」占用（D9）"})
    for t, o in owners.items():
        if o == p["id"] and t not in wanted and t not in pub_tables:
            c.execute(table_owner.delete().where(and_(table_owner.c.datasource_id == p["datasource_id"], table_owner.c.table_name == t)))
    return errors


def schema(pid: int, dsl: dict | None = None) -> dict:
    return analyze(dsl or get_draft(pid)["dsl"]).to_json()


def publish(pid: int, draft_version: int, note: str | None, who: str, ip: str, regression_id: str | None = None) -> dict:
    from cellflow.services import datasources

    p = get_pipeline(pid)
    d = get_draft(pid)
    if d["draftVersion"] != draft_version:
        raise CFError("DSL_REV_CONFLICT", "草稿已被更新，请刷新后再发布", 409)
    dsl = d["dsl"]
    dsl.pop("sampleFileId", None)
    a = analyze(dsl)
    errors = list(a.errors)
    sinks = [n for n in dsl.get("nodes", []) if n.get("type") == "SINK"]
    if not sinks:
        errors.append({"code": "DSL_INVALID", "message": "方案至少需要一个输出节点"})
    modes = {((n.get("config") or {}).get("binding") or {}).get("strategy", "SWAP") for n in sinks}
    if len(modes) > 1:
        errors.append({"code": "DSL_INVALID", "message": "同一个方案的输出表写入方式需要一致（都用整表替换，或都用按分区替换），以保证多张表一起成功或一起失败"})
    for n in sinks:
        b = (n.get("config") or {}).get("binding") or {}
        edge = next((e for e in dsl.get("edges", []) if e["target"]["nodeId"] == n["id"]), None)
        schema_cols = []
        if edge:
            s = a.schemas.get((edge["source"]["nodeId"], edge["source"]["portId"]))
            schema_cols = [c.to_json() for c in s.columns] if s else []
        chk = datasources.check_binding(p["datasource_id"], b, schema_cols, pid)
        errors += [{**e, "node": n["id"]} for e in chk["errors"]]
    if errors:
        raise CFError("DSL_INVALID", "方案存在错误，不能发布", 422, {"errors": errors})
    with get_engine().begin() as c:
        c.execute(select(pipeline.c.id).where(pipeline.c.id == pid).with_for_update())
        rev = (c.execute(select(func.max(pipeline_revision.c.rev)).where(pipeline_revision.c.pipeline_id == pid)).scalar() or 0) + 1
        report = None
        if regression_id:
            from cellflow.services import regression

            report = regression.get_report(regression_id)
        rid = c.execute(pipeline_revision.insert().values(
            pipeline_id=pid, rev=rev, dsl=dsl, dsl_schema_ver=dsl.get("dslVersion", DSL_VERSION), status="PUBLISHED",
            note=note, regression_report=report, created_by=d["updatedBy"] or who, published_by=who, published_at=func.now(3),
        )).inserted_primary_key[0]
        _activate(c, p, rid, dsl)
        c.execute(pipeline_draft.update().where(pipeline_draft.c.pipeline_id == pid).values(base_rev=rev))
        audit.record(c, who, ip, "PUBLISH_REVISION", f"pipeline:{p['code']}", {"rev": rev, "note": note}, note)
    return {"rev": rev, "revisionId": rid}


def _activate(c, p: dict, rev_id: int, dsl: dict) -> None:
    c.execute(pipeline_revision.update().where(and_(pipeline_revision.c.pipeline_id == p["id"],
                                                    pipeline_revision.c.status == "PUBLISHED",
                                                    pipeline_revision.c.id != rev_id)).values(status="RETIRED"))
    c.execute(pipeline_revision.update().where(pipeline_revision.c.id == rev_id).values(status="PUBLISHED"))
    c.execute(pipeline.update().where(pipeline.c.id == p["id"]).values(published_rev_id=rev_id))
    c.execute(table_binding.delete().where(table_binding.c.revision_id == rev_id))
    for n in dsl.get("nodes", []):
        if n.get("type") == "SINK":
            cfg = n.get("config") or {}
            b = cfg.get("binding") or {}
            c.execute(table_binding.insert().values(
                revision_id=rev_id, dataset=cfg.get("dataset"), table_name=b.get("table"), strategy=b.get("strategy", "SWAP"),
                key_fields=b.get("keyFields") or None, column_mapping=b.get("columnMapping") or [], guards=b.get("guards"),
            ))
    for t in _sink_tables(dsl):
        exists = c.execute(select(table_owner.c.pipeline_id).where(and_(
            table_owner.c.datasource_id == p["datasource_id"], table_owner.c.table_name == t))).scalar()
        if exists is None:
            c.execute(table_owner.insert().values(datasource_id=p["datasource_id"], table_name=t, pipeline_id=p["id"]))


def republish(pid: int, rev: int, who: str, ip: str, reason: str | None) -> dict:
    p = get_pipeline(pid)
    with get_engine().begin() as c:
        r = c.execute(select(pipeline_revision).where(and_(pipeline_revision.c.pipeline_id == pid,
                                                           pipeline_revision.c.rev == rev))).mappings().first()
        if not r:
            raise not_found("版本")
        _activate(c, p, r["id"], r["dsl"])
        audit.record(c, who, ip, "REPUBLISH_REVISION", f"pipeline:{p['code']}", {"rev": rev}, reason)
    return {"rev": rev, "revisionId": r["id"]}


def list_revisions(pid: int) -> list[dict]:
    with get_engine().connect() as c:
        rows = c.execute(select(pipeline_revision).where(pipeline_revision.c.pipeline_id == pid)
                         .order_by(pipeline_revision.c.rev.desc())).mappings().all()
    out = []
    for r in rows:
        rep = r["regression_report"] or {}
        out.append({"id": r["id"], "rev": r["rev"], "status": r["status"], "note": r["note"], "publishedBy": r["published_by"],
                    "publishedAt": _iso(r["published_at"]), "regression": rep.get("summary")})
    return out


def get_revision(pid: int, rev: int) -> dict:
    with get_engine().connect() as c:
        r = c.execute(select(pipeline_revision).where(and_(pipeline_revision.c.pipeline_id == pid,
                                                           pipeline_revision.c.rev == rev))).mappings().first()
    if not r:
        raise not_found("版本")
    return dict(r)


def published_revision(pid: int) -> dict | None:
    p = get_pipeline(pid)
    if not p["published_rev_id"]:
        return None
    with get_engine().connect() as c:
        return dict(c.execute(select(pipeline_revision).where(pipeline_revision.c.id == p["published_rev_id"])).mappings().first())


def dsl_diff(a: dict, b: dict) -> dict:
    """配置差异：节点增删改、连线增删、绑定变化（P4、P4-1）。"""
    na = {n["id"]: n for n in a.get("nodes", [])}
    nb = {n["id"]: n for n in b.get("nodes", [])}

    def strip(n: dict) -> Any:
        x = copy.deepcopy(n)
        x.pop("position", None)
        return x

    ea = {(e["source"]["nodeId"], e["source"]["portId"], e["target"]["nodeId"], e["target"]["portId"]) for e in a.get("edges", [])}
    eb = {(e["source"]["nodeId"], e["source"]["portId"], e["target"]["nodeId"], e["target"]["portId"]) for e in b.get("edges", [])}
    changed = [i for i in na.keys() & nb.keys() if strip(na[i]) != strip(nb[i])]
    bindings = [i for i in changed if nb[i].get("type") == "SINK" and (na[i].get("config") or {}).get("binding") != (nb[i].get("config") or {}).get("binding")]
    return {
        "nodesAdded": [{"id": i, "type": nb[i]["type"], "label": nb[i].get("label")} for i in sorted(nb.keys() - na.keys())],
        "nodesRemoved": [{"id": i, "type": na[i]["type"], "label": na[i].get("label")} for i in sorted(na.keys() - nb.keys())],
        "nodesChanged": [{"id": i, "type": nb[i]["type"], "label": nb[i].get("label")} for i in sorted(changed)],
        "edgesAdded": [list(x) for x in sorted(eb - ea)], "edgesRemoved": [list(x) for x in sorted(ea - eb)],
        "bindingsChanged": sorted(bindings),
    }


def freeze(pid: int, frozen: bool, who: str, ip: str, reason: str | None) -> dict:
    p = get_pipeline(pid)
    with get_engine().begin() as c:
        c.execute(pipeline.update().where(pipeline.c.id == pid).values(frozen=1 if frozen else 0))
        audit.record(c, who, ip, "FREEZE" if frozen else "UNFREEZE", f"pipeline:{p['code']}", None, reason)
    return pipeline_json(pid)
