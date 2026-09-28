"""L2 形态解析：把一块区域打平成「行」（TECH_DESIGN §4.3、§7.4~§7.7.1）。

统一契约：输入 Block → 输出 ShapeResult（表头名、行值、每个值的来源单元格、行元信息）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from cellflow.engine.grid import BOLD, HIDDEN_ROW, STRIKE, SheetGrid, is_blank, norm_text, to_a1
from cellflow.engine.locate import Block, Cell, LocateError, Rect, find_cells, slice_block


class ShapeError(LocateError):
    """形态解析中的结构性错误。"""


@dataclass
class ShapeResult:
    headers: list[str]
    rows: list[list[Any]] = field(default_factory=list)
    lineage: list[dict[str, str]] = field(default_factory=list)  # 表头名 → 'Sheet!A1'
    meta: list[dict[str, int]] = field(default_factory=list)  # {"sheetRow": 12}
    issues: list[dict] = field(default_factory=list)  # {severity, code, message, cell?}
    declared_types: dict[str, str] = field(default_factory=dict)  # 带类型声明的键值区
    internal_headers: bool = False  # 表头由配置生成（矩阵维度、纵向键值），不做「新列」提示

    def add(self, values: list[Any], lineage: dict[str, str], sheet_row: int) -> None:
        self.rows.append(values)
        self.lineage.append(lineage)
        self.meta.append({"sheetRow": sheet_row})


@dataclass
class RowPolicy:
    hidden_rows: str = "KEEP_WARN"  # KEEP_WARN | SKIP
    strikethrough_rows: str = "KEEP"  # KEEP | SKIP


def _ffill(arr: np.ndarray, axis: int) -> np.ndarray:
    out = arr.copy()
    if axis == 1:
        for i in range(out.shape[0]):
            last = None
            for j in range(out.shape[1]):
                if is_blank(out[i, j]):
                    out[i, j] = last
                else:
                    last = out[i, j]
    else:
        for j in range(out.shape[1]):
            last = None
            for i in range(out.shape[0]):
                if is_blank(out[i, j]):
                    out[i, j] = last
                else:
                    last = out[i, j]
    return out


def dedupe_names(names: list[str]) -> tuple[list[str], list[str]]:
    seen: dict[str, int] = {}
    out, dups = [], []
    for n in names:
        if n in seen:
            seen[n] += 1
            new = f"{n}_{seen[n]}"
            dups.append(n)
            out.append(new)
        else:
            seen[n] = 1
            out.append(n)
    return out, dups


class _View:
    """按方向（按行 / 按列）看待一个区块。"""

    def __init__(self, block: Block, orientation: str):
        self.block = block
        self.t = orientation in ("COLUMN", "HORIZONTAL")
        self.values = block.values.T if self.t else block.values
        self.flags = block.flags.T if self.t else block.flags

    def addr(self, i: int, j: int) -> str:
        return self.block.addr(j, i) if self.t else self.block.addr(i, j)

    def sheet_row(self, i: int, j: int = 0) -> int:
        return self.block.origin.r1 + (j if self.t else i)


def _skip_row(view: _View, i: int, policy: RowPolicy, counters: dict) -> bool:
    fl = view.flags[i, :]
    if not view.t:
        if (fl & HIDDEN_ROW).any():
            if policy.hidden_rows == "SKIP":
                return True
            counters["hidden"] = counters.get("hidden", 0) + 1
        if policy.strikethrough_rows == "SKIP" and fl.size and (fl[0] & STRIKE):
            return True
    return False


def _header_names(view: _View, k: int, joiner: str) -> tuple[list[str], list[dict]]:
    w = view.values.shape[1]
    if k <= 0:
        return [to_a1(1, j + 1)[:-1] for j in range(w)], []
    hdr = view.values[:k, :]
    upper = _ffill(hdr[: k - 1, :], axis=1) if k > 1 else hdr[:0, :]
    names, issues = [], []
    for j in range(w):
        parts = []
        for lvl in range(k):
            v = upper[lvl, j] if lvl < k - 1 else hdr[k - 1, j]
            t = norm_text(v)
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        name = joiner.join(parts)
        if not name:
            name = f"_col_{to_a1(1, view.block.origin.c1 + j)[:-1]}" if not view.t else f"_row_{view.block.origin.r1 + j}"
        names.append(name)
    names, dups = dedupe_names(names)
    for d in sorted(set(dups)):
        issues.append({"severity": "WARN", "code": "HEADER_DUPLICATE", "message": f"表头「{d}」重复，已自动编号"})
    return names, issues


def parse_detail(block: Block, opt: dict, policy: RowPolicy | None = None) -> ShapeResult:
    policy = policy or RowPolicy()
    view = _View(block, opt.get("orientation", "ROW"))
    k = int(opt.get("headerRows", 1))
    headers, issues = _header_names(view, k, opt.get("headerJoiner", "."))
    res = ShapeResult(headers, issues=issues)
    skip = opt.get("skipRows") or {}
    prefix = skip.get("commentPrefix")
    subtotal = [norm_text(x) for x in opt.get("subtotalMarkers") or []]
    counters: dict = {}
    for i in range(k, view.values.shape[0]):
        row = view.values[i, :]
        if skip.get("blank", True) and all(is_blank(v) for v in row):
            continue
        first = norm_text(row[0]) if row.size else ""
        if prefix and first.startswith(prefix):
            continue
        if subtotal and first in subtotal:
            continue
        if _skip_row(view, i, policy, counters):
            continue
        res.add(list(row), {h: view.addr(i, j) for j, h in enumerate(headers)}, view.sheet_row(i))
    if counters.get("hidden"):
        res.issues.append(
            {"severity": "WARN", "code": "HIDDEN_ROWS_READ", "message": f"读取了 {counters['hidden']} 行隐藏行（隐藏不等于删除）"}
        )
    return res


def parse_kv(block: Block, opt: dict, policy: RowPolicy | None = None) -> ShapeResult:
    view = _View(block, opt.get("orientation", "VERTICAL"))
    kc, vc = int(opt.get("keyCol", 1)) - 1, int(opt.get("valueCol", 2)) - 1
    tc = int(opt["typeCol"]) - 1 if opt.get("typeCol") else None
    dup_policy = opt.get("duplicateKey", "ERROR")
    record: dict[str, Any] = {}
    lin: dict[str, str] = {}
    types: dict[str, str] = {}
    rows_of: dict[str, int] = {}
    issues: list[dict] = []
    for i in range(view.values.shape[0]):
        if kc >= view.values.shape[1]:
            break
        key = norm_text(view.values[i, kc])
        if not key or key.startswith("#"):
            continue
        val = view.values[i, vc] if vc < view.values.shape[1] else None
        if key in record:
            if dup_policy == "ERROR":
                issues.append(
                    {"severity": "ERROR", "code": "KV_DUPLICATE_KEY", "message": f"键「{key}」重复", "cell": view.addr(i, kc)}
                )
                continue
            if dup_policy == "ARRAY":
                prev = record[key]
                record[key] = (prev if isinstance(prev, list) else [prev]) + [val]
                continue
        record[key] = val
        lin[key] = view.addr(i, vc)
        rows_of[key] = view.sheet_row(i, 0)
        if tc is not None and tc < view.values.shape[1]:
            types[key] = norm_text(view.values[i, tc])
    if opt.get("outputMode", "WIDE") == "LONG":
        res = ShapeResult(["key", "value"], issues=issues, internal_headers=True)
        for key, val in record.items():
            res.add([key, val], {"key": lin[key], "value": lin[key]}, rows_of[key])
        return res
    headers = list(record.keys())
    res = ShapeResult(headers, issues=issues, declared_types=types)
    if headers:
        res.add([record[h] for h in headers], lin, block.origin.r1)
    return res


def _parse_dim(value: Any, parser: dict | None) -> Any:
    if parser is None or is_blank(value):
        return value
    m = re.fullmatch(parser["regex"], norm_text(value))
    if not m:
        return value
    raw = m.group(1) if m.groups() else m.group(0)
    t = parser.get("type", "string")
    try:
        if t == "int":
            return int(raw)
        if t in ("float", "double"):
            return float(raw)
    except ValueError:
        return value
    return raw


def parse_matrix(block: Block, opt: dict, policy: RowPolicy | None = None) -> ShapeResult:
    m, n = int(opt.get("colHeaderRows", 1)), int(opt.get("rowHeaderCols", 1))
    raw = block.values
    row_dims = list(opt.get("rowDims") or [f"row{i + 1}" for i in range(n)])
    col_dims = list(opt.get("colDims") or [f"col{i + 1}" for i in range(m)])
    if len(row_dims) != n or len(col_dims) != m:
        raise ShapeError("MATRIX_DIMS_MISMATCH", "矩阵维度名个数与行头列数/列头行数不一致")
    value_name = opt.get("valueName", "value")
    col_hdr = _ffill(raw[:m, n:], axis=1)
    row_hdr = _ffill(raw[m:, :n], axis=0)
    body = raw[m:, n:]
    totals = {norm_text(x) for x in opt.get("totalMarkers") or []}
    keep_c = [j for j in range(body.shape[1]) if not any(norm_text(x) in totals for x in col_hdr[:, j])
              and not all(is_blank(x) for x in col_hdr[:, j])]
    keep_r = [i for i in range(body.shape[0]) if not any(norm_text(x) in totals for x in row_hdr[i, :])
              and not all(is_blank(x) for x in row_hdr[i, :])]
    parsers = opt.get("dimParsers") or {}
    drop_empty = opt.get("dropEmpty", True)
    metric = opt.get("metricLevel")
    long_rows: list[tuple[dict, dict, int]] = []
    for i in keep_r:
        for j in keep_c:
            v = body[i, j]
            if drop_empty and is_blank(v):
                continue
            rec, lin = {}, {}
            for d, name in enumerate(row_dims):
                rec[name] = _parse_dim(row_hdr[i, d], parsers.get(name))
                lin[name] = block.addr(m + i, d)
            for d, name in enumerate(col_dims):
                rec[name] = _parse_dim(col_hdr[d, j], parsers.get(name))
                lin[name] = block.addr(d, n + j)
            rec[value_name] = v
            lin[value_name] = block.addr(m + i, n + j)
            long_rows.append((rec, lin, block.sheet_row(m + i)))
    if not metric:
        headers = row_dims + col_dims + [value_name]
        res = ShapeResult(headers, internal_headers=True)
        for rec, lin, sr in long_rows:
            res.add([rec[h] for h in headers], lin, sr)
        return res
    # 某一级维度其实是「指标名」：逆透视后再按指标横向展开
    if metric not in row_dims + col_dims:
        raise ShapeError("MATRIX_METRIC_UNKNOWN", f"指标层「{metric}」不是矩阵维度之一")
    dims = [d for d in row_dims + col_dims if d != metric]
    grouped: dict[tuple, tuple[dict, dict, int]] = {}
    metrics: list[str] = []
    issues = []
    for rec, lin, sr in long_rows:
        key = tuple(rec[d] for d in dims)
        mname = norm_text(rec[metric])
        if mname not in metrics:
            metrics.append(mname)
        out, olin, _ = grouped.setdefault(key, ({d: rec[d] for d in dims}, {d: lin[d] for d in dims}, sr))
        if mname in out:
            issues.append({"severity": "ERROR", "code": "MATRIX_DUPLICATE_CELL", "message": f"指标「{mname}」在同一维度组合下重复",
                           "cell": lin[value_name]})
            continue
        out[mname] = rec[value_name]
        olin[mname] = lin[value_name]
    headers = dims + metrics
    res = ShapeResult(headers, issues=issues)
    for out, olin, sr in grouped.values():
        res.add([out.get(h) for h in headers], olin, sr)
    return res


def parse_summary(block: Block, opt: dict, policy: RowPolicy | None = None, align_headers: list[str] | None = None) -> ShapeResult:
    if opt.get("layout", "ROW") == "KV":
        return parse_kv(block, {**opt, "outputMode": "WIDE"})
    headers = list(align_headers or [])
    w = block.values.shape[1]
    headers = (headers + [f"_col_{to_a1(1, block.origin.c1 + j)[:-1]}" for j in range(len(headers), w)])[:w]
    headers, _ = dedupe_names(headers)
    res = ShapeResult(headers)
    for i in range(block.values.shape[0]):
        row = list(block.values[i, :])
        if all(is_blank(v) for v in row):
            continue
        res.add(row, {h: block.addr(i, j) for j, h in enumerate(headers)}, block.sheet_row(i))
        break  # 汇总区只取第一条非空行
    return res


def parse_grouped(block: Block, opt: dict, policy: RowPolicy | None = None) -> ShapeResult:
    policy = policy or RowPolicy()
    view = _View(block, "ROW")
    k = int(opt.get("headerRows", 1))
    headers, issues = _header_names(view, k, opt.get("headerJoiner", "."))
    group_field = opt.get("groupField", "group")
    det = opt.get("groupRowDetector") or {"mode": "FIRST_CELL_ONLY"}
    sub = opt.get("subtotalDetector")
    res = ShapeResult([*headers, group_field], issues=issues)
    group_val, group_addr = None, None
    counters: dict = {}
    for i in range(k, view.values.shape[0]):
        row = view.values[i, :]
        if all(is_blank(v) for v in row):
            continue
        first = norm_text(row[0])
        if sub and first and re.fullmatch(sub["pattern"], first):
            continue
        mode = det.get("mode", "FIRST_CELL_ONLY")
        is_group = False
        if mode == "FIRST_CELL_ONLY":
            is_group = bool(first) and all(is_blank(v) for v in row[1:])
        elif mode == "REGEX":
            is_group = bool(first) and re.fullmatch(det["pattern"], first) is not None
        elif mode == "BOLD":
            is_group = bool(first) and bool(view.flags[i, 0] & BOLD) and all(is_blank(v) for v in row[1:])
        if is_group:
            m = re.fullmatch(det["pattern"], first) if det.get("pattern") else None
            group_val = (m.group(1) if m and m.groups() else first)
            group_addr = view.addr(i, 0)
            continue
        if _skip_row(view, i, policy, counters):
            continue
        lin = {h: view.addr(i, j) for j, h in enumerate(headers)}
        lin[group_field] = group_addr or ""
        res.add([*row, group_val], lin, view.sheet_row(i))
    return res


def parse_form(block: Block, opt: dict, policy: RowPolicy | None = None) -> ShapeResult:
    fields: dict[str, dict] = opt.get("fields") or {}
    rec, lin = [], {}
    h, w = block.values.shape
    for name, off in fields.items():
        r, c = int(off.get("row", 0)), int(off.get("col", 0))
        if r >= h or c >= w or r < 0 or c < 0:
            raise ShapeError("FORM_FIELD_OUT_OF_RANGE", f"表单字段「{name}」超出区域范围")
        rec.append(block.values[r, c])
        lin[name] = block.addr(r, c)
    res = ShapeResult(list(fields.keys()))
    res.add(rec, lin, block.origin.r1)
    return res


def parse_repeating(grid: SheetGrid, rect: Rect, opt: dict, masks: list[Rect]) -> ShapeResult:
    size = opt.get("blockSize") or {}
    bh, bw = int(size.get("rows", 1)), int(size.get("cols", 1))
    if opt.get("blockAnchor"):
        starts = find_cells(grid, opt["blockAnchor"], "REGEX", rect)
        starts = sorted(starts, key=lambda c: (c.row, c.col))
    else:
        stride = opt.get("stride") or {}
        dr, dc = int(stride.get("dRow") or 0), int(stride.get("dCol") or 0)
        starts = []
        r, c = rect.r1, rect.c1
        while r <= rect.r2 and c <= rect.c2:
            starts.append(Cell(r, c))
            if dr == 0 and dc == 0:
                break
            r, c = r + dr, c + dc
    inner = opt.get("innerShape", "FORM")
    inner_opt = opt.get("innerOptions") or {}
    parts: list[ShapeResult] = []
    prev: list[Rect] = []
    for s0 in starts:
        brect = Rect(s0.row, s0.col, s0.row + bh - 1, s0.col + bw - 1)
        for p in prev:
            if p.intersect(brect):
                raise ShapeError("REPEATING_BLOCK_OVERLAP", f"重复块 {brect.a1()} 与 {p.a1()} 重叠")
        prev.append(brect)
        blk = slice_block(grid, brect, masks)
        if opt.get("skipEmptyBlocks", True) and all(is_blank(v) for v in blk.values.flat):
            continue
        parts.append(parse_form(blk, inner_opt) if inner == "FORM" else parse_kv(blk, {**inner_opt, "outputMode": "WIDE"}))
    headers: list[str] = []
    for part in parts:
        headers += [h for h in part.headers if h not in headers]
    result = ShapeResult([*headers, "_block"])
    for k, part in enumerate(parts, start=1):
        result.issues += part.issues
        for row, lin, meta in zip(part.rows, part.lineage, part.meta, strict=True):
            vals = dict(zip(part.headers, row, strict=True))
            result.add([vals.get(h) for h in headers] + [k], lin, meta["sheetRow"])
    return result


def suggest_shape(block: Block) -> str:
    vals = block.values
    h, w = vals.shape
    nonblank = [[not is_blank(v) for v in row] for row in vals]
    if h == 1:
        return "SUMMARY"
    if w == 2 and all(isinstance(vals[i, 0], str) for i in range(h) if nonblank[i][0]):
        return "KEY_VALUE"
    tl = norm_text(vals[0, 0])
    body_numeric = sum(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals[1:, 1:].flat)
    body_total = max(1, sum(1 for v in vals[1:, 1:].flat if not is_blank(v)))
    if ("\\" in tl or "/" in tl) and body_numeric / body_total > 0.8:
        return "MATRIX"
    return "DETAIL"
