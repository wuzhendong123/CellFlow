"""按分区替换：每天一个文件写同一张表，只替换本批数据涉及的分区；安全闸、撤销、恢复都按分区。"""

import dsl_examples as d
import fixtures
from flow import OP, okd, rows, setup_pipeline, submit
from sqlalchemy import text


def partition_dsl():
    dsl = d.hero_dsl()
    parts = {"sink_hp": ["job"], "sink_reward": ["job"], "sink_global": ["maxOpenDays"]}
    for n in dsl["nodes"]:
        if n["type"] == "SINK":
            n["config"]["binding"]["strategy"] = "PARTITION"
            n["config"]["binding"]["partitionFields"] = parts[n["id"]]
    return dsl


def job(client, r):
    assert r.status_code == 202, r.text
    return okd(client.get(f"/api/jobs/{r.json()['data']['jobId']}"))


def hp(biz):
    return {(x["job_name"], x["lv"]): x["base_hp"] for x in rows(biz, "cfg_hero_base_hp")}


def test_partition_keeps_other_partitions_and_replaces_same(client, biz):
    with biz.begin() as c:
        c.execute(text("INSERT INTO cfg_hero_base_hp (job_name, lv, base_hp) VALUES ('刺客', 1, 77), ('刺客', 2, 88)"))
    env = setup_pipeline(client, dsl=partition_dsl())
    pid = env["pipeline"]["id"]
    j1 = job(client, submit(client, env["app"], fixtures.hero_config()))
    assert j1["status"] == "PUBLISHED", j1
    h = hp(biz)
    assert h[("刺客", 1)] == 77 and h[("刺客", 2)] == 88 and h[("战士", 1)] == 100
    total = len(h)
    # 同一批（同一分区）再提交：替换，不重复
    j2 = job(client, submit(client, env["app"], fixtures.hero_config(hp_overrides={"战士": [101, 120, 150, 180]})))
    assert j2["status"] == "PUBLISHED", j2
    h = hp(biz)
    assert len(h) == total and h[("战士", 1)] == 101 and h[("刺客", 1)] == 77
    rel = okd(client.get(f"/api/pipelines/{pid}/releases"))
    assert [x["kind"] for x in rel["rows"]] == ["NORMAL", "NORMAL"]
    assert rel["rows"][0]["strategy"] == "PARTITION" and rel["rows"][0]["undoable"]
    assert ["战士"] in rel["rows"][0]["partitions"]["cfg_hero_base_hp"]
    # 整表回滚不适用
    r = client.post(f"/api/pipelines/{pid}/rollback/preview", json={"targetReleaseId": rel["rows"][1]["id"]})
    assert r.json()["code"] == "USE_PARTITION_UNDO"
    # 先撤销早的一次：后面的写入也改过同分区，拒绝
    r = client.post(f"/api/pipelines/{pid}/releases/{rel['rows'][1]['id']}/undo", json={"reason": "x"}, headers=OP)
    assert r.status_code == 409 and r.json()["code"] == "LATER_WRITE_EXISTS"
    # 撤销最近一次：恢复成写入前（战士 100），其他分区不动
    okd(client.post(f"/api/pipelines/{pid}/releases/{rel['rows'][0]['id']}/undo", json={"reason": "数值填错"}, headers=OP))
    h = hp(biz)
    assert h[("战士", 1)] == 100 and h[("刺客", 1)] == 77 and len(h) == total
    rel = okd(client.get(f"/api/pipelines/{pid}/releases"))
    assert rel["rows"][0]["kind"] == "ROLLBACK" and rel["rows"][1]["undone"] and not rel["rows"][1]["undoable"]
    r = client.post(f"/api/pipelines/{pid}/releases/{rel['rows'][1]['id']}/undo", json={"reason": "x"}, headers=OP)
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_UNDONE"


def test_partition_guard_scoped(client, biz):
    """安全闸只和同分区的现有数据比；其他分区的大量数据不算删除。"""
    with biz.begin() as c:
        for i in range(50):
            c.execute(text("INSERT INTO cfg_hero_base_hp (job_name, lv, base_hp) VALUES (:j, 1, 1)"), {"j": f"其他{i}"})
    env = setup_pipeline(client, dsl=partition_dsl())
    assert job(client, submit(client, env["app"], fixtures.hero_config()))["status"] == "PUBLISHED"
    assert len([k for k in hp(biz) if k[0].startswith("其他")]) == 50


def test_mixed_strategy_rejected_and_fields_required(client, biz):
    dsl = partition_dsl()
    sinks = [n for n in dsl["nodes"] if n["type"] == "SINK"]
    sinks[0]["config"]["binding"]["strategy"] = "SWAP"
    env = setup_pipeline(client, dsl=dsl, publish=False)
    r = client.post(f"/api/pipelines/{env['pipeline']['id']}/publish", json={"draftVersion": env["draftVersion"], "note": "x"}, headers=OP)
    assert r.status_code >= 400, r.text
    ds = env["datasource"]["id"]
    from cellflow.services import datasources

    b = {"table": "cfg_hero_base_hp", "strategy": "PARTITION", "partitionFields": [], "keyFields": ["job", "level"],
         "columnMapping": [{"field": "job", "column": "job_name"}]}
    res = datasources.check_binding(ds, b, [{"field": "job", "type": "STRING"}])
    assert any(e["code"] == "PARTITION_FIELDS_REQUIRED" for e in res["errors"]), res


def test_partition_every_job_executes_not_latest_only(client, biz, monkeypatch):
    """按分区替换：一个文件是一批，连续提交的每个任务都执行（按提交顺序串行），不会被「只执行最新」作废。"""
    import cellflow.services.jobs as jobs_mod
    from cellflow.runtime.executor import run_job
    from cellflow.services import client_apps

    env = setup_pipeline(client, dsl=partition_dsl())
    monkeypatch.setattr("cellflow.runtime.queue.dispatch", lambda jid: None)
    app = client_apps.get(env["app"]["id"])
    a = jobs_mod.submit(app, {"pipelineCode": "hero_config"}, fixtures.hero_config(), "a.xlsx")["jobId"]
    b = jobs_mod.submit(app, {"pipelineCode": "hero_config"}, fixtures.hero_config(hp_overrides={"战士": [5, 6, 7, 8]}), "b.xlsx")["jobId"]
    assert okd(client.get(f"/api/jobs/{a}"))["status"] == "QUEUED"
    assert run_job(a) == "PUBLISHED"
    assert run_job(b) == "PUBLISHED"
    assert hp(biz)[("战士", 1)] == 5
