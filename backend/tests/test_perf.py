"""性能验收（T29，PRD §7 / TECH_DESIGN 性能目标）：设置 CF_PERF=1 时运行，默认跳过（生成大文件较慢）。

目标：5 万行 Sheet 首屏 < 3s；试跑预览 < 3s；解析 + 校验 < 30s；写表 10 万行 < 60s。
文件：10 万行 × 5 列 = 50 万有效单元格（系统设置 file.maxCells 的上限）。
"""

from __future__ import annotations

import io
import os
import time

import openpyxl
import pytest
from dsl_examples import e, sink
from flow import OP, WHO, okd, setup_pipeline, submit
from sqlalchemy import text

pytestmark = pytest.mark.skipif(os.environ.get("CF_PERF") != "1", reason="设置 CF_PERF=1 运行性能验收")
ROWS = 99_999  # 加表头共 10 万行 × 5 列 = 50 万单元格


def big_file(rows: int = ROWS, bump: int = 0) -> bytes:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("道具")
    ws.append(["道具ID", "名称", "等级", "数量", "价格"])
    for i in range(1, rows + 1):
        ws.append([i, f"道具{i}", i % 100, (i * 7 + bump) % 1000, round(i * 0.01, 2)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def perf_dsl() -> dict:
    src = {"id": "src", "type": "EXCEL_SOURCE", "label": "道具", "config": {
        "sheet": {"match": "EXACT", "value": "道具"},
        "regions": [{
            "regionId": "rg", "name": "道具", "shape": "DETAIL", "outputPortId": "out",
            "designRange": {"startRow": 1, "startCol": 1, "endRow": 20, "endCol": 5},
            "locator": {"type": "AUTO_EXPAND", "blankRowsToStop": 2}, "shapeOptions": {"headerRows": 1},
            "columns": [
                {"source": "道具ID", "field": "itemId", "type": "long", "isKey": True, "required": True},
                {"source": "名称", "field": "name", "type": "string", "required": True},
                {"source": "等级", "field": "lv", "type": "int"},
                {"source": "数量", "field": "cnt", "type": "int"},
                {"source": "价格", "field": "price", "type": "decimal"},
            ],
        }],
    }}
    der = {"id": "der", "type": "DERIVE", "label": "总价", "config": {"columns": [{"field": "total", "expr": "double(price) * double(cnt)", "type": "float"}]}}
    out = sink("sink", "items", "perf_items", ["itemId"], [("itemId", "id"), ("name", "name"), ("lv", "lv"), ("cnt", "cnt"), ("price", "price"), ("total", "total")],
               guards={"maxRowChangeRatio": 1, "maxDeleteRatio": 1})
    return {"dslVersion": "1.0", "pipelineCode": "hero_config", "nodes": [src, der, out],
            "edges": [e("e1", "src", "out", "der", "in"), e("e2", "der", "out", "sink", "in")]}


def timed(label, fn, limit, results):
    t0 = time.perf_counter()
    r = fn()
    dt = time.perf_counter() - t0
    results.append((label, dt, limit))
    print(f"[perf] {label}: {dt:.2f}s (目标 < {limit}s)")
    assert dt < limit, f"{label} 用时 {dt:.2f}s 超过 {limit}s"
    return r


def test_performance_targets(client, biz):
    with biz.begin() as c:
        c.execute(text("""CREATE TABLE perf_items (id BIGINT PRIMARY KEY, name VARCHAR(64) NOT NULL, lv INT, cnt INT,
                          price DECIMAL(12,2), total DOUBLE) CHARSET=utf8mb4"""))
    data = big_file()
    results: list = []
    env = setup_pipeline(client, dsl=perf_dsl(), data=data)
    pid = env["pipeline"]["id"]
    fid = timed("上传并解析文件结构", lambda: okd(client.post("/api/files", files={"file": ("big.xlsx", data, "application/octet-stream")}, headers=WHO)), 30, results)["fileId"]
    timed("F1-6 首屏（前 1000 行）", lambda: okd(client.get(f"/api/files/{fid}/sheets/道具/univer?rows=1-1000")), 3, results)
    prev = timed("F8-1 预览试跑（采样 200 行）", lambda: okd(client.post("/api/jobs/test", json={"pipelineId": pid, "fileId": fid, "preview": True}, headers=WHO)), 3, results)
    assert prev["status"] == "VALIDATED"
    jid = timed("首次写表 10 万行（解析 + 校验 + 写入）", lambda: submit(client, env["app"], data).json()["data"]["jobId"], 60, results)
    j = okd(client.get(f"/api/jobs/{jid}"))
    assert j["status"] == "PUBLISHED", j
    with biz.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM perf_items")).scalar() == ROWS
    data2 = big_file(bump=1)
    jid2 = timed("全量比对后再写 10 万行（全部修改）", lambda: submit(client, env["app"], data2).json()["data"]["jobId"], 60, results)
    j2 = okd(client.get(f"/api/jobs/{jid2}"))
    assert j2["status"] == "PUBLISHED" and j2["result"]["tables"][0]["changes"]["u"] > 0, j2
    rel = okd(client.get(f"/api/pipelines/{pid}/releases"))
    target = next(r for r in rel["rows"] if r["jobId"] == jid)
    from cellflow.runtime import executor, queue

    deferred: list[int] = []
    orig = queue.dispatch_release_changes
    queue.dispatch_release_changes = deferred.append  # 生产环境由 Worker 异步补算，这里分开计时
    try:
        rb = timed("F14-1 回滚（互换备份表 + 切换线上指针）", lambda: okd(client.post(f"/api/pipelines/{pid}/rollback", headers=OP, json={
            "targetReleaseId": target["id"], "expectedLiveReleaseId": rel["liveReleaseId"], "reason": "perf", "freeze": False})), 3, results)
    finally:
        queue.dispatch_release_changes = orig
    assert rb["mode"] == "FAST_SWAP" and deferred == [rb["releaseId"]]
    with biz.connect() as c:
        assert c.execute(text("SELECT SUM(cnt) FROM perf_items")).scalar() == sum((i * 7) % 1000 for i in range(1, ROWS + 1))
    timed("回滚后补算变更明细（Worker 异步）", lambda: executor.fill_release_changes(rb["releaseId"]), 30, results)
    ch = okd(client.get(f"/api/releases/{rb['releaseId']}/changes?limit=1"))
    assert ch["total"] == ROWS
    print("\n".join(f"[perf-summary] {a}\t{b:.2f}s\t< {c}s" for a, b, c in results))
