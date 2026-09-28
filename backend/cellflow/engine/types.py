"""字段类型系统与值转换（TECH_DESIGN §5.3、§7.8）。"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from openpyxl.utils.datetime import from_excel

from cellflow.engine.grid import is_blank, norm_text

RESERVED_FIELDS = {
    "params", "meta", "in", "as", "break", "const", "continue", "else", "for", "function", "if", "import", "let",
    "loop", "package", "namespace", "return", "var", "void", "while", "null", "true", "false",
}
FIELD_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

DEFAULT_NULLS = ["", "-", "N/A", "NULL"]
DEFAULT_BOOL = {
    "true": ["true", "是", "y", "yes", "1", "√", "✓", "开", "on"],
    "false": ["false", "否", "n", "no", "0", "×", "✗", "关", "off"],
}
# Excel 数值只有 15 位有效数字：超过 1e15 的整数若以数字存储，末位可能已被截断
EXCEL_INT_PRECISION = 10**15


class CoerceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TypeSpec:
    name: str  # string|int|long|decimal|float|bool|date|datetime|enum|json|list|struct
    precision: int | None = None
    scale: int | None = None
    item: TypeSpec | None = None
    fields: tuple[tuple[str, TypeSpec], ...] = ()

    def __str__(self) -> str:
        if self.name == "decimal":
            return f"decimal({self.precision},{self.scale})"
        if self.name == "list":
            return f"list<{self.item}>"
        if self.name == "struct":
            return "struct<" + ",".join(f"{k}:{v}" for k, v in self.fields) + ">"
        return self.name


def _split_top(s: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "<" or ch == "(":
            depth += 1
        elif ch == ">" or ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return [p.strip() for p in parts]


def parse_type(s: str) -> TypeSpec:
    s = (s or "string").strip()
    low = s.lower()
    if low in ("string", "int", "long", "float", "double", "bool", "date", "datetime", "enum", "json"):
        return TypeSpec("float" if low == "double" else low)
    m = re.fullmatch(r"decimal\((\d+)\s*,\s*(\d+)\)", low)
    if m:
        return TypeSpec("decimal", int(m.group(1)), int(m.group(2)))
    if low == "decimal":
        return TypeSpec("decimal", 38, 10)
    if low.startswith("list<") and s.endswith(">"):
        return TypeSpec("list", item=parse_type(s[5:-1]))
    if low.startswith("struct<") and s.endswith(">"):
        fields = []
        for part in _split_top(s[7:-1]):
            k, _, t = part.partition(":")
            fields.append((k.strip(), parse_type(t)))
        return TypeSpec("struct", fields=tuple(fields))
    raise ValueError(f"不支持的类型 {s}")


def clean_text(v: Any, trim: bool = True, width: bool = True) -> Any:
    if not isinstance(v, str):
        return v
    if width:
        v = unicodedata.normalize("NFKC", v)
    if trim:
        v = v.replace(" ", " ").translate(dict.fromkeys(map(ord, "​‌‍﻿⁠"), None)).strip()
    return v


def _to_int(v: Any) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if abs(v) >= EXCEL_INT_PRECISION:
            raise CoerceError("ID_PRECISION_LOST", "数值超过 Excel 的 15 位精度，可能已被截断，请将该列设为文本格式")
        if not v.is_integer():
            raise CoerceError("TYPE_COERCE_FAILED", f"{v} 不是整数")
        return int(v)
    if isinstance(v, Decimal):
        if v != v.to_integral_value():
            raise CoerceError("TYPE_COERCE_FAILED", f"{v} 不是整数")
        return int(v)
    s = str(v).replace(",", "").strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise CoerceError("TYPE_COERCE_FAILED", f"「{v}」不是整数") from None
    if d != d.to_integral_value():
        raise CoerceError("TYPE_COERCE_FAILED", f"{v} 不是整数")
    return int(d)


def _to_decimal(v: Any) -> Decimal:
    if isinstance(v, bool):
        raise CoerceError("TYPE_COERCE_FAILED", "布尔值不能转为数字")
    if isinstance(v, float):
        return Decimal(repr(v))
    try:
        return Decimal(str(v).replace(",", "").strip())
    except InvalidOperation:
        raise CoerceError("TYPE_COERCE_FAILED", f"「{v}」不是数字") from None


def _to_date(v: Any, date1904: bool, with_time: bool) -> Any:
    if isinstance(v, dt.datetime):
        return v if with_time else v.date()
    if isinstance(v, dt.date):
        return dt.datetime.combine(v, dt.time()) if with_time else v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        base = dt.datetime(1904, 1, 1) if date1904 else None
        d = from_excel(v, base) if base else from_excel(v)
        return d if with_time else d.date()
    s = norm_text(v)
    fmts = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y%m%d"]
    if with_time:
        fmts = ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", *fmts]
    for f in fmts:
        try:
            d = dt.datetime.strptime(s, f)
            return d if with_time else d.date()
        except ValueError:
            continue
    raise CoerceError("TYPE_COERCE_FAILED", f"「{v}」不是有效日期")


def convert(v: Any, t: TypeSpec, spec: dict, date1904: bool = False) -> Any:
    """把一个非空值转换为目标类型；失败抛 CoerceError。"""
    name = t.name
    if name == "string":
        if isinstance(v, float) and v.is_integer():
            if abs(v) >= EXCEL_INT_PRECISION:
                raise CoerceError("ID_PRECISION_LOST", "数值超过 Excel 的 15 位精度，可能已被截断，请将该列设为文本格式")
            return str(int(v))
        if isinstance(v, dt.datetime):
            return v.strftime("%Y-%m-%d") if v.time() == dt.time() else v.isoformat(sep=" ")
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)
    if name in ("int", "long"):
        n = _to_int(v)
        if name == "int" and not (-(2**31) <= n < 2**31):
            raise CoerceError("TYPE_COERCE_FAILED", f"{n} 超出 int 范围")
        return n
    if name == "float":
        if isinstance(v, bool):
            raise CoerceError("TYPE_COERCE_FAILED", "布尔值不能转为数字")
        return float(_to_decimal(v))
    if name == "decimal":
        d = _to_decimal(v)
        q = d.quantize(Decimal(1).scaleb(-(t.scale or 0)), rounding=ROUND_HALF_UP)
        int_digits = len(str(abs(int(q)))) if int(q) != 0 else 0
        if t.precision is not None and int_digits > (t.precision - (t.scale or 0)):
            raise CoerceError("TYPE_COERCE_FAILED", f"{d} 超出 decimal({t.precision},{t.scale}) 的范围")
        return q
    if name == "bool":
        tokens = spec.get("boolTokens") or DEFAULT_BOOL
        if isinstance(v, bool):
            return v
        s = norm_text(v).lower()
        if isinstance(v, (int, float)):
            s = str(int(v)) if float(v).is_integer() else s
        if s in [str(x).lower() for x in tokens.get("true", [])]:
            return True
        if s in [str(x).lower() for x in tokens.get("false", [])]:
            return False
        raise CoerceError("TYPE_COERCE_FAILED", f"「{v}」不是可识别的布尔写法")
    if name in ("date", "datetime"):
        return _to_date(v, date1904, name == "datetime")
    if name == "enum":
        mapping = spec.get("enumMap") or {}
        key = norm_text(str(int(v)) if isinstance(v, float) and v.is_integer() else v)
        for k, mapped in mapping.items():
            if norm_text(k) == key:
                return mapped
        raise CoerceError("ENUM_UNKNOWN", f"未知枚举值「{v}」")
    if name == "json":
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(str(v))
        except json.JSONDecodeError:
            raise CoerceError("TYPE_COERCE_FAILED", "不是合法的 JSON") from None
    if name == "list":
        split = spec.get("split") or {}
        sep = split.get("item", "|")
        if isinstance(v, list):
            items = v
        else:
            items = [x for x in str(v).split(sep) if not is_blank(x)]
        return [convert(clean_text(x), t.item or TypeSpec("string"), spec, date1904) for x in items]
    if name == "struct":
        split = spec.get("split") or {}
        sep = split.get("kv", ":")
        if isinstance(v, dict):
            parts = [v.get(k) for k, _ in t.fields]
        else:
            parts = str(v).split(sep)
        if len(parts) != len(t.fields):
            raise CoerceError("TYPE_COERCE_FAILED", f"「{v}」应有 {len(t.fields)} 段（分隔符「{sep}」）")
        return {k: convert(clean_text(p), ft, spec, date1904) for (k, ft), p in zip(t.fields, parts, strict=True)}
    raise CoerceError("TYPE_COERCE_FAILED", f"不支持的类型 {name}")


def to_jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, list):
        return [to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: to_jsonable(x) for k, x in v.items()}
    return v
