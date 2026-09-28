"""节点之间流转的数据集（TECH_DESIGN §6.3）。

实现上用「行字典列表」而非 DataFrame：配置数据规模有限（单文件 ≤ 50 万单元格），
而行级类型（Decimal、日期、列表、结构体）与逐行血缘用原生对象表达更准确。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass
class ColumnSchema:
    field: str
    type: str = "string"
    is_key: bool = False
    origin: str | None = None
    renamed_from: str | None = None

    def to_json(self) -> dict:
        d = {"field": self.field, "type": self.type, "isKey": self.is_key}
        if self.origin:
            d["origin"] = self.origin
        if self.renamed_from:
            d["renamedFrom"] = self.renamed_from
        return d


@dataclass
class Dataset:
    columns: list[ColumnSchema]
    rows: list[dict[str, Any]] = field(default_factory=list)
    lineage: list[dict[str, Any]] = field(default_factory=list)  # 字段 → 'Sheet!A1' 或其列表
    meta: list[dict[str, Any]] = field(default_factory=list)  # {"rid", "sheetRow", "index"}

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def fields(self) -> list[str]:
        return [c.field for c in self.columns]

    def column(self, name: str) -> ColumnSchema | None:
        for c in self.columns:
            if c.field == name:
                return c
        return None

    def take(self, idx: list[int]) -> Dataset:
        return Dataset(
            [replace(c) for c in self.columns],
            [self.rows[i] for i in idx],
            [self.lineage[i] for i in idx],
            [self.meta[i] for i in idx],
        )

    def head(self, n: int) -> Dataset:
        return self.take(list(range(min(n, len(self.rows)))))

    def cells_of(self, i: int, fields: list[str]) -> list[str]:
        out: list[str] = []
        for f in fields:
            v = self.lineage[i].get(f)
            if isinstance(v, list):
                out += [x for x in v if x]
            elif v:
                out.append(v)
        return out

    def schema_json(self) -> list[dict]:
        return [c.to_json() for c in self.columns]

    def page(self, offset: int, limit: int) -> dict:
        from cellflow.engine.types import to_jsonable

        rows = []
        for i in range(offset, min(offset + limit, len(self.rows))):
            rows.append(
                {
                    "_rid": self.meta[i].get("rid"),
                    "data": {k: to_jsonable(v) for k, v in self.rows[i].items()},
                    "_lineage": self.lineage[i],
                }
            )
        return {"total": len(self.rows), "offset": offset, "rows": rows, "columns": self.schema_json()}


def split_addr(addr: str | None) -> tuple[str | None, str | None]:
    if not addr:
        return None, None
    if "!" in addr:
        sheet, cell = addr.rsplit("!", 1)
        return sheet, cell
    return None, addr
