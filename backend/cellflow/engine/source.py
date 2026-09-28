"""Excel 源节点：定位 → 切片 → 形态解析 → 字段清洗（TECH_DESIGN §7.9 ExcelSourceNode、§7.7.2）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cellflow.engine.columns import bind_and_coerce
from cellflow.engine.dataset import Dataset
from cellflow.engine.grid import SheetGrid, Workbook, WorkbookError, from_a1
from cellflow.engine.locate import LocateError, Rect, check_overlaps, resolve_rect, slice_block
from cellflow.engine.shapes import (
    RowPolicy,
    ShapeResult,
    parse_detail,
    parse_form,
    parse_grouped,
    parse_kv,
    parse_matrix,
    parse_repeating,
    parse_summary,
)

SHAPES = {
    "DETAIL": parse_detail,
    "KEY_VALUE": parse_kv,
    "MATRIX": parse_matrix,
    "GROUPED_DETAIL": parse_grouped,
    "FORM": parse_form,
}


@dataclass
class SourceResult:
    outputs: dict[str, Dataset] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    locate_report: list[dict] = field(default_factory=list)
    failed_ports: set[str] = field(default_factory=set)


def _issue(node_id: str, region: dict | None, d: dict) -> dict:
    out = {"node": node_id, "severity": d.get("severity", "ERROR"), "code": d["code"], "message": d["message"]}
    if region:
        out["region"] = region.get("regionId")
    for k in ("cell", "field", "value", "rule", "related"):
        if d.get(k) is not None:
            out[k] = d[k]
    return out


def match_sheets(wb: Workbook, rule: dict) -> list[str]:
    mode, value = rule.get("match", "EXACT"), rule.get("value", "")
    names = wb.sheet_names
    if mode == "REGEX":
        return [n for n in names if re.fullmatch(value, n)]
    if mode == "INDEX":
        i = int(value)
        return [names[i]] if 0 <= i < len(names) else []
    return [n for n in names if n == value]


def run_source(cfg: dict, wb: Workbook, node_id: str) -> SourceResult:
    rule = cfg.get("sheet") or {}
    names = match_sheets(wb, rule)
    res = SourceResult()
    ports = [r["outputPortId"] for r in cfg.get("regions", []) if r.get("shape") != "IGNORE"]
    if not names:
        res.issues.append(_issue(node_id, None, {
            "code": "SHEET_NOT_FOUND",
            "message": f"按规则「{rule.get('value')}」找不到 Sheet，现有：{', '.join(wb.sheet_names)}",
        }))
        res.failed_ports.update(ports)
        return res
    if rule.get("match") != "REGEX":
        return _run_sheet(cfg, wb, names[0], node_id)
    # 多 Sheet 同构合并（D15）：同一套区域逐 Sheet 执行后按字段名合并，并追加来源 Sheet 字段
    as_col = rule.get("asColumn") or "_sheet"
    per_port: dict[str, list[Dataset]] = {}
    for n in names:
        part = _run_sheet(cfg, wb, n, node_id)
        res.issues += [dict(i, sheet=n) for i in part.issues]
        res.locate_report += part.locate_report
        res.failed_ports |= part.failed_ports
        for port, ds in part.outputs.items():
            if as_col not in ds.fields:
                from cellflow.engine.dataset import ColumnSchema

                ds.columns.append(ColumnSchema(as_col, "string", origin="sheet"))
            for r, lin in zip(ds.rows, ds.lineage, strict=True):
                r[as_col] = n
                lin[as_col] = None
            per_port.setdefault(port, []).append(ds)
    for port, parts in per_port.items():
        if port in res.failed_ports:
            continue
        res.outputs[port] = union_by_name(parts)
    return res


def union_by_name(parts: list[Dataset]) -> Dataset:
    cols = []
    seen = set()
    for p in parts:
        for c in p.columns:
            if c.field not in seen:
                seen.add(c.field)
                cols.append(c)
    out = Dataset(cols)
    for p in parts:
        for r, lin, m in zip(p.rows, p.lineage, p.meta, strict=True):
            out.rows.append({c.field: r.get(c.field) for c in cols})
            out.lineage.append(lin)
            out.meta.append(m)
    return out


def _run_sheet(cfg: dict, wb: Workbook, sheet_name: str, node_id: str) -> SourceResult:
    res = SourceResult()
    lo = cfg.get("loaderOptions") or {}
    regions: list[dict] = cfg.get("regions", [])
    try:
        grid = wb.sheet(sheet_name, lo.get("mergePolicy", "FILL"))
    except WorkbookError as e:
        res.issues.append(_issue(node_id, None, {"code": e.code, "message": e.message}))
        res.failed_ports.update(r["outputPortId"] for r in regions if r.get("shape") != "IGNORE")
        return res
    policy = RowPolicy(lo.get("hiddenRows", "KEEP_WARN"), lo.get("strikethroughRows", "KEEP"))
    rects: dict[str, Rect] = {}
    for r in regions:
        design = Rect.from_json(r["designRange"]) if r.get("designRange") else None
        try:
            rect, warns = resolve_rect(grid, r.get("locator") or {"type": "FIXED"}, design)
        except LocateError as e:
            res.issues.append(_issue(node_id, r, {"code": e.code, "message": f"区域「{r.get('name')}」：{e.message}",
                                                  **({"related": e.data.get("candidates")} if e.data.get("candidates") else {})}))
            if r.get("shape") != "IGNORE":
                res.failed_ports.add(r["outputPortId"])
            continue
        rects[r["regionId"]] = rect
        for w in warns:
            res.issues.append(_issue(node_id, r, {"severity": "WARN", **w}))
        report = {"regionId": r["regionId"], "name": r.get("name"), "sheet": sheet_name, "range": rect.to_json()}
        if design:
            report["designRange"] = design.to_json()
            report["offset"] = {"rows": rect.r1 - design.r1, "cols": rect.c1 - design.c1,
                                "heightChange": rect.height - design.height}
            if design.height and abs(rect.height - design.height) > 0.5 * design.height and r.get("locator", {}).get("type") != "FIXED":
                res.issues.append(_issue(node_id, r, {"severity": "WARN", "code": "REGION_SIZE_CHANGED",
                                                      "message": f"区域「{r.get('name')}」行数由 {design.height} 变为 {rect.height}"}))
        res.locate_report.append(report)
    for ov in check_overlaps(rects, [r for r in regions if r["regionId"] in rects]):
        res.issues.append(_issue(node_id, None, {"code": ov["code"], "message": ov["message"]}))
        for rid in ov["regions"]:
            port = next(r["outputPortId"] for r in regions if r["regionId"] == rid)
            res.failed_ports.add(port)
    header_cache: dict[str, list[str]] = {}
    for r in regions:
        shape = r.get("shape")
        if shape == "IGNORE" or r["regionId"] not in rects or r["outputPortId"] in res.failed_ports:
            continue
        rect = rects[r["regionId"]]
        masks = [rects[x["regionId"]] for x in regions if x["regionId"] in rects and x["regionId"] != r["regionId"]
                 and (x.get("shape") == "IGNORE" or x["regionId"] in (r.get("excludeFrom") or []))]
        opt = r.get("shapeOptions") or {}
        try:
            if shape == "REPEATING_BLOCK":
                sr = parse_repeating(grid, rect, opt, masks)
            else:
                block = slice_block(grid, rect, masks)
                if shape == "SUMMARY":
                    align = opt.get("alignWith")
                    hdrs = _aligned_headers(grid, regions, rects, align, rect, header_cache) if align else None
                    sr = parse_summary(block, opt, policy, hdrs)
                elif shape in SHAPES:
                    sr = SHAPES[shape](block, opt, policy)
                else:
                    raise LocateError("SHAPE_UNSUPPORTED", f"不支持的形态 {shape}")
        except LocateError as e:
            res.issues.append(_issue(node_id, r, {"code": e.code, "message": f"区域「{r.get('name')}」：{e.message}"}))
            res.failed_ports.add(r["outputPortId"])
            continue
        ds, issues, _stats = bind_and_coerce(
            sr, r.get("columns") or _default_specs(sr), node_id=node_id, region_id=r["regionId"],
            date1904=grid.date1904, cell_flag=_flag_lookup(grid), new_columns=r.get("newColumns", "DROP"),
        )
        res.issues += [_issue(node_id, r, i) for i in issues]
        res.outputs[r["outputPortId"]] = ds
    return res


def _default_specs(sr: ShapeResult) -> list[dict]:
    """未配置字段时按表头生成（仅用于预览与推荐）。"""
    specs = []
    for i, h in enumerate(sr.headers):
        if h == "_block":
            continue
        specs.append({"source": h, "field": f"col{i + 1}", "type": "string"})
    return specs


def _flag_lookup(grid: SheetGrid):
    def f(addr: str) -> int:
        sheet, _, cell = addr.rpartition("!")
        if sheet and sheet != grid.name:
            return 0
        r, c = from_a1(cell)
        return grid.flag(r, c)

    return f


def _aligned_headers(grid, regions, rects, align_id, rect, cache) -> list[str] | None:
    """汇总行按对齐区域的表头命名各列（§4.3 SUMMARY alignWith）。"""
    if align_id not in rects:
        return None
    other = next((x for x in regions if x["regionId"] == align_id), None)
    if other is None:
        return None
    if align_id not in cache:
        k = int((other.get("shapeOptions") or {}).get("headerRows", 1))
        orect = rects[align_id]
        blk = slice_block(grid, Rect(orect.r1, orect.c1, orect.r1 + k - 1, orect.c2))
        from cellflow.engine.shapes import _header_names, _View

        cache[align_id], _ = _header_names(_View(blk, "ROW"), k, ".")
        cache[align_id + "@c1"] = [orect.c1]
    hdrs = cache[align_id]
    start = rect.c1 - cache[align_id + "@c1"][0]
    return hdrs[start:] if start >= 0 else None


def preview_region(wb: Workbook, sheet: str, region: dict, limit: int = 50) -> dict:
    """单区域预览（分屏配置时使用）。"""
    cfg = {"sheet": {"match": "EXACT", "value": sheet}, "regions": [region]}
    r = _run_sheet(cfg, wb, sheet, "preview")
    ds = r.outputs.get(region["outputPortId"])
    return {
        "issues": r.issues,
        "locateReport": r.locate_report,
        "output": ds.page(0, limit) if ds else None,
    }
