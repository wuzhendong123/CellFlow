"""区域定位与切片（TECH_DESIGN §4.2、§4.4、§7.3）。

所有坐标 1-based、闭区间，与 Excel 一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from cellflow.engine.grid import SheetGrid, is_blank, norm_text, to_a1


@dataclass(frozen=True)
class Rect:
    r1: int
    c1: int
    r2: int
    c2: int

    @property
    def height(self) -> int:
        return self.r2 - self.r1 + 1

    @property
    def width(self) -> int:
        return self.c2 - self.c1 + 1

    @property
    def empty(self) -> bool:
        return self.r1 > self.r2 or self.c1 > self.c2

    def a1(self) -> str:
        return f"{to_a1(self.r1, self.c1)}:{to_a1(self.r2, self.c2)}"

    def to_json(self) -> dict:
        return {"startRow": self.r1, "startCol": self.c1, "endRow": self.r2, "endCol": self.c2, "a1": self.a1()}

    @staticmethod
    def from_json(d: dict) -> Rect:
        return Rect(int(d["startRow"]), int(d["startCol"]), int(d["endRow"]), int(d["endCol"]))

    def intersect(self, o: Rect) -> Rect | None:
        r = Rect(max(self.r1, o.r1), max(self.c1, o.c1), min(self.r2, o.r2), min(self.c2, o.c2))
        return None if r.empty else r

    def contains(self, row: int, col: int) -> bool:
        return self.r1 <= row <= self.r2 and self.c1 <= col <= self.c2


class LocateError(Exception):
    """结构性定位错误（锚点找不到、区域为空坐标等），会让该源节点失败。"""

    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


@dataclass
class Cell:
    row: int
    col: int


@dataclass
class Block:
    """切片结果：局部网格 + 原点，用于回推单元格地址。"""

    sheet: str
    origin: Rect
    values: np.ndarray
    flags: np.ndarray
    date1904: bool = False
    warnings: list = field(default_factory=list)

    def addr(self, i: int, j: int) -> str:
        """局部 0-based → 'Sheet!D12'。"""
        return f"{self.sheet}!{to_a1(self.origin.r1 + i, self.origin.c1 + j)}"

    def sheet_row(self, i: int) -> int:
        return self.origin.r1 + i


def text_matches(value, pattern: str, mode: str) -> bool:
    if is_blank(value):
        return False
    t = norm_text(value)
    p = norm_text(pattern)
    if mode == "REGEX":
        return re.fullmatch(pattern, t) is not None
    if mode == "CONTAINS":
        return p in t
    if mode == "PREFIX":
        return t.startswith(p)
    return t == p  # EXACT（归一化后相等）


def find_cells(grid: SheetGrid, pattern: str, mode: str = "EXACT", scope: Rect | None = None) -> list[Cell]:
    r1, c1, r2, c2 = (scope.r1, scope.c1, scope.r2, scope.c2) if scope else (1, 1, grid.n_rows, grid.n_cols)
    hits = []
    for r in range(r1, min(r2, grid.n_rows) + 1):
        for c in range(c1, min(c2, grid.n_cols) + 1):
            if text_matches(grid.values[r - 1, c - 1], pattern, mode):
                hits.append(Cell(r, c))
    return hits


def similar_candidates(grid: SheetGrid, pattern: str, limit: int = 3) -> list[dict]:
    """锚点找不到时给出最相似的候选单元格（编辑距离）。"""
    target = norm_text(pattern)
    scored = []
    for r in range(1, grid.n_rows + 1):
        for c in range(1, grid.n_cols + 1):
            v = grid.values[r - 1, c - 1]
            if isinstance(v, str) and not is_blank(v):
                t = norm_text(v)
                if len(t) <= len(target) * 3 + 10:
                    scored.append((_edit_distance(target, t), to_a1(r, c), t))
    scored.sort()
    return [{"cell": a, "text": t, "distance": d} for d, a, t in scored[:limit]]


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def row_blank(grid: SheetGrid, r: int, c1: int, c2: int) -> bool:
    if r > grid.n_rows:
        return True
    return all(is_blank(grid.values[r - 1, c - 1]) for c in range(c1, min(c2, grid.n_cols) + 1))


def col_blank(grid: SheetGrid, c: int, r1: int, r2: int) -> bool:
    if c > grid.n_cols:
        return True
    return all(is_blank(grid.values[r - 1, c - 1]) for r in range(r1, min(r2, grid.n_rows) + 1))


def last_non_blank_row(grid: SheetGrid, c1: int = 1, c2: int | None = None) -> int:
    c2 = c2 or grid.n_cols
    for r in range(grid.n_rows, 0, -1):
        if not row_blank(grid, r, c1, c2):
            return r
    return 0


def _scan_blank_rows(grid: SheetGrid, r1: int, c1: int, c2: int, n: int) -> int:
    """从 r1 下一行开始向下，直到连续 n 个空行；返回最后一个非空行。"""
    last, blanks = r1, 0
    for r in range(r1 + 1, grid.n_rows + 2):
        if row_blank(grid, r, c1, c2):
            blanks += 1
            if blanks >= n:
                break
        else:
            blanks, last = 0, r
    return last


def _scan_blank_header(grid: SheetGrid, r1: int, c1: int) -> int:
    c2 = c1
    while c2 + 1 <= grid.n_cols and not is_blank(grid.values[r1 - 1, c2]):
        c2 += 1
    return c2


def _nearest(hits: list[Cell], design: Rect | None) -> Cell:
    if design is None:
        return hits[0]
    return min(hits, key=lambda h: (abs(h.row - design.r1) + abs(h.col - design.c1), h.row, h.col))


def resolve_rect(grid: SheetGrid, locator: dict, design: Rect | None) -> tuple[Rect, list[dict]]:
    """按定位器在本次文件上计算区域范围。返回 (范围, 警告列表)。"""
    t = locator.get("type", "FIXED")
    warns: list[dict] = []
    if t == "FIXED":
        if design is None:
            raise LocateError("REGION_NO_RANGE", "固定坐标区域缺少设计范围")
        return Rect(design.r1, design.c1, design.r2, design.c2), warns

    if t == "ANCHOR":
        start = locator["start"]
        scope = Rect.from_json(locator["searchScope"]) if locator.get("searchScope") else None
        hits = find_cells(grid, start["text"], start.get("match", "EXACT"), scope)
        if not hits:
            raise LocateError(
                "ANCHOR_NOT_FOUND",
                f"找不到锚点「{start['text']}」",
                {"anchor": start["text"], "candidates": similar_candidates(grid, start["text"])},
            )
        hit = _nearest(hits, design)
        if len(hits) > 1:
            warns.append(
                {
                    "code": "ANCHOR_AMBIGUOUS",
                    "message": f"锚点「{start['text']}」出现 {len(hits)} 次，已取离设计位置最近的 {to_a1(hit.row, hit.col)}",
                }
            )
        off = start.get("offset") or {}
        r1, c1 = hit.row + int(off.get("row", 0)), hit.col + int(off.get("col", 0))
        end = locator.get("end") or {}
        c2 = _resolve_end_col(grid, r1, c1, end.get("cols") or {"mode": "UNTIL_BLANK_HEADER"}, design)
        r2 = _resolve_end_row(grid, r1, c1, c2, end.get("rows") or {"mode": "UNTIL_BLANK_ROWS", "n": 1}, design)
        return Rect(r1, c1, r2, c2), warns

    if t == "AUTO_EXPAND":
        if design is None:
            raise LocateError("REGION_NO_RANGE", "自动扩展区域缺少设计起点")
        r1, c1 = design.r1, design.c1
        c2 = _scan_blank_header(grid, r1, c1)
        r2 = _scan_blank_rows(grid, r1, c1, c2, int(locator.get("blankRowsToStop", 1)))
        return Rect(r1, c1, r2, c2), warns

    raise LocateError("LOCATOR_UNSUPPORTED", f"不支持的定位方式 {t}")


def _resolve_end_row(grid: SheetGrid, r1: int, c1: int, c2: int, rule: dict, design: Rect | None) -> int:
    mode = rule.get("mode", "UNTIL_BLANK_ROWS")
    if mode == "FIXED_SIZE":
        n = int(rule.get("n") or (design.height if design else 1))
        return r1 + n - 1
    if mode == "SHEET_END":
        return max(last_non_blank_row(grid, c1, c2), r1)
    if mode == "UNTIL_BLANK_ROWS":
        return _scan_blank_rows(grid, r1, c1, c2, int(rule.get("n", 1)))
    if mode == "UNTIL_ANCHOR":
        scope = Rect(r1 + 1, c1, grid.n_rows, c2 if rule.get("anyColumn") else c1)
        hits = find_cells(grid, rule["text"], rule.get("match", "EXACT"), scope)
        if not hits:
            raise LocateError("END_ANCHOR_NOT_FOUND", f"找不到终点锚点「{rule['text']}」", {"anchor": rule["text"]})
        first = min(hits, key=lambda h: h.row)
        return first.row if rule.get("inclusive") else first.row - 1
    raise LocateError("LOCATOR_UNSUPPORTED", f"不支持的终点方式 {mode}")


def _resolve_end_col(grid: SheetGrid, r1: int, c1: int, rule: dict, design: Rect | None) -> int:
    mode = rule.get("mode", "UNTIL_BLANK_HEADER")
    if mode == "FIXED_SIZE":
        n = int(rule.get("n") or (design.width if design else 1))
        return c1 + n - 1
    if mode == "UNTIL_BLANK_HEADER":
        return _scan_blank_header(grid, r1, c1)
    if mode == "SHEET_END":
        return grid.n_cols
    raise LocateError("LOCATOR_UNSUPPORTED", f"不支持的列终点方式 {mode}")


def slice_block(grid: SheetGrid, rect: Rect, masks: list[Rect] | None = None) -> Block:
    """按物理坐标切出区域，并把屏蔽区 / excludeFrom 区域挖空。越出表格的部分以空值补齐。"""
    if rect.empty:
        raise LocateError("EMPTY_REGION", f"区域范围无效：{rect}")
    h, w = rect.height, rect.width
    vals = np.empty((h, w), dtype=object)
    flg = np.zeros((h, w), dtype=np.uint16)
    rr2, cc2 = min(rect.r2, grid.n_rows), min(rect.c2, grid.n_cols)
    if rect.r1 <= rr2 and rect.c1 <= cc2:
        vals[: rr2 - rect.r1 + 1, : cc2 - rect.c1 + 1] = grid.values[rect.r1 - 1 : rr2, rect.c1 - 1 : cc2]
        flg[: rr2 - rect.r1 + 1, : cc2 - rect.c1 + 1] = grid.flags[rect.r1 - 1 : rr2, rect.c1 - 1 : cc2]
    for m in masks or []:
        ov = rect.intersect(m)
        if ov:
            vals[ov.r1 - rect.r1 : ov.r2 - rect.r1 + 1, ov.c1 - rect.c1 : ov.c2 - rect.c1 + 1] = None
    return Block(grid.name, rect, vals, flg, grid.date1904)


def check_overlaps(rects: dict[str, Rect], regions: list[dict]) -> list[dict]:
    """同一 Sheet 内区域默认不允许重叠；声明了 excludeFrom、屏蔽区、键值区之间的重叠除外（§4.4）。"""
    by_id = {r["regionId"]: r for r in regions}
    issues = []
    ids = [i for i in rects if by_id[i].get("shape") != "IGNORE"]
    for a_i, a in enumerate(ids):
        for b in ids[a_i + 1 :]:
            if not rects[a].intersect(rects[b]):
                continue
            ra, rb = by_id[a], by_id[b]
            if b in ra.get("excludeFrom", []) or a in rb.get("excludeFrom", []):
                continue
            if ra.get("shape") == "KEY_VALUE" and rb.get("shape") == "KEY_VALUE":
                continue
            issues.append(
                {
                    "code": "REGION_OVERLAP",
                    "message": f"区域「{ra.get('name', a)}」与「{rb.get('name', b)}」重叠且未声明排除",
                    "regions": [a, b],
                }
            )
    return issues


def suggest_locator(grid: SheetGrid, sel: Rect) -> dict:
    """框选后推荐定位方式（§4.2 用户交互）。"""
    top_left = grid.cell(sel.r1, sel.c1)
    below_blank = row_blank(grid, sel.r2 + 1, sel.c1, sel.c2)
    if isinstance(top_left, str) and not is_blank(top_left):
        hits = find_cells(grid, top_left, "EXACT")
        if len(hits) == 1:
            rows = {"mode": "UNTIL_BLANK_ROWS", "n": 1} if below_blank else {"mode": "FIXED_SIZE", "n": sel.height}
            nxt = grid.cell(sel.r2 + 1, sel.c1)
            if not below_blank and isinstance(nxt, str) and not is_blank(nxt):
                rows = {"mode": "UNTIL_ANCHOR", "text": norm_text(nxt), "inclusive": False}
            cols = (
                {"mode": "UNTIL_BLANK_HEADER"}
                if _scan_blank_header(grid, sel.r1, sel.c1) == sel.c2
                else {"mode": "FIXED_SIZE", "n": sel.width}
            )
            return {
                "type": "ANCHOR",
                "start": {"text": norm_text(top_left), "match": "EXACT", "offset": {"row": 0, "col": 0}},
                "end": {"rows": rows, "cols": cols},
            }
    if below_blank:
        return {"type": "AUTO_EXPAND", "blankRowsToStop": 1}
    return {"type": "FIXED"}
