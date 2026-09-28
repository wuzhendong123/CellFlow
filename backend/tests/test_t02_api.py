import fixtures


def upload(client, data, name="hero.xlsx"):
    return client.post("/api/files", files={"file": (name, data, "application/octet-stream")},
                       headers={"X-CF-Operator": "%E5%BC%A0%E4%B8%89"})


def test_upload_and_univer(client):
    r = upload(client, fixtures.hero_config())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert [s["name"] for s in d["sheets"]] == ["角色配置", "道具表"]
    fid = d["fileId"]
    info = client.get(f"/api/files/{fid}").json()["data"]
    assert info["uploadedBy"] == "张三"
    u = client.get(f"/api/files/{fid}/sheets/角色配置/univer?rows=1-10").json()["data"]
    assert u["cellData"]["0"]["0"]["v"] == "开服天数上限"
    assert u["loadedRows"] == {"from": 1, "to": 10}


def test_upload_rejects_non_xlsx_and_too_large(client):
    r = upload(client, b"a,b\n1,2", "x.csv")
    assert r.status_code == 422 and r.json()["code"] == "FILE_UNSUPPORTED"
    from cellflow.services import settings

    settings.update({"file.maxSizeMB": 1}, "t")
    r = upload(client, b"0" * (1024 * 1024 + 1), "big.xlsx")
    assert r.status_code == 413 and r.json()["code"] == "FILE_TOO_LARGE"


def test_same_file_dedup_storage(client):
    data = fixtures.hero_config()  # 只生成一次：openpyxl 写入的时间戳跨秒会让两次生成的字节不同
    a = upload(client, data).json()["data"]
    b = upload(client, data).json()["data"]
    assert a["sha256"] == b["sha256"] and a["fileId"] != b["fileId"]


def test_suggest_and_preview(client):
    fid = upload(client, fixtures.hero_config()).json()["data"]["fileId"]
    s = client.post("/api/regions/suggest", json={"fileId": fid, "sheet": "角色配置",
                                                   "range": {"startRow": 17, "startCol": 1, "endRow": 22, "endCol": 5}}).json()["data"]
    assert s["shape"] == "DETAIL" and s["locator"]["type"] == "ANCHOR"
    assert [c["source"] for c in s["columns"]] == ["奖励ID", "职业", "等级", "道具ID", "数量"]
    region = {"regionId": "r", "name": "奖励", "shape": s["shape"], "locator": s["locator"], "designRange": s["range"],
              "shapeOptions": {"headerRows": 1}, "columns": s["columns"]}
    p = client.post("/api/regions/preview", json={"fileId": fid, "sheet": "角色配置", "region": region}).json()["data"]
    assert p["output"]["total"] == 5
    bad = dict(region, columns=[{"source": "奖励ID", "field": "params"}])
    r = client.post("/api/regions/preview", json={"fileId": fid, "sheet": "角色配置", "region": bad})
    assert r.status_code == 422


def test_zip_bomb_rejected(client):
    """解压后体积异常大的文件在解析前就被拒绝。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/worksheets/sheet1.xml", b"\0" * (600 * 2**20))
    r = upload(client, buf.getvalue())
    assert len(buf.getvalue()) < 2 * 2**20
    assert r.status_code == 413 and r.json()["code"] == "FILE_TOO_LARGE"


def test_detect_regions(client):
    """整张 Sheet 自动识别：按空行 / 空列切块，块首单独的文本行作为区域名称。"""
    fid = upload(client, fixtures.hero_config()).json()["data"]["fileId"]
    rs = client.post("/api/regions/detect", json={"fileId": fid, "sheet": "角色配置"}).json()["data"]["regions"]
    got = {r["range"]["a1"]: r["shape"] for r in rs}
    assert got["A1:B5"] == "KEY_VALUE" and got["A8:F11"] == "MATRIX" and got["A17:E23"] == "DETAIL"
    reward = next(r for r in rs if r["range"]["a1"] == "A17:E23")
    assert [c["source"] for c in reward["columns"]] == ["奖励ID", "职业", "等级", "道具ID", "数量"]
    note = next(r for r in rs if r["range"]["startCol"] == 8)
    assert note["name"].startswith("填表说明")  # 标题行成为区域名称
