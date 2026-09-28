"""文件与区域相关的控制台接口（WIREFRAME P2、P3、P3-1）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel

from cellflow.api.common import ok
from cellflow.api.deps import operator
from cellflow.engine.columns import validate_column_specs
from cellflow.engine.detect import detect_regions, suggest_region
from cellflow.engine.grid import WorkbookError
from cellflow.engine.locate import Rect
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
    shape: str | None = None  # 指定形态时按该形态推荐字段（切换形态时用）


@router.post("/regions/suggest")
def suggest(body: SuggestIn):
    return ok(suggest_region(_grid(body.fileId, body.sheet), Rect.from_json(body.range), body.shape))


class DetectIn(BaseModel):
    fileId: int
    sheet: str


@router.post("/regions/detect")
def detect(body: DetectIn):
    """整张 Sheet 自动识别区域（按空行 / 空列切块），结果供用户逐个调整。"""
    return ok({"regions": detect_regions(_grid(body.fileId, body.sheet))})


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
