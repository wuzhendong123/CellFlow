"""任务执行编排：解析 → 校验 → 快照 → 比对 → 安全闸 → 写表 → 回调（TECH_DESIGN §2.2 运行期、§10.1~§10.7）。

同时提供人工放行（FORCED）、回滚（ROLLBACK）与崩溃恢复。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, select

from cellflow.engine.dag import execute
from cellflow.engine.types import to_jsonable
from cellflow.errors import CFError
from cellflow.meta.db import get_engine
from cellflow.meta.tables import parse_job, pipeline, release, release_snapshot, snapshot
from cellflow.runtime import writer
from cellflow.services import audit, datasources, files, jobs, pipelines, settings
from cellflow.services import publishing as pub
from cellflow.services.callbacks import enqueue_job_callback


def _finish(job_id: int, status: str, **vals) -> None:
    jobs.update_job(job_id, status=status, finished_at=func.now(3), **vals)
    enqueue_job_callback(job_id)


def newer_execute_exists(job: dict) -> int | None:
    with get_engine().connect() as c:
        return c.execute(select(parse_job.c.id).where(and_(
            parse_job.c.pipeline_id == job["pipeline_id"], parse_job.c.mode == "EXECUTE", parse_job.c.id > job["id"],
            parse_job.c.status.notin_(["SUPERSEDED", "CANCELLED"]))).order_by(parse_job.c.id.desc()).limit(1)).scalar()


def run_job(job_id: int) -> str:
    """执行一个已入队的任务，返回最终状态。"""
    job = jobs.get_job(job_id)
    if job["status"] != "QUEUED":
        return job["status"]
    p = pipelines.get_pipeline(job["pipeline_id"])
    if job["mode"] == "EXECUTE" and p["frozen"]:
        _finish(job_id, "FAILED", error_summary={"error": 1, "code": "PIPELINE_FROZEN", "message": "方案已冻结"})
        return "FAILED"
    jobs.update_job(job_id, status="RUNNING", started_at=func.now(3))
    try:
        dsl = job["dsl_snapshot"] or _revision_dsl(job["revision_id"])
        s = settings.get_all()
        res = execute(dsl, files.load_workbook(job["file_id"]), s)
        jobs.save_outputs(job_id, res)
        jobs.save_issues(job_id, res.issues)
        summary = jobs.summarize(res)
        metrics = {"nodes": res.metrics, "locateReport": res.locate_report, "skipped": res.skipped}
        if res.error_count:
            _finish(job_id, "FAILED_VALIDATION", error_summary=summary, metrics=metrics)
            return "FAILED_VALIDATION"
        tds = [pub.to_table_data(node, ds) for node, ds in res.sinks.values() if ds is not None]
        if job["mode"] == "VALIDATE_ONLY":
            forecast = pub.forecast(p, res)
            _finish(job_id, "VALIDATED", error_summary=summary, metrics=metrics, result=to_jsonable({"forecast": forecast}))
            return "VALIDATED"
        snaps = {td.dataset: pub.save_snapshot(job_id, td) for td in tds}
        with get_engine().connect() as lc:
            if not pub.pipeline_lock(lc, p["id"], timeout=600):
                raise CFError("PIPELINE_BUSY", "等待方案锁超时", 503)
            try:
                newer = newer_execute_exists(job)
                if newer:
                    _finish(job_id, "SUPERSEDED", superseded_by=newer, error_summary=summary, metrics=metrics)
                    return "SUPERSEDED"
                status, result, extra_issues = _guard_and_write(p, job, tds, snaps, res.error_count, kind="NORMAL",
                                                                operator=job["operator"] or "open-api")
            finally:
                pub.pipeline_unlock(lc, p["id"])
        if extra_issues:
            jobs.save_issues(job_id, extra_issues)
            summary["error"] += sum(1 for i in extra_issues if i["severity"] == "ERROR")
        _finish(job_id, status, error_summary=summary, metrics=metrics, result=to_jsonable(result),
                release_id=result.get("releaseId"))
        return status
    except CFError as e:
        _finish(job_id, "FAILED", error_summary={"error": 1, "code": e.code, "message": e.message})
        return "FAILED"
    except Exception as e:  # noqa: BLE001 — 任何系统异常都要落到任务状态
        _finish(job_id, "FAILED", error_summary={"error": 1, "code": "INTERNAL", "message": f"{type(e).__name__}: {str(e)[:300]}"})
        return "FAILED"


def _revision_dsl(rev_id: int) -> dict:
    from cellflow.meta.tables import pipeline_revision

    with get_engine().connect() as c:
        return c.execute(select(pipeline_revision.c.dsl).where(pipeline_revision.c.id == rev_id)).scalar()


def ensure_baseline(p: dict, tds: list[pub.TableData]) -> dict:
    """首次接管：把业务表当前内容保存为基线发布（kind=BASELINE），可回滚到接管前（§10.5）。"""
    live = pub.live_release(p["id"])
    if live:
        return live
    eng = datasources.engine_for(p["datasource_id"])
    snaps, checksums = {}, {}
    with eng.connect() as c:
        for td in tds:
            if not writer.table_exists(c, td.table):
                raise CFError("TABLE_NOT_FOUND", f"目标表「{td.table}」不存在", 422)
            cols, rows = writer.read_rows(c, td.table)
            base = pub.TableData(td.dataset, td.table, td.columns, td.key_columns, rows, [{} for _ in rows], td.binding, td.schema)
            snaps[td.dataset] = (pub.save_snapshot(None, base, full_columns=cols), td.table)
            checksums[td.table] = writer.checksum(c, td.table)
    with get_engine().begin() as c:
        rid = pub.create_release(c, pipeline_id=p["id"], kind="BASELINE", status="PUBLISHED", write_plan={"tables": []},
                                 table_checksums=checksums, change_summary={}, operator="system", reason="首次接管前的业务表内容")
        for ds, (sid, table) in snaps.items():
            c.execute(release_snapshot.insert().values(release_id=rid, dataset=ds, snapshot_id=sid, table_name=table))
        pub.finish_release(c, rid)
        pub.set_live(c, p["id"], rid, None)
    return pub.live_release(p["id"])


def _guard_and_write(p: dict, job: dict | None, tds: list[pub.TableData], snaps: dict[str, int], error_count: int, *,
                     kind: str, operator: str, reason: str | None = None, skip_overridable: bool = False,
                     rollback_to: int | None = None) -> tuple[str, dict, list[dict]]:
    live = ensure_baseline(p, tds)
    per_table, guards_all, cell_issues = {}, [], []
    for td in tds:
        live_rows, meta = pub.current_rows(p["datasource_id"], live, td)
        new = pub.project([{"r": r, "c": c} for r, c in zip(td.rows, td.cells, strict=True)], td.columns, td.key_columns)
        changes = pub.diff_rows(live_rows, new, td.columns, td.key_columns)
        g, cells = pub.evaluate_guards(td, live_rows, meta, changes, p["datasource_id"], live, error_count)
        per_table[td.dataset] = (td.table, changes)
        guards_all += g
        cell_issues += cells
    result: dict[str, Any] = {
        "baseReleaseId": live["id"], "snapshots": snaps, "guards": guards_all,
        "tables": [{"dataset": ds, "table": t, "rows": len(next(x for x in tds if x.dataset == ds).rows),
                    "changes": pub.summary(ch)} for ds, (t, ch) in per_table.items()],
    }
    failed = [g for g in guards_all if not g["passed"] and (not skip_overridable or not g["overridable"])]
    if failed:
        result["blockedBy"] = sorted({g["guard"] for g in failed})
        return "FAILED_GUARD", result, cell_issues
    if kind == "NORMAL" and all(sum(pub.summary(ch).values()) == 0 for _, ch in per_table.values()):
        return "NO_CHANGE", result, []
    rid = write_release(p, tds, snaps, per_table, live, kind=kind, operator=operator, reason=reason,
                        job_id=job["id"] if job else None, rollback_to=rollback_to)
    result["releaseId"] = rid
    return "PUBLISHED", result, []


def write_release(p: dict, tds: list[pub.TableData], snaps: dict[str, int], per_table: dict, live: dict, *, kind: str,
                  operator: str, reason: str | None, job_id: int | None, rollback_to: int | None = None) -> int:
    s = settings.get_all()
    eng = datasources.engine_for(p["datasource_id"])
    token = job_id if job_id else f"r{live['id']}"
    writes = [writer.TableWrite(td.table, writer.shadow_name(td.table, token), writer.backup_name(td.table, live["id"]),
                                td.columns, td.rows) for td in tds]
    with get_engine().begin() as c:
        rid = pub.create_release(c, pipeline_id=p["id"], job_id=job_id, kind=kind, status="WRITING", rollback_to=rollback_to,
                                 prev_release_id=live["id"], write_plan={"tables": [w.plan_json() for w in writes]},
                                 change_summary={ds: pub.summary(ch) for ds, (_, ch) in per_table.items()},
                                 operator=operator[:64], reason=reason)
        for td in tds:
            c.execute(release_snapshot.insert().values(release_id=rid, dataset=td.dataset, snapshot_id=snaps[td.dataset],
                                                       table_name=td.table))
    try:
        checksums = writer.swap(eng, writes, s["write.lockWaitTimeoutSec"], s["write.renameRetries"])
    except writer.WriteError as e:
        with get_engine().begin() as c:
            c.execute(release.update().where(release.c.id == rid).values(status="FAILED", reason=e.message[:512]))
        raise CFError(e.code if e.code == "TARGET_LOCK_TIMEOUT" else "WRITE_FAILED", e.message, 500) from e
    change_uri = pub.save_changes(rid, per_table)
    with get_engine().begin() as c:
        pub.finish_release(c, rid, status="PUBLISHED", table_checksums=checksums, change_uri=change_uri)
        pub.set_live(c, p["id"], rid, live["id"])
    keep = pub.keep_backup_ids(p["id"], s["retention.backupTables"])
    for td in tds:
        writer.cleanup_backups(eng, td.table, keep)
    return rid


# ======================= 人工放行（F11-6、F17） =======================
def force_publish(job_id: int, operator: str, ip: str, reason: str) -> dict:
    if not reason:
        raise CFError("INVALID_REQUEST", "放行必须填写理由", 400)
    job = jobs.get_job(job_id)
    if job["status"] != "FAILED_GUARD":
        raise CFError("JOB_NOT_BLOCKED", "只有被安全闸拦截的任务可以放行", 409)
    blocked = set((job["result"] or {}).get("blockedBy") or [])
    if blocked & {"G1", "G6", "G8"}:
        raise CFError("GUARD_NOT_OVERRIDABLE", "因 ERROR 级问题、目标表结构不兼容或值超出列定义被拦截的任务不能放行", 409)
    p = pipelines.get_pipeline(job["pipeline_id"])
    tds = _tds_from_snapshots(job["result"]["snapshots"])
    with get_engine().connect() as lc:
        if not pub.pipeline_lock(lc, p["id"], timeout=60):
            raise CFError("PIPELINE_BUSY", "方案正在执行其他任务，请稍后再试", 409)
        try:
            status, result, _ = _guard_and_write(p, job, tds, job["result"]["snapshots"], 0, kind="FORCED", operator=operator,
                                                 reason=reason, skip_overridable=True)
        finally:
            pub.pipeline_unlock(lc, p["id"])
    if status != "PUBLISHED":
        raise CFError("GUARD_NOT_OVERRIDABLE", "存在不可放行的安全闸问题", 409, result)
    with get_engine().begin() as c:
        c.execute(parse_job.update().where(parse_job.c.id == job_id).values(status="PUBLISHED", release_id=result["releaseId"],
                                                                             result=to_jsonable({**job["result"], **result, "forced": True})))
        audit.record(c, operator, ip, "FORCE_PUBLISH", f"pipeline:{p['code']}", {"jobId": job_id, "releaseId": result["releaseId"]}, reason)
    enqueue_job_callback(job_id)
    return {"releaseId": result["releaseId"]}


def _tds_from_snapshots(snaps: dict[str, int]) -> list[pub.TableData]:
    tds = []
    for dataset, sid in snaps.items():
        meta, rows = pub.load_snapshot(int(sid))
        sj = meta["schema_json"]
        b = sj.get("binding") or {}
        td = pub.TableData(dataset, b.get("table") or dataset, sj.get("columns") or [], meta["key_fields"],
                           [x["r"] for x in rows], [x.get("c") or {} for x in rows], b)
        td.schema = sj.get("fieldTypes") or []
        tds.append(td)
    return tds


# ======================= 回滚（§10.6，F14） =======================
def rollback_targets(pipeline_id: int) -> list[dict]:
    with get_engine().connect() as c:
        return [dict(r) for r in c.execute(select(release).where(release.c.pipeline_id == pipeline_id)
                                           .order_by(release.c.id.desc())).mappings()]


def _target_tds(target_id: int) -> list[pub.TableData]:
    with get_engine().connect() as c:
        rs = c.execute(select(release_snapshot).where(release_snapshot.c.release_id == target_id)).mappings().all()
    if not rs:
        raise CFError("SNAPSHOT_EXPIRED", "目标版本没有可用快照", 410)
    tds = []
    for r in rs:
        meta, rows = pub.load_snapshot(r["snapshot_id"])
        cols = meta["schema_json"].get("columns") or []
        b = meta["schema_json"].get("binding") or {}
        td = pub.TableData(r["dataset"], r["table_name"], cols, meta["key_fields"], [x["r"] for x in rows],
                           [x.get("c") or {} for x in rows], b)
        td.compare_columns = meta["schema_json"].get("compareColumns") or cols  # type: ignore[attr-defined]
        tds.append(td)
    return tds


def rollback_preview(pipeline_id: int, target_id: int) -> dict:
    p = pipelines.get_pipeline(pipeline_id)
    live = pub.live_release(pipeline_id)
    if not live:
        raise CFError("NO_LIVE_RELEASE", "方案还没有发布记录", 409)
    target = _release(target_id, pipeline_id)
    if target["id"] == live["id"]:
        raise CFError("INVALID_REQUEST", "目标版本就是当前线上版本", 400)
    tds = _target_tds(target_id)
    eng = datasources.engine_for(p["datasource_id"])
    tables = []
    fast = writer.backups_exist(eng, [td.table for td in tds], target_id)
    for td in tds:
        live_raw, _ = pub.live_rows_for(live, td.dataset, td.table)
        cmp = getattr(td, "compare_columns", td.columns)
        key = td.key_columns
        old = pub.project(live_raw, cmp, key)
        new = pub.project([{"r": r} for r in td.rows], cmp, key)
        changes = pub.diff_rows(old, new, cmp, key)
        desc = datasources.describe_table(p["datasource_id"], td.table)
        cols = {c["name"]: c for c in desc["columns"]}
        problems = [f"目标表已没有列「{c}」" for c in td.columns if c not in cols]
        problems += [f"列「{c['name']}」不可为空且没有默认值，但目标版本没有该列的数据" for c in desc["columns"]
                     if c["name"] not in td.columns and not c["nullable"] and not c["hasDefault"]]
        drift = False
        if (live.get("table_checksums") or {}).get(td.table) is not None:
            with eng.connect() as c:
                drift = writer.checksum(c, td.table) != live["table_checksums"][td.table]
        tables.append({"dataset": td.dataset, "table": td.table, "changes": pub.summary(changes), "schemaProblems": problems,
                       "drift": drift})
    return {"liveReleaseId": live["id"], "targetReleaseId": target_id, "mode": "FAST_SWAP" if fast else "REWRITE",
            "tables": tables, "compatible": not any(t["schemaProblems"] for t in tables),
            "drift": any(t["drift"] for t in tables)}


def _release(rid: int, pipeline_id: int) -> dict:
    with get_engine().connect() as c:
        r = c.execute(select(release).where(and_(release.c.id == rid, release.c.pipeline_id == pipeline_id))).mappings().first()
    if not r or r["status"] != "PUBLISHED":
        raise CFError("RELEASE_NOT_FOUND", "目标版本不存在或不可用", 404)
    return dict(r)


def rollback(pipeline_id: int, target_id: int, expected_live_id: int, operator: str, ip: str, reason: str,
             freeze: bool = True, confirm_drift: bool = False) -> dict:
    if not reason:
        raise CFError("INVALID_REQUEST", "回滚必须填写理由", 400)
    p = pipelines.get_pipeline(pipeline_id)
    with get_engine().connect() as lc:
        if not pub.pipeline_lock(lc, pipeline_id, timeout=60):
            raise CFError("PIPELINE_BUSY", "方案正在执行其他任务，请稍后再试", 409)
        try:
            live = pub.live_release(pipeline_id)
            if not live or live["id"] != expected_live_id:
                raise CFError("LIVE_STATE_CONFLICT", "线上版本已变化，请刷新后再试", 409)
            pre = rollback_preview(pipeline_id, target_id)
            if not pre["compatible"]:
                raise CFError("TARGET_SCHEMA_MISMATCH", "目标版本与当前表结构不兼容", 409, pre)
            if pre["drift"] and not confirm_drift:
                raise CFError("TARGET_DRIFT", "业务表已被外部修改，请确认覆盖后再回滚", 409, pre)
            if freeze:
                with get_engine().begin() as c:
                    c.execute(pipeline.update().where(pipeline.c.id == pipeline_id).values(frozen=1))
            tds = _target_tds(target_id)
            eng = datasources.engine_for(p["datasource_id"])
            s = settings.get_all()
            per_table = {}
            for td in tds:
                live_raw, _ = pub.live_rows_for(live, td.dataset, td.table)
                cmp = getattr(td, "compare_columns", td.columns)
                per_table[td.dataset] = (td.table, pub.diff_rows(pub.project(live_raw, cmp, td.key_columns),
                                                                 pub.project([{"r": r} for r in td.rows], cmp, td.key_columns),
                                                                 cmp, td.key_columns))
            with get_engine().connect() as c:
                snap_ids = {r["dataset"]: r["snapshot_id"] for r in c.execute(select(release_snapshot).where(
                    release_snapshot.c.release_id == target_id)).mappings()}
            if pre["mode"] == "FAST_SWAP":
                with get_engine().begin() as c:
                    rid = pub.create_release(c, pipeline_id=pipeline_id, kind="ROLLBACK", status="WRITING", rollback_to=target_id,
                                             prev_release_id=live["id"], write_plan={"tables": [], "fastSwap": True},
                                             change_summary={ds: pub.summary(ch) for ds, (_, ch) in per_table.items()},
                                             operator=operator, reason=reason)
                    for td in tds:
                        c.execute(release_snapshot.insert().values(release_id=rid, dataset=td.dataset,
                                                                   snapshot_id=snap_ids[td.dataset], table_name=td.table))
                try:
                    checksums = writer.swap_back(eng, [td.table for td in tds], live["id"], target_id,
                                                 s["write.lockWaitTimeoutSec"], s["write.renameRetries"])
                except writer.WriteError as e:
                    with get_engine().begin() as c:
                        c.execute(release.update().where(release.c.id == rid).values(status="FAILED"))
                    raise CFError(e.code, e.message, 500) from e
                with get_engine().begin() as c:
                    pub.finish_release(c, rid, status="PUBLISHED", table_checksums=checksums,
                                       change_uri=pub.save_changes(rid, per_table))
                    pub.set_live(c, pipeline_id, rid, live["id"])
            else:
                rid = write_release(p, tds, snap_ids, per_table, live, kind="ROLLBACK", operator=operator, reason=reason,
                                    job_id=None, rollback_to=target_id)
            with get_engine().begin() as c:
                audit.record(c, operator, ip, "ROLLBACK", f"pipeline:{p['code']}",
                             {"from": live["id"], "to": target_id, "releaseId": rid, "mode": pre["mode"], "freeze": freeze}, reason)
        finally:
            pub.pipeline_unlock(lc, pipeline_id)
    return {"releaseId": rid, "mode": pre["mode"], "frozen": freeze}


# ======================= 崩溃恢复（§10.4） =======================
def recover_writing() -> list[dict]:
    """把 WRITING 状态的发布按业务库实际情况修正为 PUBLISHED / FAILED。"""
    out = []
    with get_engine().connect() as c:
        rows = [dict(r) for r in c.execute(select(release).where(release.c.status == "WRITING")).mappings()]
    for r in rows:
        p = pipelines.get_pipeline(r["pipeline_id"])
        eng = datasources.engine_for(p["datasource_id"])
        plan = r["write_plan"] or {}
        state = writer.recover(eng, plan) if plan.get("tables") else "FAILED"
        with get_engine().begin() as c:
            if state == "PUBLISHED":
                with eng.connect() as bc:
                    sums = {t["table"]: writer.checksum(bc, t["table"]) for t in plan["tables"]}
                pub.finish_release(c, r["id"], status="PUBLISHED", table_checksums=sums)
                try:
                    pub.set_live(c, r["pipeline_id"], r["id"], r["prev_release_id"])
                except CFError:
                    pub.set_live(c, r["pipeline_id"], r["id"], None)
            else:
                c.execute(release.update().where(release.c.id == r["id"]).values(status="FAILED"))
        if r["job_id"]:
            job = jobs.get_job(r["job_id"])
            if job["status"] not in jobs.TERMINAL:
                if state == "PUBLISHED":
                    jobs.update_job(r["job_id"], status="PUBLISHED", release_id=r["id"], finished_at=func.now(3))
                else:
                    jobs.update_job(r["job_id"], status="FAILED_WRITE", finished_at=func.now(3))
                enqueue_job_callback(r["job_id"])
        out.append({"releaseId": r["id"], "state": state})
    # 仍处于 RUNNING 且没有发布记录的任务：进程崩溃前未写表
    with get_engine().connect() as c:
        stale = [x[0] for x in c.execute(select(parse_job.c.id).where(and_(
            parse_job.c.status == "RUNNING", parse_job.c.mode != "TEST",
            parse_job.c.started_at < func.date_sub(func.now(), text_interval(30))))).all()]
    for jid in stale:
        _finish(jid, "FAILED", error_summary={"error": 1, "code": "WORKER_CRASHED", "message": "执行进程中断，业务表未写入"})
    return out


def text_interval(minutes: int):
    from sqlalchemy import literal_column

    return literal_column(f"INTERVAL {int(minutes)} MINUTE")


def snapshot_exists(sid: int) -> bool:
    with get_engine().connect() as c:
        return c.execute(select(snapshot.c.id).where(snapshot.c.id == sid)).first() is not None
