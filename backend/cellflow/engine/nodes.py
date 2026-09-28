"""画布节点：端口定义、设计期 Schema 推导（infer）、运行期执行（run）。

TECH_DESIGN §5.4（变换）、§5.5（派生列）、§8（关联）、§9（校验）、§6.1（输出）。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from cellflow.engine.dataset import ColumnSchema, Dataset
from cellflow.engine.expr import ExprError, FieldInfo, compatible, compile_expr, suggest_cast_fix
from cellflow.engine.grid import norm_text
from cellflow.engine.types import FIELD_NAME, RESERVED_FIELDS, parse_type


class FatalNodeError(Exception):
    """结构性错误：该节点失败，其下游跳过，其他分支继续（§7.9）。"""

    def __init__(self, code: str, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass
class PortSchema:
    columns: list[ColumnSchema]
    single_row: bool = False

    def field_infos(self) -> dict[str, FieldInfo]:
        return {c.field: FieldInfo(c.type) for c in self.columns}

    def get(self, name: str) -> ColumnSchema | None:
        return next((c for c in self.columns if c.field == name), None)

    def to_json(self) -> dict:
        return {"columns": [c.to_json() for c in self.columns], "singleRow": self.single_row}


@dataclass
class InferResult:
    outputs: dict[str, PortSchema] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


@dataclass
class RunContext:
    node_id: str
    settings: dict
    issues: list[dict] = field(default_factory=list)

    def issue(self, severity: str, code: str, message: str, **kw) -> None:
        d = {"node": self.node_id, "severity": severity, "code": code, "message": message}
        d.update({k: v for k, v in kw.items() if v is not None})
        self.issues.append(d)


def _err(code: str, message: str, **kw) -> dict:
    return {"code": code, "message": message, **kw}


def key_norm(v: Any) -> Any:
    """关联/外键比较用的键归一化：数字与数字字符串视为相等（真实样例中账户号在不同 Sheet 里类型不同）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (int, float)):
        return str(v)
    s = norm_text(v)
    return s


# ======================= 端口定义 =======================
def input_ports(node: dict) -> dict[str, dict]:
    """返回 {portId: {kind: DATA|PARAM|REF, optional, multi}}。"""
    t = node["type"]
    if t == "EXCEL_SOURCE":
        return {}
    if t in ("FILTER", "DERIVE"):
        return {"in": {"kind": "DATA"}, "in_params": {"kind": "PARAM", "optional": True}}
    if t in ("SELECT_RENAME", "SINK", "PIVOT"):
        return {"in": {"kind": "DATA"}}
    if t == "UNION":
        return {"in": {"kind": "DATA", "multi": True}}
    if t == "LOOKUP":
        return {"in": {"kind": "DATA"}, "in_dict": {"kind": "DATA", "reference": True}}
    if t == "JOIN":
        return {"in_left": {"kind": "DATA"}, "in_right": {"kind": "DATA", "reference": True}}
    if t == "VALIDATOR":
        ports = {"in_main": {"kind": "DATA"}, "in_params": {"kind": "PARAM", "optional": True}}
        for p in (node.get("config") or {}).get("refs", {}).values():
            ports[p] = {"kind": "REF", "optional": False, "reference": True}
        return ports
    return {}


def output_ports(node: dict) -> dict[str, dict]:
    t = node["type"]
    cfg = node.get("config") or {}
    if t == "EXCEL_SOURCE":
        return {r["outputPortId"]: {} for r in cfg.get("regions", []) if r.get("shape") != "IGNORE"}
    if t == "DERIVE":
        return {"out": {}, "out_reject": {"side": True}}
    if t in ("FILTER", "SELECT_RENAME", "UNION", "LOOKUP", "PIVOT"):
        return {"out": {}}
    if t == "JOIN":
        return {"out_main": {}, "out_unmatched": {"side": True}}
    if t == "VALIDATOR":
        return {"out_pass": {}, "out_reject": {"side": True}}
    return {}


# ======================= EXCEL_SOURCE =======================
SINGLE_ROW_SHAPES = {"SUMMARY", "FORM"}


def infer_source(node: dict, inputs: dict) -> InferResult:
    from cellflow.engine.columns import schema_type, validate_column_specs

    res = InferResult()
    cfg = node.get("config") or {}
    if "file" in cfg or "fileId" in cfg:
        res.errors.append(_err("DSL_INVALID", "源节点不能配置文件，样例文件归属方案（D23）"))
    ids = set()
    for r in cfg.get("regions", []):
        if r.get("shape") == "IGNORE":
            continue
        port = r.get("outputPortId")
        if not port or port in ids:
            res.errors.append(_err("DSL_INVALID", f"区域「{r.get('name')}」的输出端口缺失或重复"))
            continue
        ids.add(port)
        for e in validate_column_specs(r.get("columns") or []):
            res.errors.append(_err("DSL_INVALID", f"区域「{r.get('name')}」：{e}"))
        cols = [ColumnSchema(c["field"], schema_type(c), bool(c.get("isKey")), origin=c.get("source"))
                for c in r.get("columns") or []]
        if (cfg.get("sheet") or {}).get("match") == "REGEX":
            cols.append(ColumnSchema((cfg["sheet"].get("asColumn") or "_sheet"), "string", origin="sheet"))
        if r.get("shape") == "REPEATING_BLOCK":
            cols.append(ColumnSchema("_block", "int", origin="_block"))
        shape = r.get("shape")
        opt = r.get("shapeOptions") or {}
        single = shape in SINGLE_ROW_SHAPES or (shape == "KEY_VALUE" and opt.get("outputMode", "WIDE") == "WIDE")
        if (cfg.get("sheet") or {}).get("match") == "REGEX":
            single = False
        res.outputs[port] = PortSchema(cols, single)
    return res


