"""端到端：绑定检查（T08）、快照比对与安全闸（T09）、写入与恢复（T10）、Open API（T11）、发布与回归（T16）、
回滚与冻结（T17）、口令审计设置（T18）、清理（T19）。"""

import dsl_examples as d
import fixtures
from flow import OP, WHO, okd, open_call, rows, setup_pipeline, submit
from sqlalchemy import text

from cellflow.services import callbacks


def job(client, jid):
    return okd(client.get(f"/api/jobs/{jid}"))


# -------- T08 绑定检查 --------
def test_f7_binding_checks(client, biz):
    env = setup_pipeline(client, publish=False)
    pid, ds = env["pipeline"]["id"], env["datasource"]["id"]
    tables = okd(client.get(f"/api/datasources/{ds}/tables"))
    assert {t["table"] for t in tables} == {"cfg_hero_base_hp", "cfg_level_reward", "cfg_global_switch"}
    desc = okd(client.get(f"/api/datasources/{ds}/tables/cfg_hero_base_hp"))
    assert desc["primaryKey"] == ["id"] and desc["uniqueKeys"] == [["job_name", "lv"]]
    r = okd(client.post(f"/api/pipelines/{pid}/bindings/check", json={"nodeId": "sink_hp"}))
    assert r["ok"], r["errors"]
    assert any(w["code"] == "AUTO_ID_REASSIGNED" for w in r["warnings"])
    # F7-2 不可空列没有来源、F7-3 类型不兼容
    dsl = d.hero_dsl()
    sink = next(n for n in dsl["nodes"] if n["id"] == "sink_reward")
    sink["config"]["binding"]["columnMapping"] = [{"field": "job", "column": "item_count"}]
    r = okd(client.post(f"/api/pipelines/{pid}/bindings/check", json={"nodeId": "sink_reward", "dsl": dsl}))
    msgs = " ".join(e["message"] for e in r["errors"])
    assert "不能写入列「item_count」" in msgs and "不可为空且没有默认值" in msgs
    # F7-6 主键建议（未声明主键时按目标表唯一键建议）
    sink = next(n for n in dsl["nodes"] if n["id"] == "sink_hp")
    sink["config"]["binding"]["keyFields"] = None
    r = okd(client.post(f"/api/pipelines/{pid}/bindings/check", json={"nodeId": "sink_hp", "dsl": dsl}))
    assert r["keySuggestion"] == ["job", "level"]


def test_f7_5_trigger_table_rejected(client, biz):
    with biz.begin() as c:
        c.execute(text("CREATE TRIGGER trg_x BEFORE INSERT ON cfg_global_switch FOR EACH ROW SET NEW.max_open_days = NEW.max_open_days"))
    env = setup_pipeline(client, publish=False)
    r = okd(client.post(f"/api/pipelines/{env['pipeline']['id']}/bindings/check", json={"nodeId": "sink_global"}))
    assert any(e["code"] == "STRATEGY_NOT_AVAILABLE" and "触发器" in e["message"] for e in r["errors"])
    r = client.post(f"/api/pipelines/{env['pipeline']['id']}/publish", json={"draftVersion": env["draftVersion"]}, headers=OP)
    assert r.status_code == 422 and r.json()["code"] == "DSL_INVALID"


def test_f7_4_table_owned_by_other_pipeline(client, biz):
    env = setup_pipeline(client)
    p2 = okd(client.post("/api/pipelines", json={"code": "other", "name": "另一个", "datasourceId": env["datasource"]["id"]},
                         headers=WHO))
    saved = okd(client.put(f"/api/pipelines/{p2['id']}/draft", json={"dsl": d.hero_dsl(), "draftVersion": 1}, headers=WHO))
    assert any(e["code"] == "TABLE_OWNED" for e in saved["analysis"]["errors"])
    tables = okd(client.get(f"/api/datasources/{env['datasource']['id']}/tables"))
    assert next(t for t in tables if t["table"] == "cfg_level_reward")["owner"]["pipelineCode"] == "hero_config"


def test_datasource_ref_only_and_connection_test(client, biz):
    r = client.post("/api/datasources", json={"name": "x", "hostRef": "mysql://u:p@h", "dbName": "d"}, headers=OP)
    assert r.status_code == 400
    ds = okd(client.post("/api/datasources", json={"name": "biz", "hostRef": "test_biz", "dbName": biz.url.database}, headers=OP))
    t = okd(client.post(f"/api/datasources/{ds['id']}/test"))
    assert t["ok"], t


