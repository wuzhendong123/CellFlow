"""表达式引擎：CEL（TECH_DESIGN §5.4、§5.5，C1/C2）。

- 解析：cel-python；禁止任何 Python eval。
- 设计期类型检查：用「带类型的样本值」试算一次（typed dry-run），可发现未知字段、类型不匹配（如 int × double）并推导结果类型。
- 运行期：空值传递（任一引用字段为空 → 结果为空，除非表达式使用 coalesce / isNull）；出错按单行报告。
- 保留变量：params（参数端口）、meta（index、sheetRow）。
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Any

import celpy
import celpy.celtypes as ct
from lark import Token

from cellflow.engine.types import parse_type

MAX_EXPR_LEN = 1000
MAX_REGEX_LEN = 200
NULL_AWARE = {"coalesce", "isNull"}
_UTC = dt.UTC


class ExprError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


# ---------------- 值转换 ----------------
def to_cel(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return ct.BoolType(v)
    if isinstance(v, int):
        return ct.IntType(v)
    if isinstance(v, float):
        return ct.DoubleType(v)
    if isinstance(v, Decimal):
        return ct.DoubleType(float(v))
    if isinstance(v, str):
        return ct.StringType(v)
    if isinstance(v, dt.datetime):
        return ct.TimestampType(v if v.tzinfo else v.replace(tzinfo=_UTC))
    if isinstance(v, dt.date):
        return ct.TimestampType(dt.datetime(v.year, v.month, v.day, tzinfo=_UTC))
    if isinstance(v, (list, tuple)):
        return ct.ListType([to_cel(x) for x in v])
    if isinstance(v, dict):
        return ct.MapType({ct.StringType(k): to_cel(x) for k, x in v.items()})
    return ct.StringType(str(v))


def from_cel(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, ct.BoolType):
        return bool(v)
    if isinstance(v, ct.IntType):
        return int(v)
    if isinstance(v, ct.DoubleType):
        return float(v)
    if isinstance(v, ct.StringType):
        return str(v)
    if isinstance(v, ct.TimestampType):
        return dt.datetime(v.year, v.month, v.day, v.hour, v.minute, v.second)
    if isinstance(v, ct.ListType):
        return [from_cel(x) for x in v]
    if isinstance(v, ct.MapType):
        return {str(k): from_cel(x) for k, x in v.items()}
    return v


def cel_type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, ct.BoolType):
        return "bool"
    if isinstance(v, ct.IntType):
        return "long"
    if isinstance(v, ct.DoubleType):
        return "float"
    if isinstance(v, ct.StringType):
        return "string"
    if isinstance(v, ct.TimestampType):
        return "datetime"
    if isinstance(v, ct.ListType):
        return "list<" + (cel_type_name(v[0]) if len(v) else "string") + ">"
    if isinstance(v, ct.MapType):
        return "json"
    # cel-python 的部分算术（如 double * double）返回 Python 原生类型，而不是 celtypes
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "long"
    if isinstance(v, float):
        return "float"
    if isinstance(v, dt.datetime):
        return "datetime"
    if isinstance(v, (list, tuple)):
        return "list<" + (cel_type_name(v[0]) if len(v) else "string") + ">"
    if isinstance(v, dict):
        return "json"
    return "string"


def sample_for(type_str: str, enum_values: list | None = None) -> Any:
    t = parse_type(type_str)
    return _sample(t, enum_values)


def _sample(t, enum_values=None):
    n = t.name
    if n in ("int", "long"):
        return ct.IntType(1)
    if n in ("float", "decimal"):
        return ct.DoubleType(1.5)
    if n == "bool":
        return ct.BoolType(True)
    if n in ("date", "datetime"):
        return ct.TimestampType(dt.datetime(2026, 1, 1, tzinfo=_UTC))
    if n == "enum":
        if enum_values and all(isinstance(x, int) for x in enum_values):
            return ct.IntType(enum_values[0])
        return ct.StringType(str(enum_values[0]) if enum_values else "a")
    if n == "list":
        return ct.ListType([_sample(t.item)])
    if n == "struct":
        return ct.MapType({ct.StringType(k): _sample(ft) for k, ft in t.fields})
    if n == "json":
        return ct.MapType({})
    return ct.StringType("a")


def compatible(result_type: str, declared: str) -> bool:
    d = parse_type(declared).name
    r = result_type.split("<")[0]
    if r == "null":
        return True
    groups = {
        "long": {"int", "long", "decimal", "float", "enum"},
        "float": {"float", "decimal"},
        "string": {"string", "enum", "date", "datetime", "json"},
        "bool": {"bool"},
        "datetime": {"date", "datetime"},
        "list": {"list", "json"},
        "json": {"json", "struct"},
    }
    return d in groups.get(r, {r})


# ---------------- 函数库（前后端同一份清单） ----------------
def _s(x):
    return "" if x is None else str(x)


def _num(x):
    return float(x) if isinstance(x, ct.DoubleType) else int(x)


def _round(x, n=ct.IntType(0)):
    q = Decimal(str(float(x))).quantize(Decimal(1).scaleb(-int(n)), rounding="ROUND_HALF_UP")
    return ct.DoubleType(float(q))


def _ts(x):
    if isinstance(x, ct.TimestampType):
        return x
    s = str(x).strip()
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return ct.TimestampType(dt.datetime.strptime(s, f).replace(tzinfo=_UTC))
        except ValueError:
            continue
    raise ValueError(f"不是有效日期：{s}")


def _regex(p):
    p = str(p)
    if len(p) > MAX_REGEX_LEN:
        raise ValueError("正则过长")
    return _compiled(p)


@lru_cache(maxsize=256)
def _compiled(p: str):
    return re.compile(p)


FUNCTIONS: dict[str, tuple[Any, str]] = {
    "len": (lambda s: ct.IntType(len(_s(s))), "len(s) 文本长度"),
    "upper": (lambda s: ct.StringType(_s(s).upper()), "upper(s) 转大写"),
    "lower": (lambda s: ct.StringType(_s(s).lower()), "lower(s) 转小写"),
    "trim": (lambda s: ct.StringType(_s(s).strip()), "trim(s) 去首尾空白"),
    "substr": (lambda s, a, n=None: ct.StringType(_s(s)[int(a): (int(a) + int(n)) if n is not None else None]),
               "substr(s, start, len) 截取文本（start 从 0 开始）"),
    "replace": (lambda s, a, b: ct.StringType(_s(s).replace(_s(a), _s(b))), "replace(s, old, new) 替换"),
    "split": (lambda s, sep: ct.ListType([ct.StringType(x) for x in _s(s).split(_s(sep))]), "split(s, sep) 拆分为列表"),
    "join": (lambda lst, sep: ct.StringType(_s(sep).join(_s(x) for x in lst)), "join(list, sep) 拼接"),
    "format": (lambda f, *a: ct.StringType(_s(f).format(*[from_cel(x) for x in a])), "format('{}-{}', a, b) 格式化"),
    "regexMatch": (lambda s, p: ct.BoolType(_regex(p).fullmatch(_s(s)) is not None), "regexMatch(s, pattern) 整体匹配"),
    "abs": (lambda x: type(x)(abs(_num(x))), "abs(x) 绝对值"),
    "round": (_round, "round(x, n) 四舍五入到 n 位小数"),
    "floor": (lambda x: ct.IntType(math.floor(float(x))), "floor(x) 向下取整"),
    "ceil": (lambda x: ct.IntType(math.ceil(float(x))), "ceil(x) 向上取整"),
    "min": (lambda *a: min(a), "min(a, b, ...) 最小值"),
    "max": (lambda *a: max(a), "max(a, b, ...) 最大值"),
    "clamp": (lambda x, lo, hi: max(lo, min(hi, x)), "clamp(x, lo, hi) 限制在区间内"),
    "coalesce": (lambda *a: next((x for x in a if x is not None), None), "coalesce(a, b, ...) 取第一个非空值"),
    "isNull": (lambda x: ct.BoolType(x is None), "isNull(x) 是否为空"),
    "decimal": (lambda x, n: _round(x, n), "decimal(x, scale) 保留 scale 位小数"),
    "date": (_ts, "date(s) 文本转日期"),
    "datetime": (_ts, "datetime(s) 文本转日期时间"),
    "addDays": (lambda d, n: ct.TimestampType(_ts(d) + dt.timedelta(days=int(n))), "addDays(d, n) 加天数"),
    "diffDays": (lambda a, b: ct.IntType((_ts(a) - _ts(b)).days), "diffDays(a, b) 相差天数 a-b"),
    "formatDate": (lambda d, f: ct.StringType(_ts(d).strftime(_s(f))), "formatDate(d, '%Y-%m-%d') 日期转文本"),
    "sum": (lambda lst: sum(lst, ct.IntType(0) if all(isinstance(x, ct.IntType) for x in lst) else ct.DoubleType(0)),
            "sum(list) 列表求和"),
}
BUILTINS_DOC = {
    "size": "size(x) 列表/文本长度", "int": "int(x) 转整数", "double": "double(x) 转小数", "string": "string(x) 转文本",
    "bool": "bool(x) 转布尔", "matches": "s.matches(re) 正则匹配", "startsWith": "s.startsWith(p)", "endsWith": "s.endsWith(p)",
    "contains": "s.contains(p)", "exists": "list.exists(x, 条件)", "all": "list.all(x, 条件)", "map": "list.map(x, 表达式)",
    "filter": "list.filter(x, 条件)", "timestamp": "timestamp(s)",
}
_FUNCS = {k: v[0] for k, v in FUNCTIONS.items()}
# 转译执行器（把 CEL 转成 Python 代码）比解释执行快 4~6 倍；它必须是进程内第一个创建的环境（解析器全局共享、语法树类型随之确定）。
# 个别表达式（如自定义函数）无法转译时回退为解释执行。转译执行器通过模块全局变量传递上下文，不是线程安全的，求值时加锁。
_ENV = celpy.Environment(runner_class=celpy.CompiledRunner)
_EVAL_LOCK = threading.Lock()
logging.getLogger("celpy").setLevel(logging.CRITICAL)  # 逐行求值错误由我们转成问题记录，不需要库再打错误日志


class _Runner:
    def __init__(self, ast):
        self._ast = ast
        try:
            self._r = celpy.CompiledRunner(_ENV, ast, _FUNCS)
            self.compiled = True
        except Exception:  # noqa: BLE001 — 转译器不支持的写法
            self._r = celpy.InterpretedRunner(_ENV, ast, _FUNCS)
            self.compiled = False

    def evaluate(self, activation: dict) -> Any:
        if not self.compiled:
            return self._r.evaluate(activation)
        with _EVAL_LOCK:
            try:
                return self._r.evaluate(activation)
            except celpy.CELEvalError as e:
                inner = _unwrap(e)
                if not _is_transpile_gap(inner):
                    raise inner from None
        # 转译后的代码找不到部分自定义函数（按模块路径引用）：此表达式改用解释执行
        self._r = celpy.InterpretedRunner(_ENV, self._ast, _FUNCS)
        self.compiled = False
        return self._r.evaluate(activation)


def _is_transpile_gap(e: celpy.CELEvalError) -> bool:
    return len(e.args) > 1 and e.args[1] is NameError or "is not defined" in str(e.args[0] if e.args else "")


def _unwrap(e: celpy.CELEvalError) -> celpy.CELEvalError:
    """转译执行器把错误再包一层 ('evaluation error', 类型, 内层参数)，取出最内层，保持与解释执行一致。"""
    while len(e.args) > 2 and e.args[0] == "evaluation error" and isinstance(e.args[2], tuple):
        inner = e.args[2]
        if e.args[1] is celpy.CELEvalError:
            e = celpy.CELEvalError(*inner)
        else:
            e = celpy.CELEvalError(str(inner[0]) if inner else "evaluation error", e.args[1], inner)
    return e


def function_catalog() -> list[dict]:
    return [{"name": k, "doc": v[1]} for k, v in FUNCTIONS.items()] + [{"name": k, "doc": v} for k, v in BUILTINS_DOC.items()]


# ---------------- 编译与求值 ----------------
@dataclass
class FieldInfo:
    type: str
    enum_values: list | None = None


@dataclass
class Program:
    expr: str
    prog: Any
    referenced_fields: list[str]
    referenced_params: list[tuple[str, str]]
    uses_meta: bool
    null_aware: bool
    result_type: str = "null"
    param_aliases: list[str] = field(default_factory=list)

    def evaluate(self, row: dict, params: dict[str, dict] | None = None, meta: dict | None = None) -> Any:
        """返回 Python 值；空值传递；出错抛 ExprError。"""
        if not self.null_aware:
            if any(row.get(f) is None for f in self.referenced_fields):
                return None
            for alias, f in self.referenced_params:
                if (params or {}).get(alias, {}).get(f) is None:
                    return None
        act = {f: to_cel(row.get(f)) for f in self.referenced_fields}
        if self.referenced_params or self.param_aliases:
            act["params"] = to_cel(params or {})
        if self.uses_meta:
            act["meta"] = to_cel(meta or {})
        try:
            return from_cel(self.prog.evaluate(act))
        except celpy.CELEvalError as e:
            raise ExprError(_friendly(e)) from None
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
            raise ExprError(str(e)) from None


def _friendly(e: Exception) -> str:
    msg = str(e.args[0]) if getattr(e, "args", None) else str(e)
    if "divide by zero" in msg or "modulo by zero" in msg:
        return "除数为 0"
    if "no matching overload" in msg or "no such overload" in msg:
        return "运算的两边类型不一致"
    if "undeclared reference" in msg:
        m = re.search(r"undeclared reference to '([^']+)'", msg)
        return f"未知的字段或变量「{m.group(1) if m else '?'}」"
    if "no such member" in msg:
        m = re.search(r"no such member in mapping: '([^']+)'", msg)
        return f"不存在的参数字段「{m.group(1) if m else '?'}」"
    return msg[:200]


def compile_expr(
    expr: str,
    fields: dict[str, FieldInfo],
    params: dict[str, dict[str, FieldInfo]] | None = None,
) -> Program:
    """编译并做设计期检查；失败抛 ExprError（带修复提示）。"""
    expr = (expr or "").strip()
    if not expr:
        raise ExprError("表达式为空")
    if len(expr) > MAX_EXPR_LEN:
        raise ExprError(f"表达式超过 {MAX_EXPR_LEN} 个字符")
    try:
        ast = _ENV.compile(expr)
    except Exception as e:
        raise ExprError("语法错误：" + str(e).splitlines()[0][:200]) from None
    idents = [str(t) for t in ast.scan_values(lambda v: isinstance(v, Token) and v.type == "IDENT")]
    no_strings = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", expr)
    referenced = sorted({i for i in set(idents) if i in fields and re.search(rf"(?<![\w.]){re.escape(i)}\b", no_strings)})
    ref_params: list[tuple[str, str]] = []
    for m in re.finditer(r"\bparams\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)", expr):
        ref_params.append((m.group(1), m.group(2)))
    for alias, f in ref_params:
        if alias not in (params or {}):
            raise ExprError(f"参数别名「{alias}」不存在，请先连接参数端口并设置别名")
        if f not in params[alias]:
            raise ExprError(f"参数「{alias}」中没有字段「{f}」")
    prog = _Runner(ast)
    p = Program(expr, prog, referenced, ref_params, "meta" in idents, bool(NULL_AWARE & set(idents)),
                param_aliases=list((params or {}).keys()))
    # 设计期试算
    act = {name: _sample(parse_type(fi.type), fi.enum_values) for name, fi in fields.items()}
    act["params"] = ct.MapType({
        ct.StringType(a): ct.MapType({ct.StringType(k): _sample(parse_type(fi.type), fi.enum_values) for k, fi in fs.items()})
        for a, fs in (params or {}).items()
    })
    act["meta"] = ct.MapType({ct.StringType("index"): ct.IntType(1), ct.StringType("sheetRow"): ct.IntType(1)})
    try:
        out = prog.evaluate(act)
    except celpy.CELEvalError as e:
        msg = _friendly(e)
        if msg == "除数为 0" or isinstance(e.args[1] if len(e.args) > 1 else None, type) and issubclass(e.args[1], (ValueError, OverflowError)):
            # 样本值导致的取值错误（如 level - 1 为 0）不是设计错误：换一组样本再试，仍失败则类型未知
            act2 = {k: (ct.IntType(2) if isinstance(v, ct.IntType) else ct.DoubleType(2.5) if isinstance(v, ct.DoubleType) else v)
                    for k, v in act.items()}
            try:
                p.result_type = cel_type_name(prog.evaluate(act2))
            except Exception:
                p.result_type = "null"
            return p
        hint = None
        if msg == "运算的两边类型不一致":
            hint = "整数与小数混合运算时，请用 double(x) 或 int(x) 显式转换，例如 int(double(a) * b)"
        raise ExprError(msg, hint) from None
    except (ValueError, TypeError) as e:
        raise ExprError(str(e)[:200]) from None
    p.result_type = cel_type_name(out)
    return p


def suggest_cast_fix(expr: str, fields: dict[str, FieldInfo], params=None) -> str | None:
    """「插入类型转换」一键修复：把参与运算的整数字段包成 double(...) 后再试。"""
    ints = [f for f, fi in fields.items() if parse_type(fi.type).name in ("int", "long") and re.search(rf"\b{f}\b", expr)]
    candidate = expr
    for f in ints:
        candidate = re.sub(rf"(?<![\w.]){f}\b", f"double({f})", candidate)
    if candidate == expr:
        return None
    try:
        compile_expr(candidate, fields, params)
        return candidate
    except ExprError:
        return None