# ======================= FILTER =======================
def _param_infos(cfg: dict, inputs: dict[str, PortSchema]) -> dict[str, dict[str, FieldInfo]]:
    out = {}
    for alias, port in (cfg.get("params") or {}).items():
        if port in inputs:
            out[alias] = inputs[port].field_infos()
    return out


def _check_expr(expr: str, fields, params, res: InferResult, what: str, expect: str | None = None):
    try:
        p = compile_expr(expr, fields, params)
    except ExprError as e:
        fix = suggest_cast_fix(expr, fields, params) if e.hint else None
        res.errors.append(_err("EXPR_INVALID", f"{what}：{e.message}", hint=e.hint, fix=fix))
        return None
    if expect == "bool" and p.result_type not in ("bool", "null"):
        res.errors.append(_err("EXPR_INVALID", f"{what}：结果应为真/假，实际为 {p.result_type}"))
    return p


def infer_filter(node, inputs):
    res = InferResult()
    cfg = node.get("config") or {}
    src = inputs.get("in")
    if src is None:
        return res
    _check_expr(cfg.get("expr", ""), src.field_infos(), _param_infos(cfg, inputs), res, "过滤条件", "bool")
    res.outputs["out"] = PortSchema(list(src.columns), src.single_row)
    return res


def _load_params(cfg: dict, inputs: dict[str, Dataset]) -> tuple[dict, dict]:
    params, lin = {}, {}
    for alias, port in (cfg.get("params") or {}).items():
        ds = inputs.get(port)
        if ds is None:
            continue
        if len(ds) != 1:
            raise FatalNodeError("PARAM_NOT_SINGLE_ROW", f"参数「{alias}」的输入必须恰好 1 行，实际 {len(ds)} 行")
        params[alias] = ds.rows[0]
        lin[alias] = ds.lineage[0]
    return params, lin


def _param_schema(cfg: dict, inputs: dict[str, Dataset]) -> dict:
    return {a: {c.field: FieldInfo(c.type) for c in inputs[p].columns} for a, p in (cfg.get("params") or {}).items()
            if p in inputs}


def run_filter(node, inputs, ctx: RunContext):
    cfg = node.get("config") or {}
    ds: Dataset = inputs["in"]
    params, _ = _load_params(cfg, inputs)
    prog = compile_expr(cfg["expr"], {c.field: FieldInfo(c.type) for c in ds.columns}, _param_schema(cfg, inputs))
    keep = []
    for i, row in enumerate(ds.rows):
        try:
            v = prog.evaluate(row, params, ds.meta[i])
        except ExprError as e:
            cells = ds.cells_of(i, prog.referenced_fields)
            ctx.issue("ERROR", "EXPR_EVAL_FAILED", f"过滤条件求值失败：{e.message}", cell=cells[0] if cells else None,
                      rid=ds.meta[i].get("rid"), related=cells)
            continue
        if v is True:
            keep.append(i)
    return {"out": ds.take(keep)}


# ======================= DERIVE =======================
def infer_derive(node, inputs):
    res = InferResult()
    cfg = node.get("config") or {}
    src = inputs.get("in")
    if src is None:
        return res
    cols = [ColumnSchema(c.field, c.type, c.is_key, c.origin) for c in src.columns]
    fields = {c.field: FieldInfo(c.type) for c in cols}
    params = _param_infos(cfg, inputs)
    later = [c.get("field") for c in cfg.get("columns", [])]
    for i, col in enumerate(cfg.get("columns", [])):
        f = col.get("field", "")
        if not FIELD_NAME.match(f) or f in RESERVED_FIELDS:
            res.errors.append(_err("DSL_INVALID", f"派生列名「{f}」不合法或是保留字"))
            continue
        mode = col.get("mode", "ADD")
        exists = f in fields
        if mode == "ADD" and exists:
            res.errors.append(_err("DSL_INVALID", f"派生列「{f}」与已有字段同名，如需覆盖请选择「覆盖」"))
            continue
        if mode == "REPLACE" and not exists:
            res.errors.append(_err("DSL_INVALID", f"要覆盖的字段「{f}」不存在"))
            continue
        body = col.get("expr", "")
        for other in later[i:]:
            if other and other not in fields and re.search(rf"(?<![\w.]){re.escape(other)}\b", body):
                res.errors.append(_err("DSL_INVALID", f"派生列「{f}」引用了自身或排在后面的列「{other}」"))
                break
        else:
            p = _check_expr(body, fields, params, res, f"派生列「{f}」")
            declared = col.get("type", "string")
            try:
                parse_type(declared)
            except ValueError as e:
                res.errors.append(_err("DSL_INVALID", str(e)))
                continue
            if p and not compatible(p.result_type, declared):
                res.errors.append(_err("EXPR_INVALID", f"派生列「{f}」表达式结果为 {p.result_type}，与声明类型 {declared} 不兼容"))
        if mode == "REPLACE":
            for c in cols:
                if c.field == f:
                    c.type = col.get("type", c.type)
        else:
            cols.append(ColumnSchema(f, col.get("type", "string"), origin="derive"))
        fields[f] = FieldInfo(col.get("type", "string"))
    res.outputs["out"] = PortSchema(cols, src.single_row)
    res.outputs["out_reject"] = PortSchema(cols, False)
    return res


def _cast(v: Any, t: str) -> Any:
    if v is None:
        return None
    name = parse_type(t).name
    if name in ("int", "long"):
        if isinstance(v, float):
            if not v.is_integer():
                raise ExprError(f"结果 {v} 不是整数")
            return int(v)
        return int(v)
    if name == "float":
        return float(v)
    if name == "decimal":
        from decimal import ROUND_HALF_UP, Decimal

        pt = parse_type(t)
        return Decimal(str(v)).quantize(Decimal(1).scaleb(-(pt.scale or 0)), rounding=ROUND_HALF_UP)
    if name == "string":
        return v if isinstance(v, str) else str(v)
    if name == "date" and hasattr(v, "date"):
        return v.date()
    return v


