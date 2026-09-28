"""文件上传与读取（TECH_DESIGN §6.5.2 文件组）。"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from threading import Lock

from sqlalchemy import select

from cellflow.engine.grid import Workbook, WorkbookError
from cellflow.errors import CFError, not_found
from cellflow.meta.db import get_engine
from cellflow.meta.tables import source_file
from cellflow.services import settings
from cellflow.storage import get_storage

ALLOWED_SUFFIX = (".xlsx", ".xlsm")
_cache: OrderedDict[int, Workbook] = OrderedDict()
_lock = Lock()
_CACHE_SIZE = 8


def save_upload(data: bytes, file_name: str, uploaded_by: str) -> dict:
    max_mb = settings.get("file.maxSizeMB")
    if len(data) > max_mb * 1024 * 1024:
        raise CFError("FILE_TOO_LARGE", f"文件超过 {max_mb}MB 上限", 413)
    if not file_name.lower().endswith(ALLOWED_SUFFIX):
        raise CFError("FILE_UNSUPPORTED", "只支持 xlsx 文件，请另存为 xlsx 后上传", 422)
    try:
        wb = Workbook(data, max_cells=settings.get("file.maxCells"))
        meta = wb.sheet_meta()
    except WorkbookError as e:
        raise CFError(e.code, e.message, 413 if e.code == "FILE_TOO_LARGE" else 422) from e
    sha = hashlib.sha256(data).hexdigest()
    key = f"files/{sha[:2]}/{sha}.xlsx"
    storage = get_storage()
    if not storage.exists(key):
        storage.put(key, data)
    with get_engine().begin() as c:
        fid = c.execute(source_file.insert().values(
            sha256=sha, storage_uri=key, file_name=file_name[:255], size_bytes=len(data),
            sheet_meta=meta, uploaded_by=uploaded_by[:64],
        )).inserted_primary_key[0]
    with _lock:
        _cache[fid] = wb
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return {"fileId": fid, "fileName": file_name, "sha256": sha, "sizeBytes": len(data), "sheets": meta}


def get_file(file_id: int) -> dict:
    with get_engine().connect() as c:
        row = c.execute(select(source_file).where(source_file.c.id == file_id)).mappings().first()
    if not row:
        raise not_found("文件")
    return dict(row)


def load_workbook(file_id: int) -> Workbook:
    with _lock:
        if file_id in _cache:
            _cache.move_to_end(file_id)
            return _cache[file_id]
    row = get_file(file_id)
    data = get_storage().get(row["storage_uri"])
    wb = Workbook(data, max_cells=settings.get("file.maxCells"))
    with _lock:
        _cache[file_id] = wb
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return wb


def file_bytes(file_id: int) -> tuple[bytes, str]:
    row = get_file(file_id)
    return get_storage().get(row["storage_uri"]), row["file_name"]


def clear_cache() -> None:
    with _lock:
        _cache.clear()
