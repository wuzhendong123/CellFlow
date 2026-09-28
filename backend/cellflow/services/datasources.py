"""数据源与目标表检查（T08；TECH_DESIGN §10.3「策略选择」、F7、F15）。"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from sqlalchemy import Engine, create_engine, select, text

from cellflow import secret_box
from cellflow.config import ConfigError, resolve_ref
from cellflow.engine.types import parse_type
from cellflow.errors import CFError, not_found
from cellflow.meta.db import get_engine
from cellflow.meta.tables import datasource, pipeline, table_owner
from cellflow.runtime import writer

INTERNAL_TABLE_MARKERS = ("__cfs_", "__cfb_")
MARKER_TABLE = "_cellflow_marker"


def list_all() -> list[dict]:
    with get_engine().connect() as c:
        rows = [dict(r) for r in c.execute(select(datasource).order_by(datasource.c.id)).mappings()]
        used = {}
        for r in c.execute(select(pipeline.c.datasource_id, pipeline.c.id)).all():
            used[r[0]] = used.get(r[0], 0) + 1
    for r in rows:
        r["pipelineCount"] = used.get(r["id"], 0)
    return rows


def get(ds_id: int) -> dict:
    with get_engine().connect() as c:
        row = c.execute(select(datasource).where(datasource.c.id == ds_id)).mappings().first()
    if not row:
        raise not_found("数据源")
    return dict(row)


def save(data: dict, ds_id: int | None = None) -> dict:
    """两种连接方式：REF（只登记环境变量引用名，生产推荐）；DIRECT（控制台直接填写，口令用 CF_SECRET_KEY 加密存储）。"""
    mode = (data.get("conn_mode") or "REF").upper()
    if mode not in ("REF", "DIRECT"):
        raise CFError("INVALID_REQUEST", "连接方式只能是 REF 或 DIRECT", 400)
    vals: dict = {"name": (data.get("name") or "").strip(), "db_name": (data.get("db_name") or "").strip(), "conn_mode": mode}
    if not vals["name"] or not vals["db_name"]:
        raise CFError("INVALID_REQUEST", "名称、库名必填", 400)
    old = get(ds_id) if ds_id is not None else None
    if mode == "REF":
        vals.update(host_ref=data.get("host_ref"), credential_ref=data.get("credential_ref") or None,
                    host=None, port=None, username=None, password_enc=None)
        if not vals["host_ref"]:
            raise CFError("INVALID_REQUEST", "连接引用名必填", 400)
        for k in ("host_ref", "credential_ref"):
            v = vals.get(k)
            if v and ("://" in v or "@" in v or ":" in v):
                raise CFError("INVALID_REQUEST", "只能填写引用名，不能填写真实连接地址或口令", 400)
    else:
        host = (data.get("host") or "").strip()
        user = (data.get("username") or "").strip()
        port = int(data.get("port") or 3306)
        if not host or not user:
            raise CFError("INVALID_REQUEST", "主机、账号必填", 400)
        if any(ch in host for ch in "/@ ?#") or not (0 < port < 65536):
            raise CFError("INVALID_REQUEST", "主机只填地址（如 10.0.0.5 或 host.docker.internal），端口 1~65535", 400)
        pwd = data.get("password")
        if pwd:
            enc = secret_box.encrypt(pwd)
        elif old and old.get("conn_mode") == "DIRECT" and old.get("password_enc"):
            enc = old["password_enc"]  # 编辑时不填口令表示不修改
        else:
            raise CFError("INVALID_REQUEST", "口令必填", 400)
        vals.update(host_ref=None, credential_ref=None, host=host, port=port, username=user, password_enc=enc)
    with get_engine().begin() as c:
        if ds_id is None:
            if c.execute(select(datasource.c.id).where(datasource.c.name == vals["name"])).first():
                raise CFError("INVALID_REQUEST", f"数据源名称「{vals['name']}」已存在", 409)
            ds_id = c.execute(datasource.insert().values(**vals)).inserted_primary_key[0]
        else:
            c.execute(datasource.update().where(datasource.c.id == ds_id).values(**vals))
    engine_for.cache_clear()
    return get(ds_id)


def public_json(d: dict) -> dict:
    """对外展示（不含口令）。"""
    return {"id": d["id"], "name": d["name"], "mode": d.get("conn_mode") or "REF", "dbName": d["db_name"],
            "hostRef": d.get("host_ref"), "credentialRef": d.get("credential_ref"),
            "host": d.get("host"), "port": d.get("port"), "username": d.get("username"),
            "hasPassword": bool(d.get("password_enc")), "pipelineCount": d.get("pipelineCount")}


def delete(ds_id: int) -> None:
    with get_engine().begin() as c:
        n = c.execute(select(pipeline.c.id).where(pipeline.c.datasource_id == ds_id)).first()
        if n:
            raise CFError("DATASOURCE_IN_USE", "仍有方案使用该数据源，不能删除", 409)
        c.execute(datasource.delete().where(datasource.c.id == ds_id))
    engine_for.cache_clear()


def _url(ds: dict) -> str:
    if (ds.get("conn_mode") or "REF") == "DIRECT":
        user = quote_plus(ds["username"] or "")
        pwd = quote_plus(secret_box.decrypt(ds["password_enc"])) if ds.get("password_enc") else ""
        return f"mysql+pymysql://{user}:{pwd}@{ds['host']}:{ds.get('port') or 3306}/{ds['db_name']}?charset=utf8mb4"
    base = resolve_ref(ds["host_ref"]).rstrip("/")
    if ds.get("credential_ref"):
        cred = resolve_ref(ds["credential_ref"])
        scheme, _, rest = base.partition("://")
        rest = rest.split("@", 1)[-1]
        base = f"{scheme}://{cred}@{rest}"
    return f"{base}/{ds['db_name']}?charset=utf8mb4"


@lru_cache(maxsize=32)
def engine_for(ds_id: int) -> Engine:
    ds = get(ds_id)
    try:
        return create_engine(_url(ds), pool_pre_ping=True, pool_recycle=3600, future=True)
    except ConfigError as e:
        raise CFError("DATASOURCE_REF_MISSING", str(e), 422) from e


def test_connection(ds_id: int) -> dict:
    checks: dict[str, bool] = {}
    detail: dict[str, str] = {}
    try:
        eng = engine_for(ds_id)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        checks["connect"] = True
    except Exception as e:
        return {"ok": False, "checks": {"connect": False}, "detail": {"connect": type(e).__name__ + ": " + str(e)[:200]}}
    probe = "_cellflow_probe"
    steps = [
        ("CREATE", f"CREATE TABLE IF NOT EXISTS `{probe}` (id INT PRIMARY KEY)"),
        ("INSERT", f"INSERT INTO `{probe}` VALUES (1)"),
        ("SELECT", f"SELECT COUNT(*) FROM `{probe}`"),
        ("ALTER", f"RENAME TABLE `{probe}` TO `{probe}_r`, `{probe}_r` TO `{probe}`"),
        ("DROP", f"DROP TABLE IF EXISTS `{probe}`"),
        ("MARKER", f"CREATE TABLE IF NOT EXISTS `{MARKER_TABLE}` (pipeline_id BIGINT PRIMARY KEY, release_id BIGINT NOT NULL)"),
    ]
    with eng.connect() as c:
        for name, sql in steps:
            try:
                c.execute(text(sql))
                c.commit()
                checks[name] = True
            except Exception as e:
                checks[name] = False
                detail[name] = str(e)[:200]
                c.rollback()
        try:
            c.execute(text(f"DROP TABLE IF EXISTS `{probe}`"))
            c.commit()
        except Exception:
            c.rollback()
    return {"ok": all(checks.values()), "checks": checks, "detail": detail}


def list_tables(ds_id: int) -> list[dict]:
    ds = get(ds_id)
    with engine_for(ds_id).connect() as c:
        names = [r[0] for r in c.execute(text(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=:s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
        ), {"s": ds["db_name"]}).all()]
    names = [n for n in names if not any(m in n for m in INTERNAL_TABLE_MARKERS) and n not in (MARKER_TABLE, "_cellflow_probe")]
    owners = {}
    with get_engine().connect() as c:
        for r in c.execute(select(table_owner.c.table_name, pipeline.c.code, pipeline.c.id).join(
                pipeline, pipeline.c.id == table_owner.c.pipeline_id).where(table_owner.c.datasource_id == ds_id)).all():
            owners[r[0]] = {"pipelineId": r[2], "pipelineCode": r[1]}
    return [{"table": n, "owner": owners.get(n)} for n in names]


def describe_table(ds_id: int, table: str) -> dict:
    ds = get(ds_id)
    s = ds["db_name"]
    with engine_for(ds_id).connect() as c:
        t = c.execute(text("SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t"),
                      {"s": s, "t": table}).first()
        if not t:
            raise CFError("TABLE_NOT_FOUND", f"目标表「{table}」不存在", 404)
        cols = [dict(r) for r in c.execute(text(
            "SELECT COLUMN_NAME name, DATA_TYPE dataType, COLUMN_TYPE columnType, IS_NULLABLE nullable, COLUMN_DEFAULT dflt, "
            "EXTRA extra, CHARACTER_MAXIMUM_LENGTH maxLength, NUMERIC_PRECISION numPrecision, NUMERIC_SCALE numScale, "
            "CHARACTER_SET_NAME charset, COLUMN_KEY columnKey FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t ORDER BY ORDINAL_POSITION"), {"s": s, "t": table}).mappings()]
        idx = c.execute(text(
            "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t "
            "ORDER BY INDEX_NAME, SEQ_IN_INDEX"), {"s": s, "t": table}).all()
        triggers = [r[0] for r in c.execute(text(
            "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE EVENT_OBJECT_SCHEMA=:s AND EVENT_OBJECT_TABLE=:t"),
            {"s": s, "t": table}).all()]
        referenced_by = [f"{r[0]}.{r[1]}" for r in c.execute(text(
            "SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE REFERENCED_TABLE_SCHEMA=:s AND REFERENCED_TABLE_NAME=:t"), {"s": s, "t": table}).all()]
        own_fks = [r[0] for r in c.execute(text(
            "SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t AND REFERENCED_TABLE_NAME IS NOT NULL"), {"s": s, "t": table}).all()]
        rows = c.execute(text(f"SELECT COUNT(*) FROM {writer.q(table)}")).scalar()
    indexes: dict[str, dict] = {}
    for name, non_unique, col in idx:
        indexes.setdefault(name, {"name": name, "unique": not non_unique, "columns": []})["columns"].append(col)
    for col in cols:
        col["nullable"] = col["nullable"] == "YES"
        col["autoIncrement"] = "auto_increment" in (col["extra"] or "")
        col["hasDefault"] = col["dflt"] is not None or col["autoIncrement"] or "DEFAULT_GENERATED" in (col["extra"] or "")
    pk = indexes.get("PRIMARY", {}).get("columns", [])
    return {
        "table": table, "collation": t[1], "columns": cols, "primaryKey": pk,
        "uniqueKeys": [i["columns"] for n, i in indexes.items() if i["unique"] and n != "PRIMARY"],
        "triggers": triggers, "referencedBy": referenced_by, "foreignKeys": own_fks, "rowCount": rows,
    }


_COMPAT = {
    "int": {"tinyint", "smallint", "mediumint", "int", "bigint", "decimal", "float", "double", "varchar", "char", "text",
            "bit", "year"},
    "float": {"float", "double", "decimal", "varchar", "char", "text"},
    "decimal": {"decimal", "float", "double", "varchar", "char", "text"},
    "string": {"varchar", "char", "text", "tinytext", "mediumtext", "longtext", "enum", "set", "json"},
    "bool": {"tinyint", "bit", "smallint", "int", "bigint", "varchar", "char"},
    "date": {"date", "datetime", "timestamp", "varchar", "char"},
    "datetime": {"datetime", "timestamp", "date", "varchar", "char"},
    "json": {"json", "text", "mediumtext", "longtext", "varchar"},
}


def _kind(t: str) -> str:
    n = parse_type(t).name
    return {"long": "int", "list": "json", "struct": "json", "enum": "string"}.get(n, n)


def check_binding(ds_id: int, binding: dict, input_columns: list[dict] | None, pipeline_id: int | None = None) -> dict:
    """绑定检查：结构兼容、写入策略可行性、表占用、主键建议（F7-1~F7-6）。"""
    errors: list[dict] = []
    warnings: list[dict] = []
    table = binding.get("table")
    if not table:
        return {"ok": False, "errors": [{"code": "DSL_INVALID", "message": "未选择目标表"}], "warnings": []}
    try:
        desc = describe_table(ds_id, table)
    except CFError as e:
        return {"ok": False, "errors": [{"code": e.code, "message": e.message}], "warnings": []}
    cols = {c["name"]: c for c in desc["columns"]}
    in_types = {c["field"]: c["type"] for c in (input_columns or [])}
    mapped_cols = set()
    for m in binding.get("columnMapping") or []:
        col = cols.get(m.get("column"))
        if col is None:
            errors.append({"code": "TARGET_SCHEMA_MISMATCH", "message": f"目标表没有列「{m.get('column')}」"})
            continue
        mapped_cols.add(col["name"])
        ft = in_types.get(m.get("field"))
        if ft and col["dataType"] not in _COMPAT.get(_kind(ft), set()):
            errors.append({"code": "TARGET_SCHEMA_MISMATCH",
                           "message": f"字段「{m['field']}」（{ft}）不能写入列「{col['name']}」（{col['columnType']}）"})
    for c in desc["columns"]:
        if c["name"] not in mapped_cols and not c["nullable"] and not c["hasDefault"]:
            errors.append({"code": "TARGET_SCHEMA_MISMATCH", "message": f"列「{c['name']}」不可为空且没有默认值，但没有映射来源"})
        if c["charset"] and c["charset"] not in ("utf8mb4",) and c["name"] in mapped_cols:
            warnings.append({"code": "CHARSET_NOT_UTF8MB4", "message": f"列「{c['name']}」字符集为 {c['charset']}，emoji 等字符无法写入"})
    reasons = []
    if desc["triggers"]:
        reasons.append("表上有触发器（整表替换后触发器会留在备份表上）")
    if desc["referencedBy"]:
        reasons.append("被其他表的外键引用（外键会指向备份表）")
    if desc["foreignKeys"]:
        reasons.append("表自身有外键（影子表不会复制外键）")
    auto = [c["name"] for c in desc["columns"] if c["autoIncrement"]]
    unmapped_auto = [a for a in auto if a not in mapped_cols]
    if unmapped_auto and desc["referencedBy"]:
        reasons.append(f"自增列「{unmapped_auto[0]}」未映射且被引用（整表替换会重新分配 ID）")
    elif unmapped_auto:
        warnings.append({"code": "AUTO_ID_REASSIGNED", "message": f"自增列「{unmapped_auto[0]}」未映射：每次整表替换 ID 会重新分配，若有其他地方引用该 ID 请映射或改用增量写入（v2）"})
    strategy = binding.get("strategy", "SWAP")
    if reasons:
        errors.append({"code": "STRATEGY_NOT_AVAILABLE", "message": "该表不能使用整表替换：" + "；".join(reasons) + "。v1 暂不支持此类表（v2 增量写入）"})
    elif strategy != "SWAP":
        errors.append({"code": "STRATEGY_NOT_AVAILABLE", "message": "v1 只支持整表替换（SWAP）"})
    with get_engine().connect() as c:
        own = c.execute(select(table_owner.c.pipeline_id, pipeline.c.code).join(pipeline, pipeline.c.id == table_owner.c.pipeline_id)
                        .where(table_owner.c.datasource_id == ds_id, table_owner.c.table_name == table)).first()
    if own and own[0] != pipeline_id:
        errors.append({"code": "TABLE_OWNED", "message": f"目标表已被方案「{own[1]}」占用（D9）"})
    suggestion = None
    if not binding.get("keyFields"):
        key_cols = desc["primaryKey"] if not (len(desc["primaryKey"]) == 1 and desc["primaryKey"][0] in unmapped_auto) else None
        key_cols = key_cols or (desc["uniqueKeys"][0] if desc["uniqueKeys"] else None)
        if key_cols:
            by_col = {m["column"]: m["field"] for m in binding.get("columnMapping") or []}
            fields = [by_col.get(k) for k in key_cols]
            if all(fields):
                suggestion = fields
                warnings.append({"code": "KEY_SUGGESTED", "message": f"建议使用 {', '.join(fields)} 作为主键（对应目标表键 {', '.join(key_cols)}）"})
    if desc["rowCount"] and not own:
        warnings.append({"code": "TAKEOVER_BASELINE", "message": f"目标表已有 {desc['rowCount']} 行数据，首次发布前会保存为基线，可回滚到接管前"})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "table": desc, "keySuggestion": suggestion,
            "strategies": {"SWAP": not reasons, "APPLY_DIFF": False}}