# -------- T11 Open API + T09/T10 写表 --------
def test_first_submit_writes_tables_and_baseline(client, biz):
    with biz.begin() as c:
        c.execute(text("INSERT INTO cfg_global_switch VALUES (7, 1)"))
    env = setup_pipeline(client)
    r = submit(client, env["app"], fixtures.hero_config(), callback="https://biz-a.internal/cb")
    assert r.status_code == 202, r.text
    jid = r.json()["data"]["jobId"]
    j = job(client, jid)
    # 接管已有数据的表：原有 1 行被替换，删除比例 100% 被安全闸拦截，需人工确认放行
    assert j["status"] == "FAILED_GUARD" and j["result"]["blockedBy"] == ["G4"], j
    assert rows(biz, "cfg_global_switch") == [{"max_open_days": 7, "double_exp": 1}]
    okd(client.post(f"/api/jobs/{jid}/force-publish", json={"reason": "首次接管"}, headers=OP))
    assert len(rows(biz, "cfg_hero_base_hp")) == 11
    assert rows(biz, "cfg_level_reward", "id")[0] == {"id": 1001, "job_name": "战士", "lv": 1, "item_id": 5001, "item_count": 10,
                                                      "item_name": "金币"}
    assert rows(biz, "cfg_global_switch") == [{"max_open_days": 30, "double_exp": 1}]
    rel = okd(client.get(f"/api/pipelines/{env['pipeline']['id']}/releases"))
    kinds = [x["kind"] for x in rel["rows"]]
    assert kinds == ["FORCED", "BASELINE"]
    ch = okd(client.get(f"/api/jobs/{jid}/changes?table=cfg_global_switch"))
    assert {x["op"] for x in ch["rows"]} == {"c", "d"}  # 无主键：修改表现为一删一增
    # Open API 查询
    st = okd(open_call(client, env["app"], "GET", f"/open/v1/jobs/{jid}"))
    assert st["status"] == "PUBLISHED" and st["releaseId"]
    info = okd(open_call(client, env["app"], "GET", "/open/v1/pipelines/hero_config"))
    assert info["rev"] == 1 and {t["table"] for t in info["tables"]} == {"cfg_hero_base_hp", "cfg_level_reward", "cfg_global_switch"}
    live = okd(open_call(client, env["app"], "GET", "/open/v1/pipelines/hero_config/live"))
    assert live["releaseId"] == st["releaseId"]


def test_f11_3_no_change_and_update_detected(client, biz):
    env = setup_pipeline(client)
    assert job(client, submit(client, env["app"], fixtures.hero_config()).json()["data"]["jobId"])["status"] == "PUBLISHED"
    j2 = job(client, submit(client, env["app"], fixtures.hero_config()).json()["data"]["jobId"])
    assert j2["status"] == "NO_CHANGE"
    j3 = job(client, submit(client, env["app"], fixtures.hero_config(hp_overrides={"战士": [101, 120, 150, 180]})).json()["data"]["jobId"])
    assert j3["status"] == "PUBLISHED"
    hp = next(t for t in j3["result"]["tables"] if t["table"] == "cfg_hero_base_hp")
    assert hp["changes"] == {"c": 0, "u": 1, "d": 0}
    ch = okd(client.get(f"/api/jobs/{j3['jobId']}/changes?table=cfg_hero_base_hp"))["rows"][0]
    assert ch["op"] == "u" and ch["changedFields"] == ["base_hp", "hp"] and ch["cells"]["base_hp"] == "角色配置!B9"


def test_f11_4_validation_failure_leaves_tables(client, biz):
    env = setup_pipeline(client)
    submit(client, env["app"], fixtures.hero_config())
    before = rows(biz, "cfg_level_reward", "id")
    jid = submit(client, env["app"], fixtures.hero_config(total_count=1)).json()["data"]["jobId"]
    j = job(client, jid)
    assert j["status"] == "FAILED_VALIDATION"
    assert rows(biz, "cfg_level_reward", "id") == before
    iss = okd(open_call(client, env["app"], "GET", f"/open/v1/jobs/{jid}/issues"))
    assert iss["rows"][0]["code"] == "RECONCILE_MISMATCH" and iss["rows"][0]["cell"] == "E23"


def test_f11_5_6_guards_and_force_publish(client, biz):
    env = setup_pipeline(client)
    submit(client, env["app"], fixtures.hero_config(extra_reward_rows=10))
    # 删除超过 30%：15 → 5
    jid = submit(client, env["app"], fixtures.hero_config()).json()["data"]["jobId"]
    j = job(client, jid)
    assert j["status"] == "FAILED_GUARD" and "G4" in j["result"]["blockedBy"]
    assert len(rows(biz, "cfg_level_reward")) == 15
    # 没有口令不能放行；有理由才能放行
    r = client.post(f"/api/jobs/{jid}/force-publish", json={"reason": "确认删除"}, headers=WHO)
    assert r.status_code == 403
    r = client.post(f"/api/jobs/{jid}/force-publish", json={}, headers=OP)
    assert r.status_code == 400
    okd(client.post(f"/api/jobs/{jid}/force-publish", json={"reason": "活动下线，确认删除"}, headers=OP))
    assert len(rows(biz, "cfg_level_reward")) == 5
    rel = okd(client.get(f"/api/pipelines/{env['pipeline']['id']}/releases"))
    assert rel["rows"][0]["kind"] == "FORCED"
    logs = okd(client.get("/api/audit-logs?action=FORCE_PUBLISH"))
    assert logs["rows"][0]["operator"] == "管理员" and logs["rows"][0]["reason"] == "活动下线，确认删除"


