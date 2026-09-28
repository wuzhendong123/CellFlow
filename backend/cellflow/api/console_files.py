"""文件与区域相关的控制台接口（WIREFRAME P2、P3、P3-1）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel

from cellflow.api.common import ok
from cellflow.api.deps import operator
from cellflow.engine.columns import validate_column_specs
from cellflow.engine.grid import WorkbookError, norm_text
from cellflow.engine.locate import Rect, slice_block, suggest_locator
from cellflow.engine.shapes import parse_detail, suggest_shape
from cellflow.engine.source import preview_region
from cellflow.engine.univer import sheet_snapshot
from cellflow.errors import CFError
from cellflow.services import files

router = APIRouter(prefix="/api")


@router.post("/files")
async def upload(request: Request, file: UploadFile = File(...), who: str = Depends(operator)):
    data = await file.read()
    return ok(files.save_upload(data, file.filename or "upload.xlsx", who))


def _grid(file_id: int, sheet: str):
    try:
        return files.load_workbook(file_id).sheet(sheet)
    except WorkbookError as e:
        raise CFError(e.code, e.message, 404 if e.code == "SHEET_NOT_FOUND" else 422) from e


@router.get("/files/{file_id}")
def file_info(file_id: int):
    row = files.get_file(file_id)
    return ok({"fileId": row["id"], "fileName": row["file_name"], "sha256": row["sha256"], "sizeBytes": row["size_bytes"],
               "sheets": row["sheet_meta"], "uploadedBy": row["uploaded_by"],
               "uploadedAt": row["uploaded_at"].isoformat() if row["uploaded_at"] else None})


@router.get("/files/{file_id}/sheets/{sheet}/univer")
def univer(file_id: int, sheet: str, rows: str | None = None):
    g = _grid(file_id, sheet)
    r1, r2 = 1, 500
    if rows:
        a, _, b = rows.partition("-")
        r1, r2 = max(1, int(a)), int(b or a)
    return ok(sheet_snapshot(g, r1, r2))


class SuggestIn(BaseModel):
    fileId: int
    sheet: str
    range: dict


@router.post("/regions/suggest")
def suggest(body: SuggestIn):
    g = _grid(body.fileId, body.sheet)
    rect = Rect.from_json(body.range)
    block = slice_block(g, rect)
    shape = suggest_shape(block)
    loc = suggest_locator(g, rect)
    columns = []
    if shape in ("DETAIL", "SUMMARY"):
        hdr = parse_detail(slice_block(g, Rect(rect.r1, rect.c1, rect.r1, rect.c2)), {"headerRows": 1})
        columns = [{"source": h, "field": f"col{i + 1}", "type": "string"} for i, h in enumerate(hdr.headers)]
    elif shape == "KEY_VALUE":
        columns = [{"source": norm_text(block.values[i, 0]), "field": f"key{i + 1}", "type": "string"}
                   for i in range(block.values.shape[0]) if norm_text(block.values[i, 0])]
    return ok({"shape": shape, "locator": loc, "columns": columns, "range": rect.to_json()})


class PreviewIn(BaseModel):
    fileId: int
    sheet: str
    region: dict
    limit: int = 50


@router.post("/regions/preview")
def region_preview(body: PreviewIn):
    errs = validate_column_specs(body.region.get("columns") or [])
    if errs:
        raise CFError("DSL_INVALID", "；".join(errs), 422)
    wb = files.load_workbook(body.fileId)
    return ok(preview_region(wb, body.sheet, {**body.region, "outputPortId": body.region.get("outputPortId", "o")}, body.limit))
