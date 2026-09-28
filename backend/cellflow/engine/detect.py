"""区域推荐：单个选区的形态 / 定位 / 字段推荐，以及整张 Sheet 的自动识别（识别结果供用户在圈选界面逐个调整）。"""

from __future__ import annotations

import datetime as dt
import re

import numpy as np

from cellflow.engine.grid import SheetGrid, is_blank, norm_text
from cellflow.engine.locate import Rect, slice_block, suggest_locator
from cellflow.engine.shapes import parse_detail, suggest_shape

MAX_REGIONS = 30


def field_name(source: str, i: int, used: set[str], prefix: str = "col") -> str:
    """由表头推荐字段名：以英文为主的表头转驼峰（Credit Support 信用支持 → creditSupport），否则 col1 / key1。"""
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*|[0-9]+", source or "")
    letters = sum(len(w) for w in words if w[0].isalpha())
    name = ""
    if letters >= 4:
        parts = [w for w in words if w[0].isalpha()] + [w for w in words if w[0].isdigit()]  # 数字不打头
        name = (parts[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in parts[1:]))[:40]
    if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        name = f"{prefix}{i + 1}"
    base, k = name, 2
    while name in used:
        name = f"{base}_{k}"
        k += 1
    used.add(name)
    return name


def infer_type(values) -> str:
    """按样例值推荐类型；有文本或混杂时为 string。"""
    vals = [v for v in values if not is_blank(v)]
    if not vals:
        return "string"
    if all(isinstance(v, bool) for v in vals):
        return "bool"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        if all(float(v).is_integer() and abs(v) < 1e15 for v in vals):
            return "int" if all(abs(v) < 2**31 for v in vals) else "long"
        return "float"
    if all(isinstance(v, dt.datetime) for v in vals):
        return "date" if all(v.hour == v.minute == v.second == 0 for v in vals) else "datetime"
    if all(isinstance(v, dt.date) for v in vals):
        return "date"
    return "string"


def suggest_region(g: SheetGrid, rect: Rect) -> dict:
    """对一个矩形给出形态、定位方式与字段（字段名、类型）的推荐。"""
    block = slice_block(g, rect)
    shape = suggest_shape(block)
    loc = suggest_locator(g, rect)
    columns = []
    used: set[str] = set()
    vals = block.values
    if shape in ("DETAIL", "SUMMARY"):
        hdr = parse_detail(slice_block(g, Rect(rect.r1, rect.c1, rect.r1, rect.c2)), {"headerRows": 1})
        for i, h in enumerate(hdr.headers):
            sample = vals[1:51, i] if shape == "DETAIL" and i < vals.shape[1] else []
            columns.append({"source": h, "field": field_name(h, i, used), "type": infer_type(sample)})
    elif shape == "KEY_VALUE":
        for i in range(vals.shape[0]):
            key = norm_text(vals[i, 0])
            if key:
                v = vals[i, 1] if vals.shape[1] > 1 else None
                columns.append({"source": key, "field": field_name(key, len(columns), used, "key"), "type": infer_type([v])})
    return {"shape": shape, "locator": loc, "columns": columns, "range": rect.to_json()}


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """连续为 True 的区间（0-based，含两端）。"""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def _title_of(g: SheetGrid, mask: np.ndarray, r: int, c1: int, c2: int) -> str | None:
    """整行只有一个文本（合并单元格填充后可能重复多格）→ 视为下方表格的标题行。"""
    vals = {norm_text(g.values[r, c]) for c in range(c1, c2 + 1) if mask[r, c]}
    if len(vals) == 1:
        v = next(iter(vals))
        if v and not v.replace(".", "", 1).replace("-", "", 1).isdigit():
            return v
    return None


def detect_regions(g: SheetGrid) -> list[dict]:
    """按空行、空列把 Sheet 切成若干块；块首的单独文本行作为标题（区域名称）；每块给出推荐。"""
    mask = np.vectorize(lambda v: not is_blank(v), otypes=[bool])(g.values) if g.values.size else np.zeros((0, 0), bool)
    out: list[dict] = []
    for a, b in _runs(mask.any(axis=1)):
        for c1, c2 in _runs(mask[a:b + 1].any(axis=0)):
            r1, r2 = a, b
            rows = [r for r in range(r1, r2 + 1) if mask[r, c1:c2 + 1].any()]
            r1, r2 = rows[0], rows[-1]
            title = None
            while r1 < r2:
                t = _title_of(g, mask, r1, c1, c2)
                if not t:
                    break
                title = t
                r1 += 1
            if r1 == r2 and c1 == c2:
                continue  # 孤立的单元格（多为说明文字或标题），不单独成区
            rect = Rect(r1 + 1, c1 + 1, r2 + 1, c2 + 1)
            sug = suggest_region(g, rect)
            first = next((norm_text(g.values[r1, c]) for c in range(c1, c2 + 1) if mask[r1, c]), "")
            sug["name"] = (title or first or f"区域{len(out) + 1}")[:40]
            sug["cells"] = int(mask[r1:r2 + 1, c1:c2 + 1].sum())
            out.append(sug)
            if len(out) >= MAX_REGIONS:
                return out
    return out