def test_f11_8_drift_detected(client, biz):
    env = setup_pipeline(client)
    submit(client, env["app"], fixtures.hero_config())
    with biz.begin() as c:
        c.execute(text("UPDATE cfg_hero_base_hp SET base_hp = 999 WHERE job_name = '战士' AND lv = 1"))
    j = job(client, submit(client, env["app"], fixtures.hero_config(hp_overrides={"射手": [81, 95, 110, 130]})).json()["data"]["jobId"])
    assert j["status"] == "FAILED_GUARD" and "G7" in j["result"]["blockedBy"]


def test_f11_9_column_overflow(client, biz):
    env = setup_pipeline(client)
    items = [(5001, "金" * 70, "普通"), (5002, "钻石", "稀有"), (5003, "神器碎片", "史诗")]
    j = job(client, submit(client, env["app"], fixtures.hero_config(item_rows=items)).json()["data"]["jobId"])
    assert j["status"] == "FAILED_GUARD" and "G8" in j["result"]["blockedBy"]
    iss = okd(client.get(f"/api/jobs/{j['jobId']}/issues?severity=ERROR"))["rows"]
    assert iss[0]["code"] == "COLUMN_OVERFLOW" and iss[0]["sheet"] == "道具表" and iss[0]["cell"] == "B2"
    r = client.post(f"/api/jobs/{j['jobId']}/force-publish", json={"reason": "x"}, headers=OP)
    assert r.status_code == 409 and r.json()["code"] == "GUARD_NOT_OVERRIDABLE"


def test_f11_10_crash_recovery(client, biz, monkeypatch):
    from cellflow.runtime import executor, writer

    env = setup_pipeline(client)
    submit(client, env["app"], fixtures.hero_config())
    real = writer.rename_atomic

    def crash(*a, **k):
        raise KeyboardInterrupt  # 模拟进程在切换前被杀

    monkeypatch.setattr(writer, "rename_atomic", crash)
    import pytest

    with pytest.raises(KeyboardInterrupt):
        submit(client, env["app"], fixtures.hero_config(hp_overrides={"战士": [1, 2, 3, 4]}))
    monkeypatch.setattr(writer, "rename_atomic", real)
    before = rows(biz, "cfg_hero_base_hp", "job_name, lv")
    assert before[0]["base_hp"] != 1
    out = executor.recover_writing()
    assert out and out[0]["state"] == "FAILED"
    with biz.connect() as c:
        assert not c.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE '%\\\\_\\\\_cfs\\\\_%'")).scalar()


def test_f11_2_atomic_multi_table_and_backups(client, biz):
    env = setup_pipeline(client)
    for k in range(5):
        submit(client, env["app"], fixtures.hero_config(hp_overrides={"战士": [100 + k, 120, 150, 180]}))
    with biz.connect() as c:
        names = [r[0] for r in c.execute(text("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")).all()]
    backups = [n for n in names if n.startswith("cfg_hero_base_hp__cfb_")]
    assert 1 <= len(backups) <= 4  # 保留最近 3 份（含当前线上的上一份）


# -------- F10 Open API 鉴权 / 幂等 / 只校验 / 限流 --------
def test_f10_auth_idempotency_validate_only(client, biz):
    env = setup_pipeline(client)
    app = env["app"]
    r = client.get("/open/v1/jobs/1", headers={"X-CF-AppKey": app["appKey"], "X-CF-Timestamp": "1", "X-CF-Nonce": "n",
                                               "X-CF-Signature": "bad"})
    assert r.status_code == 401
    r1 = submit(client, app, fixtures.hero_config(), idem="same")
    r2 = submit(client, app, fixtures.hero_config(), idem="same")
    assert r1.json()["data"]["jobId"] == r2.json()["data"]["jobId"] and r2.json()["data"]["duplicate"]
    before = rows(biz, "cfg_hero_base_hp", "job_name, lv")
    v = job(client, submit(client, app, fixtures.hero_config(hp_overrides={"战士": [1, 2, 3, 4]}), mode="VALIDATE_ONLY").json()["data"]["jobId"])
    assert v["status"] == "VALIDATED"
    fc = next(t for t in v["result"]["forecast"]["tables"] if t["table"] == "cfg_hero_base_hp")
    assert fc["changes"]["u"] == 4
    assert rows(biz, "cfg_hero_base_hp", "job_name, lv") == before
    r = submit(client, app, fixtures.hero_config(), callback="https://evil.example.com/cb")
    assert r.status_code == 400 and r.json()["code"] == "CALLBACK_NOT_ALLOWED"
    r = submit(client, app, fixtures.hero_config(), pipelineCode="unknown")
    assert r.status_code == 403


