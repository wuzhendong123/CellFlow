"""快照、比对、安全闸与写入编排（T09、T10、T17；TECH_DESIGN §6.4、§10.1~§10.6）。"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, func, select, text

from cellflow.engine.dataset import Dataset
from cellflow.engine.types import to_jsonable
from cellflow.errors import CFError
from cellflow.meta.db import get_engine
from cellflow.meta.tables import live_state, release, release_snapshot, snapshot
from cellflow.runtime import writer
from cellflow.services import datasources, settings
from cellflow.storage import get_storage


# ======================= 快照 =======================
@dataclass
class TableData:
    """一个输出数据集映射成业务表行之后的样子。"""

    dataset: str
    table: str
    columns: list[str]  # 写入的目标列
    key_columns: list[str] | None
    rows: list[dict]
    cells: list[dict]  # 列 → 来源单元格
    binding: dict = field(default_factory=dict)
    schema: list[dict] = field(default_factory=list)


def to_table_data(sink_node: dict, ds: Dataset) -> TableData:
    cfg = sink_node.get("config") or {}
    b = cfg.get("binding") or {}
    mapping = b.get("columnMapping") or []
    cols = [m["column"] for m in mapping]
    by_field = {m["field"]: m["column"] for m in mapping}
    keys = [by_field[k] for k in (b.get("keyFields") or []) if k in by_field] or None
    rows, cells = [], []
    for r, lin in zip(ds.rows, ds.lineage, strict=True):
        rows.append({m["column"]: r.get(m["field"]) for m in mapping})
        c = {}
        for m in mapping:
            v = lin.get(m["field"])
            c[m["column"]] = v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)
        cells.append(c)
    return TableData(cfg.get("dataset"), b.get("table"), cols, keys, rows, cells, b, ds.schema_json())


def save_snapshot(job_id: int | None, td: TableData, full_columns: list[str] | None = None) -> int:
    """快照写对象存储（jsonl.gz，含行哈希），元数据库只存引用与摘要。"""
    cols = full_columns or td.columns
    lines = []
    hashes = []
    for r, c in zip(td.rows, td.cells or [{}] * len(td.rows), strict=False):
        h = writer.row_hash(r, td.columns)
        hashes.append(h)
        key = [writer.norm_value(r.get(k)) for k in td.key_columns] if td.key_columns else None
        lines.append(json.dumps({"k": key, "h": h, "r": to_jsonable(r), "c": c or {}}, ensure_ascii=False, default=str))
    content = __import__("hashlib").sha256("\n".join(sorted(hashes)).encode()).hexdigest()
    key = f"snapshots/{job_id or 'baseline'}/{td.dataset}-{content[:12]}.jsonl.gz"
    get_storage().put(key, gzip.compress("\n".join(lines).encode()))
    with get_engine().begin() as c:
        return c.execute(snapshot.insert().values(
            job_id=job_id, dataset=td.dataset, storage_uri=key,
            schema_json={"columns": cols, "compareColumns": td.columns, "binding": td.binding, "fieldTypes": td.schema},
            key_fields=td.key_columns, row_count=len(td.rows), content_hash=content,
        )).inserted_primary_key[0]


def load_snapshot(snapshot_id: int) -> tuple[dict, list[dict]]:
    with get_engine().connect() as c:
        s = c.execute(select(snapshot).where(snapshot.c.id == snapshot_id)).mappings().first()
    if not s:
        raise CFError("SNAPSHOT_NOT_FOUND", "快照不存在或已超过保留期", 404)
    st = get_storage()
    if not st.exists(s["storage_uri"]):
        raise CFError("SNAPSHOT_EXPIRED", "快照已超过保留期", 410)
    data = gzip.decompress(st.get(s["storage_uri"])).decode()
    return dict(s), [json.loads(x) for x in data.splitlines() if x]


# ======================= 比对（§10.5） =======================
def diff_rows(old: list[dict], new: list[dict], columns: list[str], key_columns: list[str] | None) -> list[dict]:
    """old/new 为快照行 {"k","h","r","c"}；按主键或整行内容比对，返回 ChangeRecord 列表。"""
    changes = []
    if key_columns:
        o = {json.dumps(x["k"], ensure_ascii=False, default=str): x for x in old}
        n = {json.dumps(x["k"], ensure_ascii=False, default=str): x for x in new}
        for k in n.keys() - o.keys():
            changes.append({"op": "c", "rowKey": dict(zip(key_columns, n[k]["k"], strict=True)), "before": None,
                            "after": n[k]["r"], "cells": n[k].get("c")})
        for k in o.keys() - n.keys():
            changes.append({"op": "d", "rowKey": dict(zip(key_columns, o[k]["k"], strict=True)), "before": o[k]["r"], "after": None})
        for k in n.keys() & o.keys():
            if n[k]["h"] != o[k]["h"]:
                b, a = o[k]["r"], n[k]["r"]
                changed = [c for c in columns if writer.norm_value(b.get(c)) != writer.norm_value(a.get(c))]
                changes.append({"op": "u", "rowKey": dict(zip(key_columns, n[k]["k"], strict=True)), "before": b, "after": a,
                                "changedFields": changed, "cells": {c: (n[k].get("c") or {}).get(c) for c in changed}})
    else:
        oc, nc = Counter(x["h"] for x in old), Counter(x["h"] for x in new)
        by_hash = {x["h"]: x for x in old + new}
        for h, cnt in (nc - oc).items():
            changes += [{"op": "c", "rowKey": None, "rowHash": h, "before": None, "after": by_hash[h]["r"],
                         "cells": by_hash[h].get("c")}] * cnt
        for h, cnt in (oc - nc).items():
            changes += [{"op": "d", "rowKey": None, "rowHash": h, "before": by_hash[h]["r"], "after": None}] * cnt
    return changes


def summary(changes: list[dict]) -> dict:
    c = Counter(x["op"] for x in changes)
    return {"c": c.get("c", 0), "u": c.get("u", 0), "d": c.get("d", 0)}


def project(rows: list[dict], columns: list[str], key_columns: list[str] | None) -> list[dict]:
    """把基线快照（全列）投影到本次写入的列上再比对。"""
    out = []
    for x in rows:
        r = {c: x["r"].get(c) for c in columns}
        out.append({"k": [writer.norm_value(r.get(k)) for k in key_columns] if key_columns else None,
                    "h": writer.row_hash(r, columns), "r": r, "c": x.get("c")})
    return out


# ======================= 线上状态 =======================
def live_release(pipeline_id: int) -> dict | None:
    with get_engine().connect() as c:
        rid = c.execute(select(live_state.c.release_id).where(live_state.c.pipeline_id == pipeline_id)).scalar()
        if not rid:
            return None
        r = c.execute(select(release).where(release.c.id == rid)).mappings().first()
        snaps = {x["dataset"]: dict(x) for x in c.execute(select(release_snapshot).where(release_snapshot.c.release_id == rid)).mappings()}
    out = dict(r)
    out["snapshots"] = snaps
    return out


def live_rows_for(live: dict | None, dataset: str, table: str) -> tuple[list[dict], dict | None]:
    if not live:
        return [], None
    s = live["snapshots"].get(dataset) or next((v for v in live["snapshots"].values() if v["table_name"] == table), None)
    if not s:
        return [], None
    meta, rows = load_snapshot(s["snapshot_id"])
    return rows, meta


# ======================= 安全闸（§10.2） =======================
INT_RANGES = {"tinyint": 8, "smallint": 16, "mediumint": 24, "int": 32, "bigint": 64}


def _value_problem(col: dict, v: Any) -> str | None:
    if v is None:
        if not col["nullable"]:
            return f"列「{col['name']}」不允许为空"
        return None
    dtp = col["dataType"]
    if dtp in INT_RANGES:
        if isinstance(v, bool):
            v = int(v)
        if not isinstance(v, int):
            try:
                from decimal import Decimal

                d = Decimal(str(v))
                if d != d.to_integral_value():
                    return f"值 {v} 不是整数（列「{col['name']}」为 {col['columnType']}）"
                v = int(d)
            except Exception:
                return f"值「{v}」不是数字（列「{col['name']}」为 {col['columnType']}）"
        bits = INT_RANGES[dtp]
        unsigned = "unsigned" in (col["columnType"] or "")
        lo, hi = (0, 2**bits - 1) if unsigned else (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
        if not lo <= v <= hi:
            return f"值 {v} 超出列「{col['name']}」（{col['columnType']}）的范围"
    elif dtp in ("varchar", "char", "tinytext", "text", "mediumtext"):
        s = writer.db_value(v)
        s = s if isinstance(s, str) else str(writer.norm_value(v))
        ml = col.get("maxLength")
        if ml and len(s) > int(ml):
            return f"长度 {len(s)} 超出列「{col['name']}」（{col['columnType']}）"
        if col.get("charset") and col["charset"] != "utf8mb4" and any(ord(ch) > 0xFFFF for ch in s):
            return f"列「{col['name']}」字符集为 {col['charset']}，无法写入 emoji 等字符"
    elif dtp == "decimal":
        from decimal import Decimal, InvalidOperation

        try:
            d = Decimal(str(v))
        except InvalidOperation:
            return f"值「{v}」不是数字（列「{col['name']}」）"
        p, s = int(col.get("numPrecision") or 65), int(col.get("numScale") or 0)
        int_digits = len(str(abs(int(d)))) if int(d) != 0 else 0
        if int_digits > p - s:
            return f"值 {v} 超出列「{col['name']}」（{col['columnType']}）的精度"
    elif dtp in ("float", "double"):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            try:
                float(str(v))
            except ValueError:
                return f"值「{v}」不是数字（列「{col['name']}」）"
    elif dtp in ("date", "datetime", "timestamp"):
        if not hasattr(v, "year"):
            if not re.match(r"^\d{4}-\d{2}-\d{2}", str(v)):
                return f"值「{v}」不是日期（列「{col['name']}」）"
    return None


def check_values(td: TableData, desc: dict, limit: int = 200) -> list[dict]:
    """G8：值域检查，逐单元格报告。"""
    cols = {c["name"]: c for c in desc["columns"]}
    issues = []
    for i, r in enumerate(td.rows):
        for colname in td.columns:
            col = cols.get(colname)
            if not col:
                continue
            p = _value_problem(col, r.get(colname))
            if p:
                issues.append({"severity": "ERROR", "code": "COLUMN_OVERFLOW", "message": p,
                               "cell": (td.cells[i] if i < len(td.cells) else {}).get(colname), "value": r.get(colname),
                               "node": td.dataset})
                if len(issues) >= limit:
                    return issues
    # 目标表唯一索引检查（未声明主键时也要避免灌数时报重复）
    for uk in [desc["primaryKey"]] + desc["uniqueKeys"]:
        if not uk or not all(k in td.columns for k in uk):
            continue
        seen: dict[tuple, int] = {}
        for i, r in enumerate(td.rows):
            k = tuple(writer.norm_value(r.get(x)) for x in uk)
            if k in seen:
                issues.append({"severity": "ERROR", "code": "DUPLICATE_KEY",
                               "message": f"违反目标表唯一键（{', '.join(uk)}）：{k}",
                               "cell": (td.cells[i] if i < len(td.cells) else {}).get(uk[0]), "node": td.dataset})
            seen[k] = i
    return issues


def evaluate_guards(td: TableData, live_rows: list[dict], live_meta: dict | None, changes: list[dict], ds_id: int,
                    live: dict | None, error_count: int, check_drift: bool = True) -> tuple[list[dict], list[dict]]:
    """返回 (逐条闸结果, G8 单元格问题)。"""
    g = settings.effective_guard(td.binding.get("guards"))
    results = []
    s = summary(changes)
    n_new, n_live = len(td.rows), len(live_rows)

    def add(code, name, passed, overridable, actual=None, threshold=None, message=""):
        results.append({"guard": code, "name": name, "passed": passed, "overridable": overridable, "actual": actual,
                        "threshold": threshold, "message": message, "table": td.table})

    add("G1", "ERROR 级问题", error_count == 0, False, error_count, 0, "" if error_count == 0 else f"存在 {error_count} 个 ERROR 级问题")
    empty_bad = bool(g.get("forbidEmpty")) and n_new == 0 and n_live > 0
    add("G2", "清空保护", not empty_bad, True, n_new, None, "新数据为空而线上有数据" if empty_bad else "")
    if n_live > 0:
        ratio = abs(n_new - n_live) / n_live
        lim = float(g["maxRowChangeRatio"])
        add("G3", "行数波动", ratio <= lim, True, round(ratio, 4), lim,
            "" if ratio <= lim else f"行数由 {n_live} 变为 {n_new}（{ratio:.0%} > {lim:.0%}）")
        dratio = s["d"] / n_live
        dl = float(g["maxDeleteRatio"])
        add("G4", "删除比例", dratio <= dl, True, round(dratio, 4), dl,
            "" if dratio <= dl else f"删除 {s['d']} 行，占线上 {dratio:.0%} > {dl:.0%}")
    else:
        add("G3", "行数波动", True, True, None, g["maxRowChangeRatio"], "线上为空，跳过")
        add("G4", "删除比例", True, True, None, g["maxDeleteRatio"], "线上为空，跳过")
    lo, hi = g.get("minRows"), g.get("maxRows")
    ok5 = (lo is None or n_new >= lo) and (hi is None or n_new <= hi)
    add("G5", "绝对行数范围", ok5, True, n_new, [lo, hi], "" if ok5 else f"行数 {n_new} 不在 [{lo}, {hi}]")
    chk = datasources.check_binding(ds_id, td.binding, [{"field": m["field"], "type": _field_type(td, m["field"])}
                                                         for m in td.binding.get("columnMapping") or []], None)
    schema_errors = [e for e in chk["errors"] if e["code"] != "TABLE_OWNED"]
    add("G6", "目标表结构兼容", not schema_errors, False, None, None, "；".join(e["message"] for e in schema_errors))
    drift_msg = ""
    drift_ok = True
    if check_drift and live and (live.get("table_checksums") or {}).get(td.table) is not None:
        with datasources.engine_for(ds_id).connect() as c:
            cur = writer.checksum(c, td.table)
        if cur != live["table_checksums"][td.table]:
            drift_ok = False
            drift_msg = "业务表当前内容与上次发布不一致（可能被 CellFlow 以外的程序修改）"
    policy_overwrite = g.get("driftPolicy") == "OVERWRITE"
    add("G7", "漂移检测", drift_ok or policy_overwrite, True, None, None, drift_msg)
    cell_issues = check_values(td, chk["table"]) if "table" in chk else []
    add("G8", "值域", not cell_issues, False, len(cell_issues), 0,
        "" if not cell_issues else f"{len(cell_issues)} 个值超出目标列定义")
    return results, cell_issues


def _field_type(td: TableData, fieldname: str) -> str:
    for c in td.schema:
        if c["field"] == fieldname:
            return c["type"]
    return "string"


# ======================= 预判（C10） =======================
def forecast(p: dict, res) -> dict:
    """试跑 / 只校验：若现在写入，会变更多少、会被哪条安全闸拦截（不写表）。"""
    try:
        live = live_release(p["id"])
        out = {"baseReleaseId": live["id"] if live else None, "tables": []}
        for dataset, (node, ds) in res.sinks.items():
            if ds is None:
                continue
            td = to_table_data(node, ds)
            live_rows, meta = current_rows(p["datasource_id"], live, td)
            new = project([{"r": r, "c": c} for r, c in zip(td.rows, td.cells, strict=True)], td.columns, td.key_columns)
            changes = diff_rows(live_rows, new, td.columns, td.key_columns)
            guards, cells = evaluate_guards(td, live_rows, meta, changes, p["datasource_id"], live, res.error_count)
            out["tables"].append({"dataset": dataset, "table": td.table, "rows": len(td.rows), "changes": summary(changes),
                                  "guards": guards, "blockedBy": [g["guard"] for g in guards if not g["passed"]]})
        return out
    except CFError as e:
        return {"error": e.message}
    except Exception as e:  # 数据源不可达等：预判失败不影响试跑
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def current_rows(ds_id: int, live: dict | None, td: TableData) -> tuple[list[dict], dict | None]:
    """线上内容（投影到本次写入的列）：有发布记录取快照，否则取业务表当前内容。"""
    if live:
        raw, meta = live_rows_for(live, td.dataset, td.table)
        return project(raw, td.columns, td.key_columns), meta
    return baseline_rows(ds_id, td), None


def baseline_rows(ds_id: int, td: TableData) -> list[dict]:
    """尚无发布记录时，线上内容 = 业务表当前内容（接管前）。"""
    try:
        with datasources.engine_for(ds_id).connect() as c:
            if not writer.table_exists(c, td.table):
                return []
            _, rows = writer.read_rows(c, td.table)
    except Exception:
        return []
    return project([{"r": r} for r in rows], td.columns, td.key_columns)


# ======================= 线上指针与发布记录 =======================
def pipeline_lock(conn, pipeline_id: int, timeout: int = 0) -> bool:
    return bool(conn.execute(text("SELECT GET_LOCK(:n, :t)"), {"n": f"cellflow:pipeline:{pipeline_id}", "t": timeout}).scalar())


def pipeline_unlock(conn, pipeline_id: int) -> None:
    conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": f"cellflow:pipeline:{pipeline_id}"})


def set_live(conn, pipeline_id: int, release_id: int, expected: int | None) -> None:
    cur = conn.execute(select(live_state).where(live_state.c.pipeline_id == pipeline_id).with_for_update()).mappings().first()
    if cur is None:
        if expected is not None:
            raise CFError("LIVE_STATE_CONFLICT", "线上版本已变化", 409)
        conn.execute(live_state.insert().values(pipeline_id=pipeline_id, release_id=release_id, version=1))
        return
    if expected is not None and cur["release_id"] != expected:
        raise CFError("LIVE_STATE_CONFLICT", "线上版本已变化", 409)
    conn.execute(live_state.update().where(live_state.c.pipeline_id == pipeline_id).values(
        release_id=release_id, version=cur["version"] + 1))


def save_changes(release_id: int, per_table: dict[str, tuple[str, list[dict]]]) -> str:
    lines = []
    for dataset, (table, changes) in per_table.items():
        for ch in changes:
            lines.append(json.dumps({"releaseId": release_id, "dataset": dataset, "table": table, **ch}, ensure_ascii=False, default=str))
    key = f"changes/{release_id}.jsonl.gz"
    get_storage().put(key, gzip.compress("\n".join(lines).encode()))
    return key


def read_changes(change_uri: str | None, table: str | None = None, op: str | None = None, offset: int = 0, limit: int = 100) -> dict:
    if not change_uri or not get_storage().exists(change_uri):
        return {"total": 0, "offset": offset, "rows": [], "expired": bool(change_uri)}
    rows = [json.loads(x) for x in gzip.decompress(get_storage().get(change_uri)).decode().splitlines() if x]
    if table:
        rows = [r for r in rows if r["table"] == table]
    if op:
        rows = [r for r in rows if r["op"] == op]
    return {"total": len(rows), "offset": offset, "rows": rows[offset:offset + limit]}


def create_release(conn, **vals) -> int:
    return conn.execute(release.insert().values(**vals)).inserted_primary_key[0]


def finish_release(conn, rid: int, **vals) -> None:
    conn.execute(release.update().where(release.c.id == rid).values(published_at=func.now(3), **vals))


def release_tables(rid: int) -> list[dict]:
    with get_engine().connect() as c:
        return [dict(r) for r in c.execute(select(release_snapshot).where(release_snapshot.c.release_id == rid)).mappings()]


def keep_backup_ids(pipeline_id: int, keep: int) -> set[int]:
    with get_engine().connect() as c:
        ids = [r[0] for r in c.execute(select(release.c.id).where(and_(release.c.pipeline_id == pipeline_id,
                                                                     release.c.status == "PUBLISHED"))
                                       .order_by(release.c.id.desc()).limit(keep + 1)).all()]
    return set(ids)