def run_derive(node, inputs, ctx: RunContext):
    cfg = node.get("config") or {}
    ds: Dataset = inputs["in"]
    params, p_lin = _load_params(cfg, inputs)
    pschema = _param_schema(cfg, inputs)
    out_cols = [ColumnSchema(c.field, c.type, c.is_key, c.origin) for c in ds.columns]
    rows = [dict(r) for r in ds.rows]
    lin = [dict(x) for x in ds.lineage]
    bad: set[int] = set()
    on_error = cfg.get("onError", "ERROR")
    for col in cfg.get("columns", []):
        f, t = col["field"], col.get("type", "string")
        fields = {c.field: FieldInfo(c.type) for c in out_cols}
        prog = compile_expr(col["expr"], fields, pschema)
        param_cells = [p_lin.get(a, {}).get(pf) for a, pf in prog.referenced_params]
        for i, row in enumerate(rows):
            if i in bad:
                continue
            try:
                row[f] = _cast(prog.evaluate(row, params, ds.meta[i]), t)
            except (ExprError, ValueError, TypeError, ArithmeticError) as e:
                msg = e.message if isinstance(e, ExprError) else str(e)
                cells = [c for c in (_flat(lin[i].get(x)) for x in prog.referenced_fields) for c in c if c]
                cells += [c for c in param_cells if c]
                if on_error == "ERROR":
                    ctx.issue("ERROR", "DERIVE_EVAL_FAILED", f"派生列「{f}」求值失败：{msg}", cell=cells[0] if cells else None,
                              field=f, rid=ds.meta[i].get("rid"), related=cells)
                    bad.add(i)
                else:
                    ctx.issue("WARN", "DERIVE_EVAL_FAILED", f"派生列「{f}」求值失败，已置空：{msg}",
                              cell=cells[0] if cells else None, field=f, rid=ds.meta[i].get("rid"))
                    row[f] = None
            lin[i][f] = [c for x in prog.referenced_fields for c in _flat(lin[i].get(x)) if c] + [c for c in param_cells if c]
        if col.get("mode", "ADD") == "REPLACE":
            for c in out_cols:
                if c.field == f:
                    c.type = t
        else:
            out_cols.append(ColumnSchema(f, t, origin="derive"))
    keep = [i for i in range(len(rows)) if i not in bad]
    rej = sorted(bad)
    main = Dataset(out_cols, [rows[i] for i in keep], [lin[i] for i in keep], [ds.meta[i] for i in keep])
    reject = Dataset(out_cols, [rows[i] for i in rej], [lin[i] for i in rej], [ds.meta[i] for i in rej])
    return {"out": main, "out_reject": reject}