def test_f10_replay_and_rate_limit(client, biz):
    env = setup_pipeline(client)
    app = env["app"]
    assert open_call(client, app, "GET", "/open/v1/pipelines/hero_config", nonce="n1").status_code == 200
    assert open_call(client, app, "GET", "/open/v1/pipelines/hero_config", nonce="n1").status_code == 401
    okd(client.put(f"/api/client-apps/{app['id']}", json={"name": "A", "secretRef": "test_app_secret",
                                                           "allowedPipelines": ["hero_config"], "rateLimitPerMin": 2}, headers=OP))
    codes = [open_call(client, app, "GET", "/open/v1/pipelines/hero_config").status_code for _ in range(3)]
    assert codes[-1] == 429


def test_f12_supersede(client, biz, monkeypatch):
    from cellflow.runtime import queue

    env = setup_pipeline(client)
    queued = []
    monkeypatch.setattr(queue, "dispatch", lambda jid: queued.append(jid))
    import cellflow.services.jobs as jobs_mod

    a = jobs_mod.submit(client_app_row(env), {"pipelineCode": "hero_config"}, fixtures.hero_config(), "a.xlsx")["jobId"]
    b = jobs_mod.submit(client_app_row(env), {"pipelineCode": "hero_config"}, fixtures.hero_config(hp_overrides={"战士": [5, 6, 7, 8]}),
                        "b.xlsx")["jobId"]
    v = jobs_mod.submit(client_app_row(env), {"pipelineCode": "hero_config", "mode": "VALIDATE_ONLY"}, fixtures.hero_config(), "c.xlsx")["jobId"]
    assert job(client, a)["status"] == "SUPERSEDED" and job(client, a)["supersededBy"] == b
    assert job(client, v)["status"] == "QUEUED"  # 只校验任务不参与作废
    from cellflow.runtime.executor import run_job

    assert run_job(a) == "SUPERSEDED"
    assert run_job(b) == "PUBLISHED"
    hp = {(x["job_name"], x["lv"]): x["base_hp"] for x in rows(biz, "cfg_hero_base_hp")}
    assert hp[("战士", 1)] == 5  # 最终生效的是最后提交的文件


def client_app_row(env):
    from cellflow.services import client_apps

    return client_apps.get(env["app"]["id"])


def test_f12_2_running_job_superseded_before_write(client, biz, monkeypatch):
    env = setup_pipeline(client)
    import cellflow.services.jobs as jobs_mod
    from cellflow.runtime import executor

    monkeypatch.setattr("cellflow.runtime.queue.dispatch", lambda jid: None)
    a = jobs_mod.submit(client_app_row(env), {"pipelineCode": "hero_config"}, fixtures.hero_config(), "a.xlsx")["jobId"]
    real = executor.newer_execute_exists

    def later_submit(job):
        jobs_mod.submit(client_app_row(env), {"pipelineCode": "hero_config"}, fixtures.hero_config(), "b.xlsx")
        return real(job)

    monkeypatch.setattr(executor, "newer_execute_exists", later_submit)
    assert executor.run_job(a) == "SUPERSEDED"
    assert rows(biz, "cfg_hero_base_hp") == []


def test_callbacks_signed_and_retried(client, biz, monkeypatch):
    env = setup_pipeline(client)
    sent = []
    monkeypatch.setattr(callbacks, "_post", lambda url, body, headers, timeout=5.0: sent.append((url, body, headers)) or 500)
    jid = submit(client, env["app"], fixtures.hero_config(), callback="https://biz-a.internal/cb").json()["data"]["jobId"]
    att = okd(client.get(f"/api/jobs/{jid}/callbacks"))
    assert att[0]["status"] == "PENDING" and att[0]["attempts"] == 1 and att[0]["lastError"] == "HTTP 500"
    import datetime as dt

    ok_calls = []
    n = callbacks.deliver_due(dt.datetime.now() + dt.timedelta(hours=2),
                              poster=lambda url, body, headers: ok_calls.append(headers) or 200)
    assert n == 1 and ok_calls[0]["X-CF-Signature"]
    import json

    body = json.loads(sent[0][1])
    assert body["status"] == "PUBLISHED" and body["pipelineCode"] == "hero_config" and "reportUrl" not in body


