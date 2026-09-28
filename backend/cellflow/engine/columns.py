"""L3 字段绑定与类型清洗（TECH_DESIGN §5.3、§7.8）。

- 按表头名（而非列序号）绑定字段；缺少必填列报错、新列警告。
- 逐值清洗与类型转换；转换失败的单元格记 ERROR，所在行从输出中移除（C7）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from cellflow.engine.dataset import ColumnSchema, Dataset
from cellflow.engine.grid import ERROR, F_NOCACHE, is_blank, norm_text
from cellflow.engine.shapes import ShapeResult
from cellflow.engine.types import (
    DEFAULT_NULLS,
    FIELD_NAME,
    RESERVED_FIELDS,
    CoerceError,
    clean_text,
    convert,
    parse_type,
)


def validate_column_specs(specs: list[dict]) -> list[str]:
    """保存时校验字段名：合法标识符、非保留字、不重复（C1）。"""
    errors, seen = [], set()
    for s in specs:
        f = s.get("field", "")
        if not FIELD_NAME.match(f):
            errors.append(f"字段名「{f}」只能包含字母、数字、下划线，且不能以数字开头")
        elif f in RESERVED_FIELDS:
            errors.append(f"字段名「{f}」是保留字")
        if f in seen:
            errors.append(f"字段名「{f}」重复")
        seen.add(f)
        try:
            parse_type(s.get("type", "string"))
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def _match_header(source: str, headers: list[str]) -> str | None:
    if len(source) > 2 and source.startswith("/") and source.endswith("/"):
        rx = re.compile(source[1:-1])
        for h in headers:
            if rx.fullmatch(h):
                return h
        return None
    target = norm_text(source)
    for h in headers:
        if norm_text(h) == target:
            return h
    return None


def bind_and_coerce(
    shape: ShapeResult,
    specs: list[dict],
    *,
    node_id: str,
    region_id: str,
    date1904: bool = False,
    cell_flag: Callable[[str], int] | None = None,
    new_columns: str = "DROP",
) -> tuple[Dataset, list[dict], dict]:
    """返回 (数据集, 问题列表, 统计)。"""
    issues: list[dict] = list(shape.issues)
    headers = shape.headers
    bound: list[tuple[dict, str | None]] = []
    used: set[str] = set()
    for spec in specs:
        src = spec.get("source") or spec["field"]
        h = _match_header(src, headers)
        if h is None:
            sev = "ERROR" if spec.get("required") else "WARN"
            code = "HEADER_MISSING_REQUIRED" if spec.get("required") else "HEADER_MISSING"
            issues.append({"severity": sev, "code": code, "message": f"找不到表头「{src}」", "field": spec["field"]})
        else:
            used.add(h)
        bound.append((spec, h))
    extra = [h for h in headers if h not in used and not h.startswith("_block")]
    extra_with_data = []
    for h in extra:
        j = headers.index(h)
        if any(not is_blank(r[j]) for r in shape.rows):
            extra_with_data.append(h)
    if extra_with_data and not shape.internal_headers:
        issues.append(
            {
                "severity": "WARN",
                "code": "HEADER_NEW_COLUMN",
                "message": "发现未配置的新列：" + "、".join(extra_with_data),
                "headers": extra_with_data,
            }
        )
    columns = [
        ColumnSchema(spec["field"], str(parse_type(spec.get("type") or shape.declared_types.get(h or "", "") or "string")),
                     bool(spec.get("isKey")), origin=h)
        for spec, h in bound
    ]
    if new_columns == "INCLUDE_AS_STRING" and not shape.internal_headers:
        for h in extra_with_data:
            columns.append(ColumnSchema(re.sub(r"\W", "_", h), "string", origin=h))
            bound.append(({"field": re.sub(r"\W", "_", h), "type": "string"}, h))
    if "_block" in headers:
        columns.append(ColumnSchema("_block", "int", origin="_block"))
        bound.append(({"field": "_block", "type": "int"}, "_block"))
    idx = {h: i for i, h in enumerate(headers)}
    types = [parse_type(c.type) for c in columns]
    out = Dataset(columns)
    dropped = 0
    for ri, row in enumerate(shape.rows):
        rec: dict[str, Any] = {}
        lin: dict[str, Any] = {}
        bad = False
        for (spec, h), t in zip(bound, types, strict=True):
            f = spec["field"]
            raw = row[idx[h]] if h is not None else None
            addr = shape.lineage[ri].get(h) if h is not None else None
            lin[f] = addr
            if h is None:
                rec[f] = spec.get("default")
                continue
            try:
                rec[f] = coerce_value(raw, t, spec, date1904, cell_flag(addr) if (cell_flag and addr) else 0)
            except CoerceError as e:
                issues.append({"severity": "ERROR", "code": e.code, "message": e.message, "cell": addr, "field": f,
                               "value": raw})
                bad = True
        if bad:
            dropped += 1
            continue
        out.rows.append(rec)
        out.lineage.append(lin)
        out.meta.append({"rid": f"{node_id}/{region_id}/{ri + 1:05d}", "sheetRow": shape.meta[ri]["sheetRow"],
                         "index": ri + 1})
    return out, issues, {"dropped": dropped}


def coerce_value(raw: Any, t, spec: dict, date1904: bool, flag: int) -> Any:
    if flag & F_NOCACHE:
        raise CoerceError("CELL_FORMULA_NO_CACHE", "公式没有计算结果，请用 Excel 打开并保存一次")
    if flag & ERROR:
        raise CoerceError("CELL_ERROR_VALUE", f"单元格为错误值 {raw}")
    v = clean_text(raw, spec.get("trim", True), spec.get("normalizeWidth", True))
    nulls = spec.get("nullTokens")
    nulls = DEFAULT_NULLS if nulls is None else nulls
    if v is None or (isinstance(v, str) and v in nulls) or is_blank(v):
        if spec.get("required"):
            raise CoerceError("REQUIRED_EMPTY", "必填字段为空")
        return spec.get("default")
    return convert(v, t, spec, date1904)