def _flat(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


# ======================= SELECT_RENAME =======================
def infer_select(node, inputs):
    res = InferResult()
    src = inputs.get("in")
    if src is None:
        return res
    cols = []
    names = set()
    for item in (node.get("config") or {}).get("columns", []):
        f, new = item["field"], item.get("as") or item["field"]
        c = src.get(f)
        if c is None:
            res.errors.append(_err("FIELD_NOT_FOUND", f"字段「{f}」不存在"))
            continue
        if not FIELD_NAME.match(new) or new in RESERVED_FIELDS:
            res.errors.append(_err("DSL_INVALID", f"新字段名「{new}」不合法或是保留字"))
        if new in names:
            res.errors.append(_err("DSL_INVALID", f"输出字段「{new}」重复"))
        names.add(new)
        cols.append(ColumnSchema(new, c.type, c.is_key, c.origin, renamed_from=f if new != f else None))
    res.outputs["out"] = PortSchema(cols, src.single_row)
    return res


def run_select(node, inputs, ctx):
    ds: Dataset = inputs["in"]
    items = (node.get("config") or {}).get("columns", [])
    cols = []
    for it in items:
        c = ds.column(it["field"])
        cols.append(ColumnSchema(it.get("as") or it["field"], c.type if c else "string", c.is_key if c else False))
    out = Dataset(cols)
    for r, lin, m in zip(ds.rows, ds.lineage, ds.meta, strict=True):
        out.rows.append({(it.get("as") or it["field"]): r.get(it["field"]) for it in items})
        out.lineage.append({(it.get("as") or it["field"]): lin.get(it["field"]) for it in items})
        out.meta.append(m)
    return {"out": out}


# ======================= UNION =======================
def infer_union(node, inputs):
    res = InferResult()
    parts = inputs.get("in") or []
    if isinstance(parts, PortSchema):
        parts = [parts]
    cols: list[ColumnSchema] = []
    for p in parts:
        for c in p.columns:
            ex = next((x for x in cols if x.field == c.field), None)
            if ex is None:
                cols.append(ColumnSchema(c.field, c.type, c.is_key, c.origin))
            elif parse_type(ex.type).name != parse_type(c.type).name:
                res.warnings.append(_err("UNION_TYPE_MISMATCH", f"字段「{c.field}」在各输入中的类型不同（{ex.type} / {c.type}）"))
    res.outputs["out"] = PortSchema(cols, False)
    return res


def run_union(node, inputs, ctx):
    from cellflow.engine.source import union_by_name

    parts = inputs.get("in") or []
    if isinstance(parts, Dataset):
        parts = [parts]
    return {"out": union_by_name(parts) if parts else Dataset([])}


# ======================= LOOKUP =======================
def infer_lookup(node, inputs):
    res = InferResult()
    cfg = node.get("config") or {}
    src, dic = inputs.get("in"), inputs.get("in_dict")
    if src is None or dic is None:
        return res
    cols = list(src.columns)
    for on in cfg.get("on", []):
        if src.get(on["left"]) is None:
            res.errors.append(_err("FIELD_NOT_FOUND", f"查表键「{on['left']}」不在主数据中"))
        if dic.get(on["right"]) is None:
            res.errors.append(_err("FIELD_NOT_FOUND", f"查表键「{on['right']}」不在字典中"))
    for s in cfg.get("select", []):
        c = dic.get(s["field"])
        new = s.get("as") or s["field"]
        if c is None:
            res.errors.append(_err("FIELD_NOT_FOUND", f"字典中没有字段「{s['field']}」"))
            continue
        if src.get(new) is not None:
            res.errors.append(_err("COL_CONFLICT", f"查表输出字段「{new}」与主数据字段重名，请设置别名"))
        cols.append(ColumnSchema(new, c.type, False, origin=f"dict.{s['field']}"))
    res.outputs["out"] = PortSchema(cols, src.single_row)
    return res


def run_lookup(node, inputs, ctx):
    cfg = node.get("config") or {}
    ds, dic = inputs["in"], inputs["in_dict"]
    on = cfg.get("on", [])
    index: dict[tuple, int] = {}
    dups = []
    for j, r in enumerate(dic.rows):
        k = tuple(key_norm(r.get(o["right"])) for o in on)
        if any(x is None for x in k):
            continue
        if k in index:
            dups.append((k, dic.lineage[j].get(on[0]["right"])))
        index[k] = j
    if dups:
        raise FatalNodeError("LOOKUP_KEY_NOT_UNIQUE", f"字典键不唯一：{', '.join(str(d[0][0]) for d in dups[:10])}",
                             {"cells": [d[1] for d in dups[:20]]})
    sel = cfg.get("select", [])
    cols = list(ds.columns) + [ColumnSchema(s.get("as") or s["field"], (dic.column(s["field"]) or ColumnSchema("", "string")).type)
                               for s in sel]
    out = Dataset(cols)
    miss = cfg.get("onMissing", "NULL")
    for i, r in enumerate(ds.rows):
        k = tuple(key_norm(r.get(o["left"])) for o in on)
        j = index.get(k)
        row, lin = dict(r), dict(ds.lineage[i])
        if j is None:
            if miss in ("WARN", "ERROR"):
                cells = ds.cells_of(i, [o["left"] for o in on])
                ctx.issue(miss if miss == "ERROR" else "WARN", "LOOKUP_MISSING", f"查表找不到键 {k[0] if len(k) == 1 else k}",
                          cell=cells[0] if cells else None, rid=ds.meta[i].get("rid"))
                if miss == "ERROR":
                    continue
            for s in sel:
                row[s.get("as") or s["field"]] = None
        else:
            for s in sel:
                row[s.get("as") or s["field"]] = dic.rows[j].get(s["field"])
                lin[s.get("as") or s["field"]] = dic.lineage[j].get(s["field"])
        out.rows.append(row)
        out.lineage.append(lin)
        out.meta.append(ds.meta[i])
    return {"out": out}


# ======================= JOIN（§8） =======================
def _select_plan(cfg: dict, left: PortSchema, right: PortSchema, broadcast: bool) -> tuple[list[tuple[str, str, str]], list[dict]]:
    """返回 [(side, 原字段, 输出名)] 与警告。"""
    la, ra = cfg.get("leftAlias", "left"), cfg.get("rightAlias", "right")
    aliases = cfg.get("conflictPolicy", {}).get("aliases") or cfg.get("aliases") or {}
    sel = cfg.get("select") or [f"{la}.*", f"{ra}.*"]
    right_keys = set() if broadcast else {o["right"] for o in cfg.get("on", [])}
    left_keys = set() if broadcast else {o["left"] for o in cfg.get("on", [])}
    merge_keys = (cfg.get("conflictPolicy") or {}).get("keyColumns", "MERGE") == "MERGE"
    items: list[tuple[str, str]] = []
    for s in sel:
        side_alias, _, f = s.partition(".")
        side = "L" if side_alias == la else "R" if side_alias == ra else None
        if side is None:
            continue
        schema = left if side == "L" else right
        if f == "*":
            for c in schema.columns:
                if side == "R" and merge_keys and c.field in right_keys and c.field in left_keys:
                    continue
                items.append((side, c.field))
        else:
            items.append((side, f))
    names = Counter()
    for side, f in items:
        key = f"{la if side == 'L' else ra}.{f}"
        names[aliases.get(key, f)] += 1
    plan, warns = [], []
    for side, f in items:
        key = f"{la if side == 'L' else ra}.{f}"
        out = aliases.get(key)
        if out is None:
            out = f
            if names[f] > 1:
                out = f"{la if side == 'L' else ra}_{f}"
                warns.append(_err("COL_CONFLICT_RESOLVED", f"{key} 与另一侧字段重名，已自动命名为 {out}"))
        plan.append((side, f, out))
    return plan, warns


def infer_join(node, inputs):
    res = InferResult()
    cfg = node.get("config") or {}
    left, right = inputs.get("in_left"), inputs.get("in_right")
    if left is None or right is None:
        return res
    jt = cfg.get("joinType", "INNER")
    broadcast = jt == "BROADCAST"
    if broadcast and not right.single_row:
        res.warnings.append(_err("BROADCAST_RIGHT_NOT_SINGLE", "广播关联的右侧应为单行数据（如键值区），运行时不是恰好 1 行会报错"))
    if not broadcast:
        if not cfg.get("on"):
            res.errors.append(_err("DSL_INVALID", "请至少配置一组关联键"))
        for o in cfg.get("on", []):
            lc, rc = left.get(o["left"]), right.get(o["right"])
            if lc is None or rc is None:
                res.errors.append(_err("FIELD_NOT_FOUND", f"关联键 {o['left']} = {o['right']} 中有字段不存在"))
                continue
            lt, rt = parse_type(lc.type).name, parse_type(rc.type).name
            numeric = {"int", "long"}
            if lt != rt and not (lt in numeric and rt in numeric) and cfg.get("keyNormalize") != "STRING":
                res.warnings.append(_err("JOIN_KEY_TYPE_MISMATCH", f"关联键类型不一致（{lc.type} / {rc.type}），建议选择「统一为文本比较」"))
    plan, warns = _select_plan(cfg, left, right, broadcast)
    res.warnings += warns
    seen = set()
    cols = []
    for side, f, out in plan:
        src = (left if side == "L" else right).get(f)
        if src is None:
            res.errors.append(_err("FIELD_NOT_FOUND", f"输出字段「{f}」不存在"))
            continue
        if out in seen:
            res.errors.append(_err("COL_CONFLICT", f"输出字段「{out}」重复，请设置别名"))
            continue
        seen.add(out)
        cols.append(ColumnSchema(out, src.type, src.is_key and side == "L", origin=f"{side}.{f}",
                                 renamed_from=f if out != f else None))
    res.outputs["out_main"] = PortSchema(cols, left.single_row)
    res.outputs["out_unmatched"] = PortSchema(list(left.columns), False)
    return res


PANDAS_LIKE_CARD = {"MANY_TO_ONE", "ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY"}


def run_join(node, inputs, ctx: RunContext):
    cfg = node.get("config") or {}
    left: Dataset = inputs["in_left"]
    right: Dataset = inputs["in_right"]
    jt = cfg.get("joinType", "INNER")
    lschema, rschema = PortSchema(left.columns), PortSchema(right.columns)
    plan, _ = _select_plan(cfg, lschema, rschema, jt == "BROADCAST")
    cols = []
    for side, f, out in plan:
        src = (left if side == "L" else right).column(f)
        cols.append(ColumnSchema(out, src.type if src else "string", bool(src and src.is_key and side == "L")))

    def emit(target: Dataset, i: int, j: int | None):
        row, lin = {}, {}
        for side, f, out in plan:
            if side == "L":
                row[out], lin[out] = left.rows[i].get(f), left.lineage[i].get(f)
            else:
                row[out] = right.rows[j].get(f) if j is not None else None
                lin[out] = right.lineage[j].get(f) if j is not None else None
        target.rows.append(row)
        target.lineage.append(lin)
        rid = left.meta[i].get("rid")
        if j is not None:
            rid = f"{rid}+{right.meta[j].get('rid')}"
        target.meta.append({**left.meta[i], "rid": rid})

    main, unmatched = Dataset(cols), Dataset(list(left.columns))
    if jt == "BROADCAST":
        if len(right) != 1:
            raise FatalNodeError("PARAM_NOT_SINGLE_ROW", f"广播关联的右侧必须恰好 1 行，实际 {len(right)} 行")
        for i in range(len(left)):
            emit(main, i, 0)
        return {"out_main": main, "out_unmatched": unmatched}

    on = cfg.get("on", [])
    lk = [o["left"] for o in on]
    rk = [o["right"] for o in on]
    norm = key_norm

    def lkey(i):
        return tuple(norm(left.rows[i].get(k)) for k in lk)

    def rkey(j):
        return tuple(norm(right.rows[j].get(k)) for k in rk)

    r_index: dict[tuple, list[int]] = defaultdict(list)
    for j in range(len(right)):
        k = rkey(j)
        if any(x is None for x in k):
            continue
        r_index[k].append(j)
    card = cfg.get("expectedCardinality", "MANY_TO_ONE")
    if card in ("MANY_TO_ONE", "ONE_TO_ONE"):
        dups = {k: js for k, js in r_index.items() if len(js) > 1}
        if dups:
            top = sorted(dups.items(), key=lambda kv: -len(kv[1]))[:10]
            raise FatalNodeError(
                "JOIN_KEY_NOT_UNIQUE",
                "右侧关联键不唯一：" + "；".join(f"{k[0] if len(k) == 1 else k}（{len(js)} 行）" for k, js in top),
                {"keys": [{"key": list(k), "cells": [right.lineage[j].get(rk[0]) for j in js]} for k, js in top]},
            )
    l_counts: Counter = Counter()
    null_left = []
    for i in range(len(left)):
        k = lkey(i)
        if any(x is None for x in k):
            null_left.append(i)
        else:
            l_counts[k] += 1
    if card in ("ONE_TO_MANY", "ONE_TO_ONE"):
        dups = [k for k, n in l_counts.items() if n > 1]
        if dups:
            raise FatalNodeError("JOIN_KEY_NOT_UNIQUE", "左侧关联键不唯一：" + "；".join(str(k[0]) for k in dups[:10]))
    # 执行前估算输出行数，拦截数据膨胀（§8.3）
    est = sum(n * len(r_index.get(k, [])) for k, n in l_counts.items())
    if jt == "LEFT":
        est += len(null_left) + sum(n for k, n in l_counts.items() if k not in r_index)
    g = cfg.get("explosionGuard") or {}
    abs_limit = int(g.get("maxOutputRows") or ctx.settings.get("join.maxOutputRows", 1_000_000))
    if card == "MANY_TO_MANY":
        limit = abs_limit
    else:
        limit = min(abs_limit, max(len(left), 1) * float(g.get("maxAmplification", 1.0)))
    if est > limit:
        top = sorted(((k, n * len(r_index.get(k, []))) for k, n in l_counts.items() if k in r_index), key=lambda x: -x[1])[:10]
        raise FatalNodeError(
            "JOIN_EXPLOSION",
            f"预估输出 {est} 行，超过上限 {int(limit)} 行；导致膨胀的键：" + "、".join(f"{k[0] if len(k) == 1 else k}（{n}）" for k, n in top),
            {"estimated": est, "limit": int(limit), "topKeys": [{"key": list(k), "rows": n} for k, n in top]},
        )
    policy = cfg.get("unmatchedPolicy", "SIDE_OUTPUT")
    for i in range(len(left)):
        k = lkey(i)
        js = r_index.get(k, []) if not any(x is None for x in k) else []
        if js:
            for j in js:
                emit(main, i, j)
            continue
        if jt == "LEFT":
            cells = left.cells_of(i, lk)
            why = "关联键为空" if any(x is None for x in k) else f"右侧找不到键 {k[0] if len(k) == 1 else k}"
            ctx.issue("WARN", "JOIN_UNMATCHED", f"未匹配：{why}", cell=cells[0] if cells else None, rid=left.meta[i].get("rid"))
            if policy == "KEEP_NULLS":
                emit(main, i, None)
            else:
                unmatched.rows.append(left.rows[i])
                unmatched.lineage.append(left.lineage[i])
                unmatched.meta.append(left.meta[i])
    return {"out_main": main, "out_unmatched": unmatched}


# ======================= VALIDATOR（§9） =======================
RULE_TYPES = {"NOT_NULL", "RANGE", "REGEX", "ENUM", "UNIQUE", "FOREIGN_KEY", "EXPR", "RECONCILE", "ROW_COUNT"}
_AGG = re.compile(r"^(sum|count|min|max)\((\w*)\)$")


def infer_validator(node, inputs):
    res = InferResult()
    cfg = node.get("config") or {}
    main = inputs.get("in_main")
    if main is None:
        return res
    params = _param_infos(cfg, inputs)
    refs = cfg.get("refs") or {}
    for r in cfg.get("rules", []):
        rt = r.get("type")
        rid = r.get("ruleId", "?")
        if rt not in RULE_TYPES:
            res.errors.append(_err("DSL_INVALID", f"规则 {rid}：不支持的类型 {rt}"))
            continue
        for f in (r.get("fields") or ([r["field"]] if r.get("field") else [])):
            if main.get(f) is None:
                res.errors.append(_err("FIELD_NOT_FOUND", f"规则 {rid}：字段「{f}」不存在"))
        if rt == "FOREIGN_KEY":
            ref = r.get("ref") or {}
            port = refs.get(ref.get("input", ""))
            if port is None:
                res.errors.append(_err("REF_NOT_CONNECTED", f"规则 {rid}：引用输入「{ref.get('input')}」不存在，请先添加引用输入并连线"))
            elif port not in inputs:
                res.errors.append(_err("REF_NOT_CONNECTED", f"规则 {rid}：引用输入「{ref.get('input')}」未连线"))
            elif inputs[port].get(ref.get("field", "")) is None:
                res.errors.append(_err("FIELD_NOT_FOUND", f"规则 {rid}：被引用字段「{ref.get('field')}」不存在"))
        if rt == "EXPR":
            _check_expr(r.get("expr", ""), main.field_infos(), params, res, f"规则 {rid}", "bool")
        if rt == "REGEX" and len(r.get("pattern", "")) > 200:
            res.errors.append(_err("DSL_INVALID", f"规则 {rid}：正则过长"))
        if rt == "RECONCILE":
            m = _AGG.match((r.get("detailAgg") or "").replace(" ", ""))
            if not m:
                res.errors.append(_err("DSL_INVALID", f"规则 {rid}：明细聚合应为 sum(字段) / count() / min(字段) / max(字段)"))
            elif m.group(2) and main.get(m.group(2)) is None:
                res.errors.append(_err("FIELD_NOT_FOUND", f"规则 {rid}：字段「{m.group(2)}」不存在"))
            s = r.get("summary") or {}
            if s.get("param") not in params or s.get("field") not in params.get(s.get("param"), {}):
                res.errors.append(_err("DSL_INVALID", f"规则 {rid}：汇总值应来自参数端口（参数「{s.get('param')}」的字段「{s.get('field')}」）"))
    res.outputs["out_pass"] = PortSchema(list(main.columns), main.single_row)
    res.outputs["out_reject"] = PortSchema(list(main.columns), False)
    return res


def _msg(r: dict, default: str) -> str:
    return r.get("message") or default


def run_validator(node, inputs, ctx: RunContext):
    cfg = node.get("config") or {}
    ds: Dataset = inputs["in_main"]
    params, p_lin = _load_params(cfg, inputs)
    refs = {a: inputs[p] for a, p in (cfg.get("refs") or {}).items() if p in inputs}
    failed: set[int] = set()

    def violate(r, i, f, message):
        sev = r.get("severity", "ERROR")
        cell = ds.lineage[i].get(f) if f else None
        if isinstance(cell, list):
            cell = cell[0] if cell else None
        ctx.issue(sev, "RULE_VIOLATION", message, cell=cell, field=f, rule=r.get("ruleId"), rid=ds.meta[i].get("rid"),
                  value=ds.rows[i].get(f) if f else None)
        if sev == "ERROR":
            failed.add(i)

    for r in cfg.get("rules", []):
        if r.get("enabled") is False:
            continue
        rt = r["type"]
        if rt == "NOT_NULL":
            for f in r.get("fields") or [r.get("field")]:
                for i, row in enumerate(ds.rows):
                    v = row.get(f)
                    if v is None or (isinstance(v, str) and v.strip() == ""):
                        violate(r, i, f, _msg(r, f"「{f}」必填"))
        elif rt == "RANGE":
            f = r["field"]
            lo, hi, inc = r.get("min"), r.get("max"), r.get("inclusive", True)
            for i, row in enumerate(ds.rows):
                v = row.get(f)
                if v is None:
                    continue
                try:
                    bad = (lo is not None and (v < lo if inc else v <= lo)) or (hi is not None and (v > hi if inc else v >= hi))
                except TypeError:
                    bad = True
                if bad:
                    violate(r, i, f, _msg(r, f"「{f}」={v} 超出范围 [{lo}, {hi}]"))
        elif rt == "REGEX":
            f = r["field"]
            rx = re.compile(r["pattern"])
            for i, row in enumerate(ds.rows):
                v = row.get(f)
                if v is not None and not rx.fullmatch(str(v)):
                    violate(r, i, f, _msg(r, f"「{f}」={v} 格式不符合要求"))
        elif rt == "ENUM":
            f = r["field"]
            allowed = {key_norm(x) for x in r.get("values", [])}
            for i, row in enumerate(ds.rows):
                v = row.get(f)
                if v is not None and key_norm(v) not in allowed:
                    violate(r, i, f, _msg(r, f"「{f}」={v} 不在允许值中"))
        elif rt == "UNIQUE":
            fs = r.get("fields") or [r.get("field")]
            groups: dict[tuple, list[int]] = defaultdict(list)
            for i, row in enumerate(ds.rows):
                k = tuple(key_norm(row.get(f)) for f in fs)
                if any(x is None for x in k):
                    continue
                groups[k].append(i)
            for k, idx in groups.items():
                if len(idx) > 1:
                    for i in idx:
                        violate(r, i, fs[0], _msg(r, f"（{'、'.join(fs)}）={k} 重复出现 {len(idx)} 次"))
        elif rt == "FOREIGN_KEY":
            f, ref = r["field"], r["ref"]
            ref_ds = refs.get(ref["input"])
            if ref_ds is None:
                raise FatalNodeError("REF_NOT_CONNECTED", f"引用输入「{ref['input']}」未连线")
            allowed = {key_norm(x.get(ref["field"])) for x in ref_ds.rows}
            for i, row in enumerate(ds.rows):
                v = row.get(f)
                if v is not None and key_norm(v) not in allowed:
                    violate(r, i, f, _msg(r, f"「{f}」={v} 在「{ref['input']}.{ref['field']}」中不存在"))
        elif rt == "EXPR":
            prog = compile_expr(r["expr"], {c.field: FieldInfo(c.type) for c in ds.columns}, _param_schema(cfg, inputs))
            f0 = prog.referenced_fields[0] if prog.referenced_fields else None
            for i, row in enumerate(ds.rows):
                try:
                    ok = prog.evaluate(row, params, ds.meta[i])
                except ExprError as e:
                    violate(r, i, f0, f"规则求值失败：{e.message}")
                    continue
                if ok is False:
                    violate(r, i, f0, _msg(r, f"不满足条件：{r['expr']}"))
        elif rt == "RECONCILE":
            m = _AGG.match(r["detailAgg"].replace(" ", ""))
            fn, f = m.group(1), m.group(2)
            vals = [row.get(f) for row in ds.rows] if f else []
            if fn == "count":
                agg = len(ds.rows)
            else:
                nums = [v for v in vals if v is not None]
                agg = (sum(nums) if fn == "sum" else min(nums) if fn == "min" else max(nums)) if nums else 0
            s = r["summary"]
            target = params.get(s["param"], {}).get(s["field"])
            cell = p_lin.get(s["param"], {}).get(s["field"])
            tol = float(r.get("tolerance", 0) or 0)
            if target is None or abs(float(agg) - float(target)) > tol:
                sev = r.get("severity", "ERROR")
                ctx.issue(sev, "RECONCILE_MISMATCH", _msg(r, f"明细 {r['detailAgg']} = {agg}，汇总为 {target}，不一致"),
                          cell=cell, rule=r.get("ruleId"), value=target, related=[cell] if cell else None)
        elif rt == "ROW_COUNT":
            n = len(ds.rows)
            lo, hi = r.get("min"), r.get("max")
            if (lo is not None and n < lo) or (hi is not None and n > hi):
                ctx.issue(r.get("severity", "ERROR"), "RULE_VIOLATION", _msg(r, f"行数 {n} 不在 [{lo}, {hi}] 范围内"),
                          rule=r.get("ruleId"))
    keep = [i for i in range(len(ds)) if i not in failed]
    rej = sorted(failed)
    return {"out_pass": ds.take(keep), "out_reject": ds.take(rej)}


# ======================= SINK =======================
def infer_sink(node, inputs):
    res = InferResult()
    cfg = node.get("config") or {}
    src = inputs.get("in")
    binding = cfg.get("binding") or {}
    if not cfg.get("dataset") or not FIELD_NAME.match(cfg.get("dataset", "")):
        res.errors.append(_err("DSL_INVALID", "输出节点的数据集名必须是字母、数字、下划线"))
    if src is not None:
        for k in binding.get("keyFields") or []:
            if src.get(k) is None:
                res.errors.append(_err("FIELD_NOT_FOUND", f"主键字段「{k}」不存在"))
        for m in binding.get("columnMapping") or []:
            if src.get(m.get("field", "")) is None:
                res.errors.append(_err("FIELD_NOT_FOUND", f"映射字段「{m.get('field')}」不存在"))
            if "transform" in m:
                res.errors.append(_err("DSL_INVALID", "字段映射不支持计算，请使用派生列节点（D22）"))
        if not binding.get("keyFields"):
            res.warnings.append(_err("SINK_NO_KEY", "未声明主键：只能整表替换，变更明细只区分新增/删除"))
    return res


def run_sink(node, inputs, ctx: RunContext):
    ds: Dataset = inputs["in"]
    keys = ((node.get("config") or {}).get("binding") or {}).get("keyFields") or []
    if keys:
        groups: dict[tuple, list[int]] = defaultdict(list)
        for i, r in enumerate(ds.rows):
            groups[tuple(key_norm(r.get(k)) for k in keys)].append(i)
        for k, idx in groups.items():
            if len(idx) > 1:
                cells = [c for i in idx for c in ds.cells_of(i, keys)]
                ctx.issue("ERROR", "DUPLICATE_KEY", f"主键（{'、'.join(keys)}）={k} 重复 {len(idx)} 次",
                          cell=cells[0] if cells else None, related=cells)
    return {}


# ======================= PIVOT（分组转列） =======================
PIVOT_AGGS = {"FIRST", "SUM", "COUNT", "MAX", "MIN", "AVG"}


def _pivot_value_type(agg: str, t: str) -> str:
    base = parse_type(t).name
    if agg == "COUNT":
        return "long"
    if agg == "AVG":
        return "float"
    if agg == "SUM" and base == "int":
        return "long"
    return t


def infer_pivot(node, inputs):
    """按分组字段分组，把「转列字段」的每个取值变成一列，单元格取「值字段」（重复时按聚合方式合并）。"""
    res = InferResult()
    src = inputs.get("in")
    if src is None:
        return res
    cfg = node.get("config") or {}
    group_by, pivot, value = cfg.get("groupBy") or [], cfg.get("pivotField"), cfg.get("valueField")
    agg = cfg.get("agg") or "FIRST"
    if not group_by:
        res.errors.append(_err("DSL_INVALID", "请选择分组字段"))
    if not pivot or not value:
        res.errors.append(_err("DSL_INVALID", "请选择「转成列的字段」和「值字段」"))
    if agg not in PIVOT_AGGS:
        res.errors.append(_err("DSL_INVALID", f"不支持的合并方式 {agg}"))
    for f in [*group_by, pivot, value]:
        if f and src.get(f) is None:
            res.errors.append(_err("FIELD_NOT_FOUND", f"字段「{f}」不存在"))
    if pivot and pivot in group_by or value and value in group_by:
        res.errors.append(_err("DSL_INVALID", "分组字段不能同时作为转列字段或值字段"))
    cols = [ColumnSchema(f, src.get(f).type, True, src.get(f).origin) for f in group_by if src.get(f)]
    names = set(group_by)
    vt = src.get(value).type if value and src.get(value) else "string"
    specs = cfg.get("columns") or []
    if not specs and not res.errors:
        res.warnings.append(_err("PIVOT_NO_COLUMNS", "还没有配置要生成的列：点「从数据生成列」"))
    for c in specs:
        f = c.get("field") or ""
        if not FIELD_NAME.match(f) or f in RESERVED_FIELDS:
            res.errors.append(_err("DSL_INVALID", f"列「{c.get('value')}」的字段名「{f}」不合法"))
            continue
        if f in names:
            res.errors.append(_err("DSL_INVALID", f"字段名「{f}」重复"))
            continue
        names.add(f)
        cols.append(ColumnSchema(f, c.get("type") or _pivot_value_type(agg, vt)))
    res.outputs["out"] = PortSchema(cols, False)
    return res


def _agg(agg: str, vals: list) -> Any:
    xs = [v for v in vals if v is not None]
    if agg == "COUNT":
        return len(xs)
    if not xs:
        return None
    if agg == "FIRST":
        return xs[0]
    if agg == "MAX":
        return max(xs)
    if agg == "MIN":
        return min(xs)
    total = sum(xs)
    return total / len(xs) if agg == "AVG" else total


def run_pivot(node, inputs, ctx: RunContext):
    ds: Dataset = inputs["in"]
    cfg = node.get("config") or {}
    group_by, pivot, value = cfg["groupBy"], cfg["pivotField"], cfg["valueField"]
    agg = cfg.get("agg") or "FIRST"
    specs = cfg.get("columns") or []
    by_value = {norm_text(str(c["value"])): c for c in specs}
    out_schema = infer_pivot(node, {"in": PortSchema(ds.columns)}).outputs["out"]
    groups: dict[tuple, dict] = {}
    unknown: Counter = Counter()
    for i, r in enumerate(ds.rows):
        key = tuple(key_norm(r.get(g)) for g in group_by)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"first": i, "cells": defaultdict(list)}
        pv = r.get(pivot)
        if pv is None or norm_text(str(pv)) == "":
            continue
        spec = by_value.get(norm_text(str(pv)))
        if spec is None:
            unknown[norm_text(str(pv))] += 1
            continue
        g["cells"][spec["field"]].append((r.get(value), ds.lineage[i].get(value)))
    out = Dataset(out_schema.columns)
    for k, g in enumerate(groups.values()):
        i = g["first"]
        row = {f: ds.rows[i].get(f) for f in group_by}
        lin = {f: ds.lineage[i].get(f) for f in group_by}
        for spec in specs:
            items = g["cells"].get(spec["field"], [])
            vals = [v for v, _ in items]
            if agg == "FIRST" and len({str(v) for v in vals if v is not None}) > 1:
                cells = [c for _, c in items if isinstance(c, str)]
                ctx.issue("WARN", "PIVOT_DUPLICATE", f"分组「{'/'.join(str(row[f]) for f in group_by)}」的「{spec['value']}」有 {len(vals)} 个不同的值，取第一个；如需合计请把合并方式改为求和",
                          cell=cells[0] if cells else None, field=spec["field"], related=cells)
            row[spec["field"]] = _agg(agg, vals)
            lin[spec["field"]] = [c for _, c in items if c] or None
        out.rows.append(row)
        out.lineage.append(lin)
        out.meta.append({**ds.meta[i], "rid": f"{ctx.node_id}/{k + 1}"})
    for v, n in unknown.most_common():
        ctx.issue("WARN", "PIVOT_UNKNOWN_VALUE", f"「{pivot}」出现了未配置成列的取值「{v}」（{n} 行），已忽略；可在节点中点「从数据生成列」补上")
    return {"out": out}


REGISTRY = {
    "EXCEL_SOURCE": (infer_source, None),
    "FILTER": (infer_filter, run_filter),
    "DERIVE": (infer_derive, run_derive),
    "SELECT_RENAME": (infer_select, run_select),
    "UNION": (infer_union, run_union),
    "LOOKUP": (infer_lookup, run_lookup),
    "JOIN": (infer_join, run_join),
    "VALIDATOR": (infer_validator, run_validator),
    "SINK": (infer_sink, run_sink),
    "PIVOT": (infer_pivot, run_pivot),
}