# -------- T16 发布与回归 --------
def test_f9_publish_regression_and_revision_lock(client, biz):
    env = setup_pipeline(client)
    pid = env["pipeline"]["id"]
    submit(client, env["app"], fixtures.hero_config())
    dsl = okd(client.get(f"/api/pipelines/{pid}/draft"))
    new = dsl["dsl"]
    next(n for n in new["nodes"] if n["id"] == "derive_hp")["config"]["columns"][0]["expr"] = "int(double(baseHp) * 2.0)"
    saved = okd(client.put(f"/api/pipelines/{pid}/draft", json={"dsl": new, "draftVersion": dsl["draftVersion"]}, headers=WHO))
    rep = okd(client.post(f"/api/pipelines/{pid}/regression", headers=WHO))
    assert rep["summary"]["files"] == 1
    t = next(x for x in rep["items"][0]["tables"] if x["table"] == "cfg_hero_base_hp")
    assert t["changes"]["u"] == 11
    assert rep["diff"]["nodesChanged"][0]["id"] == "derive_hp"
    r = client.post(f"/api/pipelines/{pid}/publish", json={"draftVersion": saved["draftVersion"]}, headers=WHO)
    assert r.status_code == 403  # F9-4 没有口令
    okd(client.post(f"/api/pipelines/{pid}/publish", json={"draftVersion": saved["draftVersion"], "regressionId": rep["id"]}, headers=OP))
    revs = okd(client.get(f"/api/pipelines/{pid}/revisions"))
    assert [(x["rev"], x["status"]) for x in revs] == [(2, "PUBLISHED"), (1, "RETIRED")] and revs[0]["regression"]["files"] == 1
    okd(client.post(f"/api/pipelines/{pid}/revisions/1/republish", json={"reason": "回退"}, headers=OP))
    assert okd(client.get(f"/api/pipelines/{pid}"))["publishedRev"] == 1


def test_f9_6_unpublished_rejected(client, biz):
    env = setup_pipeline(client, publish=False)
    r = submit(client, env["app"], fixtures.hero_config())
    assert r.status_code == 422 and r.json()["code"] == "PIPELINE_NOT_PUBLISHED"


def test_draft_conflict(client, biz):
    env = setup_pipeline(client, publish=False)
    pid = env["pipeline"]["id"]
    r = client.put(f"/api/pipelines/{pid}/draft", json={"dsl": d.hero_dsl(), "draftVersion": 1}, headers=WHO)
    assert r.status_code == 409 and r.json()["data"]["updatedBy"] == "策划小王"


# -------- T17 回滚与冻结 --------
def test_f14_rollback_fast_and_rewrite(client, biz):
    env = setup_pipeline(client)
    pid = env["pipeline"]["id"]
    for k in range(3):
        submit(client, env["app"], fixtures.hero_config(hp_overrides={"战士": [100 + k, 120, 150, 180]}))
    rel = okd(client.get(f"/api/pipelines/{pid}/releases"))
    live, prev, first = rel["rows"][0], rel["rows"][1], rel["rows"][2]
    pre = okd(client.post(f"/api/pipelines/{pid}/rollback/preview", json={"targetReleaseId": prev["id"]}))
    assert pre["mode"] == "FAST_SWAP" and pre["compatible"]
    r = client.post(f"/api/pipelines/{pid}/rollback", json={"targetReleaseId": prev["id"], "expectedLiveReleaseId": live["id"],
                                                            "reason": "线上数值错误"}, headers=OP)
    assert okd(r)["mode"] == "FAST_SWAP"
    hp = {(x["job_name"], x["lv"]): x["base_hp"] for x in rows(biz, "cfg_hero_base_hp")}
    assert hp[("战士", 1)] == 101
    # F14-4 回滚后冻结
    r = submit(client, env["app"], fixtures.hero_config())
    assert r.status_code == 422 and r.json()["code"] == "PIPELINE_FROZEN"
    # F14-5 并发保护
    r = client.post(f"/api/pipelines/{pid}/rollback", json={"targetReleaseId": first["id"], "expectedLiveReleaseId": live["id"],
                                                            "reason": "x"}, headers=OP)
    assert r.status_code == 409 and r.json()["code"] == "LIVE_STATE_CONFLICT"
    # 跨版本回滚（常规路径）
    rel = okd(client.get(f"/api/pipelines/{pid}/releases"))
    baseline = next(x for x in rel["rows"] if x["kind"] == "BASELINE")
    okd(client.post(f"/api/pipelines/{pid}/rollback", json={"targetReleaseId": baseline["id"], "expectedLiveReleaseId": rel["liveReleaseId"],
                                                             "reason": "回到接管前"}, headers=OP))
    assert rows(biz, "cfg_hero_base_hp") == [] and rows(biz, "cfg_level_reward") == []
    okd(client.post(f"/api/pipelines/{pid}/unfreeze", json={"reason": "已修复"}, headers=OP))
    assert submit(client, env["app"], fixtures.hero_config()).status_code == 202


