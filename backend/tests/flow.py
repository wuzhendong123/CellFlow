"""端到端测试辅助：控制台操作 + 签名的 Open API 调用。"""

from __future__ import annotations

import json
import time
import uuid

import dsl_examples as d
import fixtures
from conftest import BIZ_DB

from cellflow.services.auth import sign

OP = {"X-CF-Op-Token": "test-op-token", "X-CF-Operator": "%E7%AE%A1%E7%90%86%E5%91%98"}
WHO = {"X-CF-Operator": "%E7%AD%96%E5%88%92%E5%B0%8F%E7%8E%8B"}


def okd(r):
    assert r.status_code < 300, r.text
    body = r.json()
    assert body["code"] == "OK", body
    return body["data"]


def setup_pipeline(client, dsl=None, publish=True, data=None):
    ds = okd(client.post("/api/datasources", json={"name": "biz", "hostRef": "test_biz", "dbName": BIZ_DB}, headers=OP))
    f = okd(client.post("/api/files", files={"file": ("hero.xlsx", data or fixtures.hero_config(), "application/octet-stream")},
                        headers=WHO))
    p = okd(client.post("/api/pipelines", json={"code": "hero_config", "name": "角色配置", "datasourceId": ds["id"],
                                                "sampleFileId": f["fileId"]}, headers=WHO))
    draft = okd(client.get(f"/api/pipelines/{p['id']}/draft"))
    saved = okd(client.put(f"/api/pipelines/{p['id']}/draft", json={"dsl": dsl or d.hero_dsl(), "draftVersion": draft["draftVersion"]},
                           headers=WHO))
    assert saved["analysis"]["ok"], saved["analysis"]["errors"]
    if publish:
        okd(client.post(f"/api/pipelines/{p['id']}/publish", json={"draftVersion": saved["draftVersion"], "note": "首版"}, headers=OP))
    app = okd(client.post("/api/client-apps", json={"name": "业务服务A", "secretRef": "test_app_secret",
                                                    "allowedPipelines": ["hero_config"], "callbackAllowlist": ["biz-a.internal"]},
                          headers=OP))
    return {"pipeline": p, "datasource": ds, "app": app, "draftVersion": saved["draftVersion"]}


def open_call(client, app, method, path, body=b"", files=None, data=None, nonce=None):
    ts = str(int(time.time()))
    nonce = nonce or uuid.uuid4().hex
    if files is not None:
        req = client.build_request(method, path, files=files, data=data)
        raw = req.read()
        headers = dict(req.headers)
    else:
        raw, headers = body, {}
    headers.update({"X-CF-AppKey": app["appKey"], "X-CF-Timestamp": ts, "X-CF-Nonce": nonce,
                    "X-CF-Signature": sign("test-app-secret", method, path, ts, nonce, raw)})
    return client.request(method, path, content=raw, headers=headers)


def submit(client, app, data, mode="EXECUTE", idem=None, callback=None, **meta):
    m = {"pipelineCode": "hero_config", "mode": mode, "idempotencyKey": idem or uuid.uuid4().hex, "operator": "planner_zhang",
         **meta}
    if callback:
        m["callbackUrl"] = callback
    r = open_call(client, app, "POST", "/open/v1/jobs", files={"file": ("hero.xlsx", data, "application/octet-stream")},
                  data={"meta": json.dumps(m, ensure_ascii=False)})
    return r


def rows(eng, table, order="1"):
    from sqlalchemy import text

    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(f"SELECT * FROM `{table}` ORDER BY {order}")).mappings()]
