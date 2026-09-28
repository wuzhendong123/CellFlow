"""方案发布前的历史文件回归（T16，F9-1/F9-2）：用草稿重跑最近 N 个成功任务的原始文件，与当时生效版本的结果对比。"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import and_, select

from cellflow.engine.dag import execute
from cellflow.errors import CFError
from cellflow.meta.db import get_engine
from cellflow.meta.tables import parse_job, pipeline_revision, source_file
from cellflow.redis_client import get_redis
from cellflow.services import files, pipelines, publishing, settings

TTL = 86400


def _key(rid: str) -> str:
    return f"cf:regression:{rid}"


def start(pid: int, who: str) -> dict:
    """同步执行（N 默认 5 个文件）；结果放 Redis，发布时带上 regressionId 存入版本记录。"""
    pipelines.get_pipeline(pid)
    draft = pipelines.get_draft(pid)["dsl"]
    draft = {k: v for k, v in draft.items() if k != "sampleFileId"}
    live_rev = pipelines.published_revision(pid)
    n = settings.get("regression.fileCount")
    with get_engine().connect() as c:
        rows = c.execute(select(parse_job.c.id, parse_job.c.file_id, parse_job.c.revision_id, source_file.c.file_name,
                                parse_job.c.submitted_at).join(source_file, source_file.c.id == parse_job.c.file_id)
                         .where(and_(parse_job.c.pipeline_id == pid, parse_job.c.status.in_(["PUBLISHED", "NO_CHANGE"])))
                         .order_by(parse_job.c.id.desc()).limit(n)).all()
    s = settings.get_all()
    items = []
    for jid, fid, rev_id, fname, at in rows:
        wb = files.load_workbook(fid)
        with get_engine().connect() as c:
            old_dsl = c.execute(select(pipeline_revision.c.dsl).where(pipeline_revision.c.id == rev_id)).scalar() if rev_id else None
        old = execute(old_dsl or (live_rev or {}).get("dsl") or {"nodes": [], "edges": []}, wb, s)
        new = execute(draft, wb, s)
        tables = []
        for ds, (node, nds) in new.sinks.items():
            ntd = publishing.to_table_data(node, nds) if nds is not None else None
            o = old.sinks.get(ds)
            otd = publishing.to_table_data(o[0], o[1]) if o and o[1] is not None else None
            if ntd and otd:
                cols = ntd.columns
                a = publishing.project([{"r": r} for r in otd.rows], cols, ntd.key_columns)
                b = publishing.project([{"r": r} for r in ntd.rows], cols, ntd.key_columns)
                ch = publishing.diff_rows(a, b, cols, ntd.key_columns)
                tables.append({"dataset": ds, "table": ntd.table, "oldRows": len(otd.rows), "newRows": len(ntd.rows),
                               "changes": publishing.summary(ch), "sample": ch[:20]})
            elif ntd:
                tables.append({"dataset": ds, "table": ntd.table, "oldRows": None, "newRows": len(ntd.rows), "changes": None,
                               "note": "新增的输出"})
        items.append({"jobId": jid, "fileId": fid, "fileName": fname, "submittedAt": at.isoformat() if at else None,
                      "old": {"errors": old.error_count, "warns": old.warn_count},
                      "new": {"errors": new.error_count, "warns": new.warn_count,
                              "issues": [i for i in new.issues if i["severity"] == "ERROR"][:20]},
                      "tables": tables})
    report = {
        "id": uuid.uuid4().hex, "pipelineId": pid, "by": who, "items": items,
        "summary": {"files": len(items), "filesWithNewErrors": sum(1 for i in items if i["new"]["errors"] > 0),
                    "filesChanged": sum(1 for i in items if any((t.get("changes") or {}) and sum(t["changes"].values()) for t in i["tables"]))},
        "diff": pipelines.dsl_diff((live_rev or {}).get("dsl") or {"nodes": [], "edges": []}, draft),
    }
    get_redis().setex(_key(report["id"]), TTL, json.dumps(report, ensure_ascii=False, default=str))
    return report


def get_report(rid: str) -> dict:
    raw = get_redis().get(_key(rid))
    if not raw:
        raise CFError("REGRESSION_NOT_FOUND", "回归结果不存在或已过期，请重新运行", 404)
    return json.loads(raw)