def test_f14_9_rollback_schema_incompatible(client, biz):
    env = setup_pipeline(client)
    pid = env["pipeline"]["id"]
    submit(client, env["app"], fixtures.hero_config())
    submit(client, env["app"], fixtures.hero_config(hp_overrides={"战士": [1, 2, 3, 4]}))
    with biz.begin() as c:
        c.execute(text("ALTER TABLE cfg_hero_base_hp ADD COLUMN must_have INT NOT NULL"))
    rel = okd(client.get(f"/api/pipelines/{pid}/releases"))
    pre = okd(client.post(f"/api/pipelines/{pid}/rollback/preview", json={"targetReleaseId": rel["rows"][1]["id"]}))
    assert not pre["compatible"]


# -------- T18 口令 / 设置 / 审计 --------
def test_f16_op_token_lock(client, biz):
    bad = {"X-CF-Op-Token": "wrong", "X-CF-Operator": "x"}
    codes = [client.put("/api/settings", json={"guard.maxDeleteRatio": 0.2}, headers=bad).json()["code"] for _ in range(6)]
    assert codes[:4] == ["OP_TOKEN_INVALID"] * 4 and codes[4] == "OP_TOKEN_LOCKED" and codes[5] == "OP_TOKEN_LOCKED"
    r = client.put("/api/settings", json={"guard.maxDeleteRatio": 0.2}, headers={"X-CF-Op-Token": "test-op-token"})
    assert r.status_code == 403  # 被锁定 / 缺少操作人


def test_f17_settings(client, biz):
    s = okd(client.get("/api/settings"))
    assert next(x for x in s if x["key"] == "guard.maxDeleteRatio")["value"] == 0.3
    r = client.put("/api/settings", json={"guard.maxDeleteRatio": 1.5}, headers=OP)
    assert r.status_code == 400
    okd(client.put("/api/settings", json={"guard.maxDeleteRatio": 0.2}, headers=OP))
    x = next(x for x in okd(client.get("/api/settings")) if x["key"] == "guard.maxDeleteRatio")
    assert x["value"] == 0.2 and x["changed"] and x["updatedBy"] == "管理员"
    log = okd(client.get("/api/audit-logs?action=EDIT_SETTING"))["rows"][0]
    assert log["detail"]["guard.maxDeleteRatio"] == {"before": 0.3, "after": 0.2}


def test_f17_3_binding_override(client, biz):
    dsl = d.hero_dsl()
    next(n for n in dsl["nodes"] if n["id"] == "sink_reward")["config"]["binding"]["guards"] = {"maxDeleteRatio": 0.9,
                                                                                                "maxRowChangeRatio": 0.9}
    env = setup_pipeline(client, dsl=dsl)
    submit(client, env["app"], fixtures.hero_config(extra_reward_rows=10))
    j = job(client, submit(client, env["app"], fixtures.hero_config()).json()["data"]["jobId"])
    assert j["status"] == "PUBLISHED"


# -------- 任务列表 / 待处理 --------
def test_f13_job_list_and_pending(client, biz):
    env = setup_pipeline(client)
    submit(client, env["app"], fixtures.hero_config(total_count=1))
    assert okd(client.get("/api/jobs/pending-count"))["count"] == 1
    lst = okd(client.get("/api/jobs?view=pending"))
    assert lst["rows"][0]["status"] == "FAILED_VALIDATION" and lst["rows"][0]["client"] == "业务服务A"
    submit(client, env["app"], fixtures.hero_config())
    assert okd(client.get("/api/jobs/pending-count"))["count"] == 0  # 之后已有成功写表
    lst = okd(client.get(f"/api/jobs?pipelineId={env['pipeline']['id']}"))
    assert lst["total"] == 2 and lst["statusCounts"]["PUBLISHED"] == 1


