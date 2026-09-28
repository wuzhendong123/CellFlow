"""数据清理（T19，TECH_DESIGN §10.9）。"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, delete, select

from cellflow.meta.db import get_engine
from cellflow.meta.tables import (
    job_issue,
    live_state,
    parse_job,
    pipeline,
    release,
    release_snapshot,
    snapshot,
    source_file,
)
from cellflow.services import settings
from cellflow.storage import get_storage


def run_cleanup(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    s = settings.get_all()
    keep_n, keep_days = s["retention.releases"], s["retention.days"]
    cutoff = now - dt.timedelta(days=keep_days)
    st = get_storage()
    stats = {"snapshots": 0, "files": 0, "testJobs": 0, "changeFiles": 0}
    with get_engine().connect() as c:
        pids = [r[0] for r in c.execute(select(pipeline.c.id)).all()]
        live = {r[0]: r[1] for r in c.execute(select(live_state.c.pipeline_id, live_state.c.release_id)).all()}
        sample_files = {r[0] for r in c.execute(select(pipeline.c.sample_file_id)).all() if r[0]}
    protected_snaps: set[int] = set()
    expired_releases: list[int] = []
    for pid in pids:
        with get_engine().connect() as c:
            rels = [dict(r) for r in c.execute(select(release).where(and_(release.c.pipeline_id == pid, release.c.status == "PUBLISHED"))
                                               .order_by(release.c.id.desc())).mappings()]
        keep_ids = {r["id"] for r in rels[:keep_n]}
        keep_ids |= {r["id"] for r in rels if r["created_at"] and r["created_at"] >= cutoff}
        keep_ids |= {r["id"] for r in rels if r["kind"] == "BASELINE"}
        if live.get(pid):
            keep_ids.add(live[pid])
        normal = [r for r in rels if r["kind"] in ("NORMAL", "FORCED")]
        if normal:
            keep_ids.add(normal[0]["id"])
        for r in rels:
            if r["id"] not in keep_ids:
                expired_releases.append(r["id"])
        with get_engine().connect() as c:
            protected_snaps |= {x[0] for x in c.execute(select(release_snapshot.c.snapshot_id).where(
                release_snapshot.c.release_id.in_(keep_ids or {-1}))).all()}
    with get_engine().connect() as c:
        expired_snaps = [dict(r) for r in c.execute(select(snapshot).where(release_snapshot.c.release_id.in_(expired_releases or [-1]))
                                                    .join(release_snapshot, release_snapshot.c.snapshot_id == snapshot.c.id)).mappings()]
        # 未进入任何发布、且任务已超过保留期的快照（被拦截/无变化的任务）
        orphan = [dict(r) for r in c.execute(select(snapshot).join(parse_job, parse_job.c.id == snapshot.c.job_id).where(and_(
            parse_job.c.submitted_at < cutoff, snapshot.c.id.notin_(select(release_snapshot.c.snapshot_id))))).mappings()]
        change_uris = [r[0] for r in c.execute(select(release.c.change_uri).where(release.c.id.in_(expired_releases or [-1]))).all() if r[0]]
    for sn in expired_snaps + orphan:
        if sn["id"] in protected_snaps:
            continue
        st.delete(sn["storage_uri"])
        stats["snapshots"] += 1
    for uri in change_uris:
        st.delete(uri)
        stats["changeFiles"] += 1
    # 试跑任务结果
    test_cut = now - dt.timedelta(days=s["retention.testJobDays"])
    with get_engine().begin() as c:
        old_tests = [r[0] for r in c.execute(select(parse_job.c.id).where(and_(parse_job.c.mode == "TEST",
                                                                             parse_job.c.submitted_at < test_cut))).all()]
        if old_tests:
            c.execute(delete(job_issue).where(job_issue.c.job_id.in_(old_tests)))
            c.execute(delete(parse_job).where(parse_job.c.id.in_(old_tests)))
        stats["testJobs"] = len(old_tests)
    # 原始文件：没有保留中的任务 / 发布引用，且不是样例文件
    with get_engine().connect() as c:
        referenced = {r[0] for r in c.execute(select(parse_job.c.file_id).where(parse_job.c.submitted_at >= cutoff)).all()}
        referenced |= sample_files
        candidates = [dict(r) for r in c.execute(select(source_file).where(source_file.c.uploaded_at < cutoff)).mappings()]
        keys_in_use = {r[0] for r in c.execute(select(source_file.c.storage_uri).where(source_file.c.id.in_(referenced or {-1}))).all()}
    for f in candidates:
        if f["id"] in referenced or f["storage_uri"] in keys_in_use:
            continue
        st.delete(f["storage_uri"])
        stats["files"] += 1
    stats["expiredReleases"] = len(expired_releases)
    return stats


def release_available(release_id: int) -> bool:
    """快照是否仍在（超过保留期的发布不可作为回滚目标，F14-7）。"""
    with get_engine().connect() as c:
        uris = [r[0] for r in c.execute(select(snapshot.c.storage_uri).join(
            release_snapshot, release_snapshot.c.snapshot_id == snapshot.c.id).where(release_snapshot.c.release_id == release_id)).all()]
    st = get_storage()
    return bool(uris) and all(st.exists(u) for u in uris)
