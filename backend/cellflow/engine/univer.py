"""把归一化网格转换为 Univer 工作簿快照（TECH_DESIGN §2.3，D5）。

只输出只读预览需要的信息：值、合并单元格、隐藏行列、加粗；另附 CellFlow 自己的单元格标记（公式无缓存值、错误值）。
行列索引为 0-based（Univer 约定）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from cellflow.engine.grid import BOLD, ERROR, F_NOCACHE, HIDDEN_COL, HIDDEN_ROW, MERGED_FILLED, SheetGrid, is_blank

CELL_STRING, CELL_NUMBER, CELL_BOOLEAN = 1, 2, 3


def _cell(v: Any) -> dict | None:
    if is_blank(v):
        return None
    if isinstance(v, bool):
        return {"v": 1 if v else 0, "t": CELL_BOOLEAN}
    if isinstance(v, (int, float)):
        return {"v": v, "t": CELL_NUMBER}
    if isinstance(v, dt.datetime):
        text = v.strftime("%Y-%m-%d") if v.time() == dt.time(0) else v.strftime("%Y-%m-%d %H:%M:%S")
        return {"v": text, "t": CELL_STRING}
    if isinstance(v, (dt.date, dt.time)):
        return {"v": v.isoformat(), "t": CELL_STRING}
    return {"v": str(v), "t": CELL_STRING}


def sheet_snapshot(grid: SheetGrid, row_from: int = 1, row_to: int | None = None) -> dict:
    """返回单个 Sheet 的 Univer 数据；row_from/row_to 为 1-based 闭区间（分页加载）。"""
    row_to = min(row_to or grid.n_rows, grid.n_rows)
    cell_data: dict[int, dict[int, dict]] = {}
    marks: list[dict] = []
    for r in range(row_from, row_to + 1):
        row_cells: dict[int, dict] = {}
        for c in range(1, grid.n_cols + 1):
            f = grid.flag(r, c)
            v = grid.cell(r, c)
            if f & MERGED_FILLED:
                continue  # 合并区域只在左上角给值
            cell = _cell(v)
            if cell is None:
                continue
            if f & BOLD:
                cell["s"] = "bold"
            row_cells[c - 1] = cell
            if f & (F_NOCACHE | ERROR):
                marks.append({"row": r - 1, "col": c - 1, "kind": "NO_CACHE" if f & F_NOCACHE else "ERROR"})
        if row_cells:
            cell_data[r - 1] = row_cells
        for c in range(1, grid.n_cols + 1):
            f = grid.flag(r, c)
            if (f & F_NOCACHE) and grid.cell(r, c) is None:
                marks.append({"row": r - 1, "col": c - 1, "kind": "NO_CACHE"})
    row_data = {r - 1: {"hd": 1} for r in range(row_from, row_to + 1) if grid.flag(r, 1) & HIDDEN_ROW}
    col_data = {c - 1: {"hd": 1} for c in range(1, grid.n_cols + 1) if grid.flag(1, c) & HIDDEN_COL}
    merge_data = [
        {"startRow": r1 - 1, "endRow": r2 - 1, "startColumn": c1 - 1, "endColumn": c2 - 1}
        for (r1, c1, r2, c2) in grid.merged
        if r2 >= row_from and r1 <= row_to
    ]
    return {
        "id": grid.name,
        "name": grid.name,
        "rowCount": grid.n_rows,
        "columnCount": grid.n_cols,
        "cellData": cell_data,
        "rowData": row_data,
        "columnData": col_data,
        "mergeData": merge_data,
        "cfMarks": marks,
        "loadedRows": {"from": row_from, "to": row_to},
    }


def workbook_snapshot(name: str, grids: list[SheetGrid], rows_per_sheet: int = 500) -> dict:
    sheets = {g.name: sheet_snapshot(g, 1, rows_per_sheet) for g in grids}
    return {
        "id": name,
        "name": name,
        "sheetOrder": [g.name for g in grids],
        "styles": {"bold": {"bl": 1}},
        "sheets": sheets,
    }