# -------- T07 试跑接口 --------
def test_f8_test_run_api(client, biz):
    env = setup_pipeline(client)
    pid = env["pipeline"]["id"]
    t = okd(client.post("/api/jobs/test", json={"pipelineId": pid, "preview": True}, headers=WHO))
    assert t["mode"] == "TEST" and t["result"]["sampled"]
    full = okd(client.post("/api/jobs/test", json={"pipelineId": pid}, headers=WHO))
    fc = full["result"]["forecast"]
    assert {x["table"] for x in fc["tables"]} == {"cfg_hero_base_hp", "cfg_level_reward", "cfg_global_switch"}
    page = okd(client.get(f"/api/jobs/{full['jobId']}/nodes/join_reward_item/ports/out_main/rows?limit=2"))
    assert page["total"] == 5 and page["rows"][0]["_lineage"]["itemName"] == "道具表!B2"
    assert full["metrics"]["locateReport"]
    recent = okd(client.get(f"/api/pipelines/{pid}/recent-files"))
    assert recent == []
    submit(client, env["app"], fixtures.hero_config())
    recent = okd(client.get(f"/api/pipelines/{pid}/recent-files"))
    t2 = okd(client.post("/api/jobs/test", json={"pipelineId": pid, "fileId": recent[0]["fileId"]}, headers=WHO))
    assert t2["status"] == "VALIDATED"


# -------- T19 清理 --------
def test_f14_7_cleanup_keeps_live_and_baseline(client, biz):
    import datetime as dt

    from cellflow.services import cleanup, settings

    env = setup_pipeline(client)
    pid = env["pipeline"]["id"]
    for k in range(4):
        submit(client, env["app"], fixtures.hero_config(hp_overrides={"战士": [100 + k, 120, 150, 180]}))
    settings.update({"retention.releases": 1, "retention.days": 1}, "t")
    stats = cleanup.run_cleanup(dt.datetime.now() + dt.timedelta(days=3))
    assert stats["snapshots"] >= 1
    rel = okd(client.get(f"/api/pipelines/{pid}/releases"))
    avail = {x["kind"]: x["available"] for x in rel["rows"] if x["live"] or x["kind"] == "BASELINE"}
    assert all(avail.values())
    expired = [x for x in rel["rows"] if not x["available"]]
    assert expired
    r = client.post(f"/api/pipelines/{pid}/rollback/preview", json={"targetReleaseId": expired[0]["id"]})
    assert r.status_code == 410


def test_decimal_values_stable_across_snapshots(client, biz):
    """decimal 列（如 1.50、10）经快照存取后比对不应被当成修改：同一文件再次提交为无变化。"""
    from test_perf import big_file, perf_dsl

    with biz.begin() as c:
        c.execute(text("""CREATE TABLE perf_items (id BIGINT PRIMARY KEY, name VARCHAR(64) NOT NULL, lv INT, cnt INT,
                          price DECIMAL(12,2), total DOUBLE) CHARSET=utf8mb4"""))
    data = big_file(rows=30)
    env = setup_pipeline(client, dsl=perf_dsl(), data=data)
    assert job(client, submit(client, env["app"], data).json()["data"]["jobId"])["status"] == "PUBLISHED"
    j = job(client, submit(client, env["app"], data).json()["data"]["jobId"])
    assert j["status"] == "NO_CHANGE", j["result"]


def test_open_api_security_boundaries(client, biz):
    """签名时间窗、错误密钥、调用方之间的任务隔离。"""
    import time as _t
    import uuid as _u

    from cellflow.services.auth import sign

    env = setup_pipeline(client)
    app = env["app"]
    path = "/open/v1/pipelines/hero_config"

    def call(ts, secret="test-app-secret", key=app["appKey"], p=path):
        nonce = _u.uuid4().hex
        return client.get(p, headers={"X-CF-AppKey": key, "X-CF-Timestamp": str(ts), "X-CF-Nonce": nonce,
                                      "X-CF-Signature": sign(secret, "GET", p, str(ts), nonce, b"")})

    now = int(_t.time())
    assert call(now).status_code == 200
    assert call(now - 3600).status_code == 401  # 过期的时间戳即使签名正确也拒绝
    assert call(now, secret="wrong-secret").status_code == 401
    jid = submit(client, app, fixtures.hero_config()).json()["data"]["jobId"]
    other = okd(client.post("/api/client-apps", json={"name": "业务服务B", "secretRef": "test_app_secret",
                                                      "allowedPipelines": ["hero_config"]}, headers=OP))
    assert call(now, key=other["appKey"], p=f"/open/v1/jobs/{jid}").status_code == 404  # 看不到别的调用方的任务
    assert call(now, p=f"/open/v1/jobs/{jid}").status_code == 200


