"""业务表写入：影子表 + RENAME 原子切换、崩溃恢复、校验和、备份表清理（TECH_DESIGN §10.3 SWAP、§10.4）。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

BATCH = 1000


class WriteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _short(name: str, suffix: str) -> str:
    full = f"{name}{suffix}"
    if len(full) <= 64:
        return full
    return f"cf_{hashlib.sha1(name.encode()).hexdigest()[:8]}{suffix}"[:64]


def shadow_name(table: str, job_id: int) -> str:
    return _short(table, f"__cfs_{job_id}")


def backup_name(table: str, release_id: int) -> str:
    """__cfb_{X} 保存的是 Release X 的数据。"""
    return _short(table, f"__cfb_{release_id}")


def q(name: str) -> str:
    if "`" in name:
        raise WriteError("INVALID_NAME", f"非法表名 {name}")
    return f"`{name}`"


def db_value(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return 1 if v else 0
    return v


@dataclass
class TableWrite:
    table: str
    shadow: str
    backup: str
    columns: list[str]
    rows: list[dict]

    def plan_json(self) -> dict:
        return {"table": self.table, "shadow": self.shadow, "backup": self.backup, "rows": len(self.rows)}


def table_exists(conn, name: str) -> bool:
    return conn.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"),
                        {"t": name}).scalar() > 0


def checksum(conn, table: str) -> str:
    row = conn.execute(text(f"CHECKSUM TABLE {q(table)}")).first()
    return str(row[1]) if row else ""


def read_rows(conn, table: str) -> tuple[list[str], list[dict]]:
    res = conn.execute(text(f"SELECT * FROM {q(table)}"))
    cols = list(res.keys())
    return cols, [dict(zip(cols, r, strict=True)) for r in res.all()]


def fill_shadow(engine: Engine, tw: TableWrite) -> None:
    with engine.connect() as c:
        c.execute(text(f"DROP TABLE IF EXISTS {q(tw.shadow)}"))
        c.execute(text(f"CREATE TABLE {q(tw.shadow)} LIKE {q(tw.table)}"))
        c.commit()
        if tw.rows:
            cols = ", ".join(q(x) for x in tw.columns)
            params = ", ".join(f":p{i}" for i in range(len(tw.columns)))
            stmt = text(f"INSERT INTO {q(tw.shadow)} ({cols}) VALUES ({params})")
            for k in range(0, len(tw.rows), BATCH):
                batch = [{f"p{i}": db_value(r.get(col)) for i, col in enumerate(tw.columns)} for r in tw.rows[k:k + BATCH]]
                c.execute(stmt, batch)
                c.commit()
        n = c.execute(text(f"SELECT COUNT(*) FROM {q(tw.shadow)}")).scalar()
        if n != len(tw.rows):
            raise WriteError("WRITE_VERIFY_FAILED", f"影子表 {tw.shadow} 行数 {n} 与预期 {len(tw.rows)} 不一致")


def rename_atomic(engine: Engine, pairs: list[tuple[str, str]], lock_wait: int, retries: int) -> None:
    """所有表一条 RENAME TABLE 原子交换；等待元数据锁的时间要短，否则会把业务查询堵在它后面（§10.3）。"""
    sql = "RENAME TABLE " + ", ".join(f"{q(a)} TO {q(b)}" for a, b in pairs)
    delay = 1.0
    for attempt in range(retries + 1):
        with engine.connect() as c:
            try:
                c.execute(text(f"SET SESSION lock_wait_timeout = {int(lock_wait)}"))
                c.execute(text(sql))
                return
            except OperationalError as e:
                code = e.orig.args[0] if getattr(e, "orig", None) and e.orig.args else None
                if code == 1205 and attempt < retries:  # Lock wait timeout exceeded
                    time.sleep(min(delay, 8))
                    delay *= 2
                    continue
                if code == 1205:
                    raise WriteError("TARGET_LOCK_TIMEOUT", "切换业务表时等待元数据锁超时（已重试）") from e
                raise WriteError("WRITE_FAILED", f"切换业务表失败：{str(e.orig)[:200]}") from e


def swap(engine: Engine, writes: list[TableWrite], lock_wait: int = 3, retries: int = 5) -> dict[str, str]:
    """灌数 → 校验 → 原子切换；失败时删除影子表、业务表不动。返回写后校验和。"""
    try:
        for tw in writes:
            fill_shadow(engine, tw)
        pairs: list[tuple[str, str]] = []
        with engine.connect() as c:
            for tw in writes:
                if table_exists(c, tw.backup):
                    c.execute(text(f"DROP TABLE {q(tw.backup)}"))
            c.commit()
        for tw in writes:
            pairs += [(tw.table, tw.backup), (tw.shadow, tw.table)]
        rename_atomic(engine, pairs, lock_wait, retries)
    except Exception:
        drop_shadows(engine, writes)
        raise
    with engine.connect() as c:
        return {tw.table: checksum(c, tw.table) for tw in writes}


def drop_shadows(engine: Engine, writes: list[TableWrite]) -> None:
    with engine.connect() as c:
        for tw in writes:
            c.execute(text(f"DROP TABLE IF EXISTS {q(tw.shadow)}"))
        c.commit()


def swap_back(engine: Engine, tables: list[str], live_release: int, target_release: int, lock_wait: int, retries: int) -> dict[str, str]:
    """快路径回滚：线上表改名为 __cfb_{live}，目标版本的备份表改回正式名（§10.6）。"""
    pairs = []
    with engine.connect() as c:
        for t in tables:
            b = backup_name(t, live_release)
            if table_exists(c, b):
                c.execute(text(f"DROP TABLE {q(b)}"))
        c.commit()
    for t in tables:
        pairs += [(t, backup_name(t, live_release)), (backup_name(t, target_release), t)]
    rename_atomic(engine, pairs, lock_wait, retries)
    with engine.connect() as c:
        return {t: checksum(c, t) for t in tables}


def backups_exist(engine: Engine, tables: list[str], release_id: int) -> bool:
    with engine.connect() as c:
        return all(table_exists(c, backup_name(t, release_id)) for t in tables)


def recover(engine: Engine, plan: dict) -> str:
    """崩溃恢复（§10.4）：影子表仍在 → 切换未发生；影子表不在且备份表在 → 切换已完成。"""
    tables = plan.get("tables", [])
    with engine.connect() as c:
        shadows = [table_exists(c, t["shadow"]) for t in tables]
        backups = [table_exists(c, t["backup"]) for t in tables]
    if tables and not any(shadows) and all(backups):
        return "PUBLISHED"
    drop_shadows(engine, [TableWrite(t["table"], t["shadow"], t["backup"], [], []) for t in tables])
    return "FAILED"


def cleanup_backups(engine: Engine, table: str, keep_release_ids: set[int]) -> list[str]:
    """删除不在保留集合中的备份表。"""
    removed = []
    with engine.connect() as c:
        names = [r[0] for r in c.execute(text(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE :p"),
            {"p": f"{table}\\_\\_cfb\\_%"}).all()]
        for n in names:
            try:
                rid = int(n.rsplit("__cfb_", 1)[1])
            except ValueError:
                continue
            if rid not in keep_release_ids:
                c.execute(text(f"DROP TABLE IF EXISTS {q(n)}"))
                removed.append(n)
        c.commit()
    return removed


def norm_value(v: Any) -> Any:
    """行哈希与比对用的值归一化：数字统一表示、布尔转 0/1、日期转文本、JSON 规范化。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int,)):
        return v
    if isinstance(v, float):
        # 快路径（与下方 Decimal 归一化结果一致）：整数值转 int；无指数的 repr 本身就是最短且无尾随 0 的写法
        r = repr(v)
        if v.is_integer():
            if -1e15 < v < 1e15:
                return int(v)
        elif "e" not in r and "n" not in r:
            return r
        d = Decimal(r).normalize()
        return int(d) if d == d.to_integral_value() else format(d, "f")
    if isinstance(v, Decimal):
        d = v.normalize()
        return int(d) if d == d.to_integral_value() else format(d, "f")
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def row_hash(row: dict, columns: list[str]) -> str:
    payload = json.dumps([[c, norm_value(row.get(c))] for c in sorted(columns)], ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