def test_datasource_direct_mode(client, biz):
    """控制台直接填写连接信息：口令加密存储、接口不返回、编辑时留空不修改、可连通并用于写表检查。"""
    from urllib.parse import urlparse

    from conftest import BIZ_DB, MYSQL_ROOT

    from cellflow.meta.db import get_engine

    u = urlparse(MYSQL_ROOT.replace("mysql+pymysql", "mysql"))
    body = {"name": "直连库", "mode": "DIRECT", "host": u.hostname, "port": u.port or 3306, "username": u.username,
            "password": u.password, "dbName": BIZ_DB}
    d = okd(client.post("/api/datasources", json=body, headers=OP))
    assert d["mode"] == "DIRECT" and d["hasPassword"] and "password" not in d and "password_enc" not in d
    with get_engine().connect() as c:
        stored = c.execute(text("SELECT password_enc FROM cf_datasource WHERE id=:i"), {"i": d["id"]}).scalar()
    assert stored and u.password not in stored  # 只存密文
    t = okd(client.post(f"/api/datasources/{d['id']}/test"))
    assert t["checks"]["connect"] and t["checks"]["CREATE"], t
    okd(client.put(f"/api/datasources/{d['id']}", json={**body, "password": None, "name": "直连库2"}, headers=OP))
    assert okd(client.post(f"/api/datasources/{d['id']}/test"))["checks"]["connect"]  # 留空口令 = 不修改
    tables = okd(client.get(f"/api/datasources/{d['id']}/tables"))
    assert "cfg_hero_base_hp" in str(tables)
    bad = client.post("/api/datasources", json={**body, "name": "缺口令", "password": None}, headers=OP)
    assert bad.status_code == 400
    lst = client.get("/api/datasources").text
    assert u.password not in lst


def test_create_target_table_from_fields(client, biz):
    """按上游字段新建目标表（需口令、留审计）→ 绑定 → 发布 → 写入。"""
    env = setup_pipeline(client, publish=False)
    ds = env["datasource"]["id"]
    fields = [{"field": "rewardId", "type": "long"}, {"field": "job", "type": "string"}, {"field": "level", "type": "int"},
              {"field": "itemId", "type": "long"}, {"field": "count", "type": "int"}, {"field": "itemName", "type": "string"}]
    prop = okd(client.post(f"/api/datasources/{ds}/tables/propose", json={"fields": fields, "keyFields": ["rewardId"]}))
    assert [c["name"] for c in prop["columns"]] == ["reward_id", "job", "level", "item_id", "count", "item_name"]
    assert prop["primaryKey"] == ["reward_id"] and prop["columns"][0]["sqlType"] == "BIGINT"
    body = {"table": "auto_level_reward", "columns": prop["columns"], "primaryKey": prop["primaryKey"]}
    ddl = okd(client.post(f"/api/datasources/{ds}/tables/ddl", json=body))["ddl"]
    assert "PRIMARY KEY (`reward_id`)" in ddl and "`reward_id` BIGINT NOT NULL" in ddl
    assert client.post(f"/api/datasources/{ds}/tables", json=body).status_code == 403  # 需要口令
    created = okd(client.post(f"/api/datasources/{ds}/tables", json=body, headers=OP))
    assert [c["name"] for c in created["table"]["columns"]][:2] == ["reward_id", "job"]
    assert client.post(f"/api/datasources/{ds}/tables", json=body, headers=OP).json()["code"] == "TABLE_EXISTS"
    bad = client.post(f"/api/datasources/{ds}/tables/ddl", json={**body, "table": "x; drop table y"})
    assert bad.status_code == 400
    bad = client.post(f"/api/datasources/{ds}/tables/ddl", json={**body, "columns": [{**prop["columns"][0], "sqlType": "INT; DROP"}]})
    assert bad.status_code == 400
    # 无主键：自动加自增 id
    ddl2 = okd(client.post(f"/api/datasources/{ds}/tables/ddl", json={"table": "t2", "columns": prop["columns"], "primaryKey": []}))["ddl"]
    assert "`id` BIGINT NOT NULL AUTO_INCREMENT" in ddl2 and "PRIMARY KEY (`id`)" in ddl2
    # 绑定新表、发布、写入
    pid = env["pipeline"]["id"]
    draft = okd(client.get(f"/api/pipelines/{pid}/draft"))
    dsl = draft["dsl"]
    for n in dsl["nodes"]:
        if n["id"] == "sink_reward":
            n["config"]["binding"]["table"] = "auto_level_reward"
            n["config"]["binding"]["columnMapping"] = [{"field": c["field"], "column": c["name"]} for c in prop["columns"]]
    saved = okd(client.put(f"/api/pipelines/{pid}/draft", json={"dsl": dsl, "draftVersion": draft["draftVersion"]}, headers=WHO))
    okd(client.post(f"/api/pipelines/{pid}/publish", json={"draftVersion": saved["draftVersion"]}, headers=OP))
    j = job(client, submit(client, env["app"], fixtures.hero_config()).json()["data"]["jobId"])
    assert j["status"] == "PUBLISHED", j
    got = rows(biz, "auto_level_reward", "reward_id")
    assert len(got) == 5 and got[0]["item_name"] == "金币"
    logs = okd(client.get("/api/audit-logs?action=CREATE_TABLE"))["rows"]
    assert logs and "auto_level_reward" in str(logs[0]["detail"])
