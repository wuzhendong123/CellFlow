"""T06/T07 DSL 校验与执行器，T12 表达式与派生列，T13 关联，T14 校验（PRD F4、F5、F6、F8）。"""

import copy

import dsl_examples as d
import fixtures

from cellflow.engine.dag import analyze, execute, reference_source_ports
from cellflow.engine.grid import Workbook
from cellflow.services.settings import DEFAULTS

S = {k: v[0] for k, v in DEFAULTS.items()}


def node(dsl, nid):
    return next(n for n in dsl["nodes"] if n["id"] == nid)


def run(dsl, data=None, **kw):
    return execute(dsl, Workbook(data or fixtures.hero_config()), S, **kw)


def errs(r, code=None):
    return [i for i in r.issues if i["severity"] == "ERROR" and (code is None or i["code"] == code)]


def a_errs(dsl):
    return [e["code"] for e in analyze(dsl).errors]


# -------- T06 静态校验 --------
def test_example_dsl_is_valid_and_schemas_inferred():
    a = analyze(d.hero_dsl())
    assert a.ok, a.errors
    cols = [c.field for c in a.schemas[("join_reward_item", "out_main")].columns]
    assert cols == ["rewardId", "job", "level", "itemId", "count", "itemName", "quality"]
    assert a.schemas[("src_hero", "out_global")].single_row is True
    assert a.schemas[("derive_hp", "out")].get("hp").type == "int"


def test_f4_5_cycle_rejected():
    dsl = d.hero_dsl()
    dsl["edges"].append(d.e("x", "val_reward", "out_pass", "join_reward_item", "in_right"))
    assert "DSL_CYCLE" in a_errs(dsl)


def test_f4_5a_source_has_no_inputs():
    dsl = d.hero_dsl()
    dsl["edges"].append(d.e("x", "src_hero", "out_reward", "src_item", "in"))
    assert "DSL_INVALID" in a_errs(dsl)


def test_f4_4_upstream_field_removed_reported():
    dsl = d.hero_dsl()
    d.region(node(dsl, "src_hero"), "rg_reward")["columns"] = [
        c for c in d.region(node(dsl, "src_hero"), "rg_reward")["columns"] if c["field"] != "count"]
    codes = a_errs(dsl)
    assert "FIELD_NOT_FOUND" in codes


def test_param_port_requires_single_row():
    dsl = d.hero_dsl()
    for e in dsl["edges"]:
        if e["id"] == "e9":
            e["source"]["portId"] = "out_reward"
    assert "PARAM_NOT_SINGLE_ROW" in a_errs(dsl)


def test_unconnected_required_port():
    dsl = d.hero_dsl()
    dsl["edges"] = [e for e in dsl["edges"] if e["id"] != "e8"]
    assert "PORT_NOT_CONNECTED" in a_errs(dsl)


def test_source_must_not_carry_file():
    dsl = d.hero_dsl()
    node(dsl, "src_hero")["config"]["fileId"] = 1
    assert "DSL_INVALID" in a_errs(dsl)


# -------- T07 执行器 --------
def test_full_run_no_errors_and_lineage():
    r = run(d.hero_dsl())
    assert not errs(r), errs(r)
    rw = r.sinks["level_reward"][1]
    assert rw.lineage[0]["itemName"] == "道具表!B2"
    assert rw.meta[0]["rid"] == "src_hero/rg_reward/00001+src_item/rg_item/00001"


def test_structural_failure_skips_only_downstream():
    r = run(d.hero_dsl(), fixtures.hero_config(rename_reward_anchor="奖励编号"))
    assert errs(r, "ANCHOR_NOT_FOUND")
    assert "sink_reward" in r.skipped and "val_reward" in r.skipped
    assert "hero_base_hp" in r.sinks and "global_switch" in r.sinks


def test_f8_3_preview_samples_but_not_reference_inputs():
    dsl = d.hero_dsl()
    assert ("src_item", "out_item") in reference_source_ports(dsl)
    assert ("src_hero", "out_global") in reference_source_ports(dsl)
    data = fixtures.hero_config(item_rows=[(5000 + i, f"道具{i}", "普通") for i in range(1, 30)])
    r = run(dsl, data, sample_rows=2)
    assert len(r.outputs[("src_hero", "out_reward")]) == 2
    assert len(r.outputs[("src_item", "out_item")]) == 29  # 被引用的字典不采样
    assert not errs(r, "RULE_VIOLATION")


def test_until_node():
    r = run(d.hero_dsl(), until_node="join_reward_item")
    assert ("join_reward_item", "out_main") in r.outputs and "level_reward" not in r.sinks


# -------- T12 表达式 / 派生列 / 过滤 --------
def derive_dsl(columns, on_error="ERROR"):
    dsl = d.hero_dsl()
    node(dsl, "derive_hp")["config"]["columns"] = columns
    node(dsl, "derive_hp")["config"]["onError"] = on_error
    node(dsl, "sink_hp")["config"]["binding"]["columnMapping"] = [{"field": "job", "column": "job_name"}]
    return dsl


def test_f4_8_9_derive_chain():
    r = run(derive_dsl([{"field": "tag", "expr": "job + '_' + string(level)", "type": "string"},
                        {"field": "code", "expr": "'R' + tag", "type": "string"}]))
    row = r.sinks["hero_base_hp"][1].rows[0]
    assert row["tag"] == "战士_1" and row["code"] == "R战士_1"


def test_f4_10_forward_reference_rejected():
    dsl = derive_dsl([{"field": "a", "expr": "b + 1", "type": "int"}, {"field": "b", "expr": "level", "type": "int"}])
    assert "DSL_INVALID" in a_errs(dsl)


def test_f4_11_same_name_requires_replace():
    assert "DSL_INVALID" in a_errs(derive_dsl([{"field": "level", "expr": "level * 2", "type": "int"}]))
    r = run(derive_dsl([{"field": "level", "expr": "level * 2", "type": "int", "mode": "REPLACE"}]))
    assert r.sinks["hero_base_hp"][1].rows[0]["level"] == 2


def test_f4_12_null_and_coalesce():
    r = run(derive_dsl([{"field": "x", "expr": "coalesce(params.global.activityId, 'none')", "type": "string"}]))
    assert r.sinks["hero_base_hp"][1].rows[0]["x"] == "ACT-2026-09"


def test_f4_13_eval_error_located():
    r = run(derive_dsl([{"field": "x", "expr": "baseHp / (level - 1)", "type": "int"}]))
    e = errs(r, "DERIVE_EVAL_FAILED")
    assert e and e[0]["cell"].startswith("角色配置!") and len(r.sinks) == 3
    assert all(row["level"] != 1 for row in r.sinks["hero_base_hp"][1].rows)
    r2 = run(derive_dsl([{"field": "x", "expr": "baseHp / (level - 1)", "type": "int"}], on_error="NULL"))
    assert not errs(r2) and any(row["x"] is None for row in r2.sinks["hero_base_hp"][1].rows)


def test_f4_14_16a_type_errors_with_fix():
    a = analyze(derive_dsl([{"field": "x", "expr": "job", "type": "int"}]))
    assert any(e["code"] == "EXPR_INVALID" for e in a.errors)
    a = analyze(derive_dsl([{"field": "x", "expr": "int(baseHp * params.global.hpRate)", "type": "int"}]))
    e = [x for x in a.errors if x["code"] == "EXPR_INVALID"][0]
    assert e["hint"] and e["fix"] == "int(double(baseHp) * params.global.hpRate)"


def test_f4_15_16_params():
    r = run(d.hero_dsl())
    assert r.sinks["hero_base_hp"][1].rows[0]["hp"] == 150
    assert "EXPR_INVALID" in a_errs(derive_dsl([{"field": "x", "expr": "params.global.notExist", "type": "string"}]))


def test_filter_node():
    dsl = d.hero_dsl(with_derive=False)
    dsl["nodes"].append({"id": "f", "type": "FILTER", "config": {"expr": "baseHp >= 100"}})
    dsl["edges"] = [x for x in dsl["edges"] if x["id"] != "e1"] + [
        d.e("f1", "src_hero", "out_hp", "f", "in"), d.e("f2", "f", "out", "sink_hp", "in")]
    r = run(dsl)
    assert all(row["baseHp"] >= 100 for row in r.sinks["hero_base_hp"][1].rows)
    dsl["nodes"][-1]["config"]["expr"] = "baseHp"
    assert "EXPR_INVALID" in a_errs(dsl)


def test_select_union_lookup():
    dsl = {"nodes": [d.src_hero(), d.src_item(),
                     {"id": "sel", "type": "SELECT_RENAME", "config": {"columns": [{"field": "itemId", "as": "id"}, {"field": "name"}]}},
                     {"id": "u", "type": "UNION", "config": {}},
                     {"id": "lk", "type": "LOOKUP", "config": {"on": [{"left": "itemId", "right": "itemId"}],
                                                              "select": [{"field": "name", "as": "itemName"}], "onMissing": "WARN"}},
                     d.sink("s1", "u_out", "t1", None, [("id", "id")]), d.sink("s2", "lk_out", "t2", None, [("itemName", "n")])],
           "edges": [d.e("1", "src_item", "out_item", "sel", "in"), d.e("2", "sel", "out", "u", "in"),
                     d.e("3", "sel", "out", "u", "in"), d.e("4", "u", "out", "s1", "in"),
                     d.e("5", "src_hero", "out_reward", "lk", "in"), d.e("6", "src_item", "out_item", "lk", "in_dict"),
                     d.e("7", "lk", "out", "s2", "in")]}
    assert analyze(dsl).ok, analyze(dsl).errors
    r = run(dsl)
    assert len(r.sinks["u_out"][1]) == 6 and r.sinks["u_out"][1].rows[0] == {"id": 5001, "name": "金币"}
    assert r.sinks["lk_out"][1].rows[0]["itemName"] == "金币"


# -------- T13 关联 --------
def test_f5_1_left_join():
    r = run(d.hero_dsl())
    assert len(r.sinks["level_reward"][1]) == 5


def test_f5_2_conflict_auto_prefix():
    dsl = d.hero_dsl()
    j = node(dsl, "join_reward_item")["config"]
    j["conflictPolicy"]["aliases"] = {}
    j["select"] = ["reward.*", "item.*"]
    d.region(node(dsl, "src_hero"), "rg_reward")["columns"].append({"source": "职业", "field": "name", "type": "string"})
    a = analyze(dsl)
    names = [c.field for c in a.schemas[("join_reward_item", "out_main")].columns]
    assert "reward_name" in names and "item_name" in names
    assert any(w["code"] == "COL_CONFLICT_RESOLVED" for w in a.warnings)


def test_f5_3_right_key_not_unique():
    r = run(d.hero_dsl(), fixtures.hero_config(item_rows=[(5001, "金币", "普通"), (5001, "金币2", "普通"), (5002, "钻石", "稀有"), (5003, "x", "史诗")]))
    e = errs(r, "JOIN_KEY_NOT_UNIQUE")
    assert e and "5001" in e[0]["message"]


def test_f5_4_explosion_blocked():
    dsl = d.hero_dsl()
    j = node(dsl, "join_reward_item")["config"]
    j["on"] = [{"left": "job", "right": "name"}]
    j["expectedCardinality"] = "MANY_TO_MANY"
    j["explosionGuard"] = {"maxOutputRows": 3}
    items = [(5001 + i, "战士", "普通") for i in range(10)]
    r = run(dsl, fixtures.hero_config(item_rows=items))
    e = errs(r, "JOIN_EXPLOSION")
    assert e and e[0]["data"]["estimated"] == 23  # 20 行匹配 + 左连接 3 行未匹配
    assert e[0]["data"]["topKeys"] == [{"key": ["战士"], "rows": 20}]


def test_f5_5_unmatched_side_output():
    r = run(d.hero_dsl(), fixtures.hero_config(reward_overrides={1005: (1005, "射手", 1, 9999, 12)}))
    assert len(r.outputs[("join_reward_item", "out_unmatched")]) == 1
    assert any(i["code"] == "JOIN_UNMATCHED" and i["cell"] == "角色配置!D22" for i in r.issues)


def test_f5_6_mixed_key_types_match():
    dsl = d.hero_dsl()
    d.region(node(dsl, "src_item"), "rg_item")["columns"][0]["type"] = "string"
    a = analyze(dsl)
    assert any(w["code"] == "JOIN_KEY_TYPE_MISMATCH" for w in a.warnings)
    r = run(dsl)
    assert len(r.sinks["level_reward"][1]) == 5


def test_f5_7_8_9_broadcast():
    dsl = d.hero_dsl()
    dsl["nodes"].append({"id": "bc", "type": "JOIN", "config": {
        "joinType": "BROADCAST", "leftAlias": "reward", "rightAlias": "global",
        "select": ["reward.*", "global.activityId", "global.cfgVersion"]}})
    dsl["edges"] = [x for x in dsl["edges"] if x["id"] != "e6"] + [
        d.e("b1", "val_reward", "out_pass", "bc", "in_left"), d.e("b2", "src_hero", "out_global", "bc", "in_right"),
        d.e("b3", "bc", "out_main", "sink_reward", "in")]
    r = run(dsl)
    rows = r.sinks["level_reward"][1]
    assert len(rows) == 5 and all(x["activityId"] == "ACT-2026-09" for x in rows.rows)
    assert rows.lineage[0]["activityId"] == "角色配置!B4"
    bad = copy.deepcopy(dsl)
    node(bad, "bc")["config"]["select"] = ["reward.*", "global.name"]
    for x in bad["edges"]:
        if x["id"] == "b2":
            x["source"] = {"nodeId": "src_item", "portId": "out_item"}
    assert any(w["code"] == "BROADCAST_RIGHT_NOT_SINGLE" for w in analyze(bad).warnings)
    r = run(bad)
    assert errs(r, "PARAM_NOT_SINGLE_ROW")


# -------- T14 校验 --------
def test_f6_1_2_3_rules_locate_cells():
    data = fixtures.hero_config(reward_overrides={1002: (1002, "战士", 2, 5002, 0)}, total_count=35)
    r = run(d.hero_dsl(), data)
    v = [i for i in r.issues if i["code"] == "RULE_VIOLATION"]
    by_rule = {i["rule"]: i for i in v}
    assert by_rule["r2"]["cell"] == "角色配置!E19" and by_rule["r2"]["message"] == "奖励数量必须大于 0"
    assert "level_reward" in r.sinks


def test_f6_3_foreign_key():
    dsl = d.hero_dsl()
    node(dsl, "join_reward_item")["config"]["unmatchedPolicy"] = "KEEP_NULLS"
    r = run(dsl, fixtures.hero_config(reward_overrides={1005: (1005, "射手", 1, 9999, 12)}))
    fk = [i for i in r.issues if i.get("rule") == "r3"]
    assert fk and fk[0]["cell"] == "角色配置!D22"
    assert len(r.outputs[("val_reward", "out_reject")]) == 1


def test_f6_3a_fk_requires_connected_ref():
    dsl = d.hero_dsl()
    node(dsl, "val_reward")["config"]["refs"] = {}
    dsl["edges"] = [x for x in dsl["edges"] if x["id"] != "e8"]
    assert "REF_NOT_CONNECTED" in a_errs(dsl)


def test_f6_3b_delete_referenced_source():
    dsl = d.hero_dsl()
    dsl["nodes"] = [n for n in dsl["nodes"] if n["id"] != "src_item"]
    dsl["edges"] = [x for x in dsl["edges"] if x["source"]["nodeId"] != "src_item"]
    codes = a_errs(dsl)
    assert "PORT_NOT_CONNECTED" in codes


def test_f6_4_unique_warn_does_not_block():
    data = fixtures.hero_config(reward_overrides={1002: (1002, "战士", 1, 5001, 20)})
    r = run(d.hero_dsl(), data)
    u = [i for i in r.issues if i.get("rule") == "r4"]
    assert len(u) == 2 and all(i["severity"] == "WARN" for i in u)
    assert len(r.sinks["level_reward"][1]) == 5


def test_f6_5_reconcile():
    r = run(d.hero_dsl(), fixtures.hero_config(total_count=50))
    e = errs(r, "RECONCILE_MISMATCH")
    assert e and e[0]["cell"] == "角色配置!E23" and "55" in e[0]["message"] and "50" in e[0]["message"]


def test_f6_6_all_problems_in_one_run():
    data = fixtures.hero_config(reward_overrides={1001: (1001, "战士", 1, 9999, 0), 1003: (1003, "法师", 1, 5001, 0)},
                                total_count=1)
    dsl = d.hero_dsl()
    node(dsl, "join_reward_item")["config"]["unmatchedPolicy"] = "KEEP_NULLS"
    r = run(dsl, data)
    assert len(errs(r)) >= 4


def test_duplicate_primary_key_in_sink():
    r = run(d.hero_dsl(), fixtures.hero_config(reward_overrides={1002: (1001, "战士", 2, 5002, 20)}))
    e = errs(r, "DUPLICATE_KEY")
    assert e and len(e[0]["related"]) == 2


def test_expr_result_type_for_native_arithmetic():
    """cel-python 的 double * double 返回 Python float，结果类型仍应推断为 float（而非 string）。"""
    from cellflow.engine.expr import FieldInfo, compile_expr

    f = {"price": FieldInfo("decimal"), "cnt": FieldInfo("int")}
    assert compile_expr("double(price) * double(cnt)", f).result_type == "float"
    assert compile_expr("cnt * 2", f).result_type == "long"
    assert compile_expr("double(cnt) + 1.0 > 2.0", f).result_type == "bool"


def test_expr_compiled_runner_matches_interpreter_and_is_thread_safe():
    """转译执行与解释执行结果一致；不能转译的表达式回退；多线程并发求值互不串行。"""
    import threading

    import celpy

    from cellflow.engine import expr as E

    f = {"a": E.FieldInfo("int"), "b": E.FieldInfo("float"), "s": E.FieldInfo("string")}
    exprs = ["a * 2 + 1", "double(a) / b", "s.startsWith('x') ? a : -a", "size(s) > 2 && b >= 1.5", "string(a) + s",
             "coalesce(s, 'n')", "a % 3 == 0 ? 'fizz' : s"]
    rows = [{"a": i, "b": 1.5 + i, "s": f"x{i}" if i % 2 else f"y{i}"} for i in range(40)]
    for x in exprs:
        p = E.compile_expr(x, f)
        interp = E.celpy.InterpretedRunner(E._ENV, E._ENV.compile(x), E._FUNCS)
        for r in rows:
            assert p.evaluate(r) == E.from_cel(interp.evaluate({k: E.to_cel(v) for k, v in r.items()})), (x, r)
    assert E.compile_expr("coalesce(s, 'n')", f).prog.compiled is False
    with __import__("pytest").raises(E.ExprError, match="除数为 0"):
        E.compile_expr("a / (a - a)", f).evaluate({"a": 3})
    assert isinstance(celpy.CELEvalError, type)

    p = E.compile_expr("a * 10 + 1", f)
    errors = []

    def work(base):
        for i in range(base, base + 300):
            if p.evaluate({"a": i}) != i * 10 + 1:
                errors.append(i)

    ts = [threading.Thread(target=work, args=(k * 1000,)) for k in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors


def test_expression_check_preview_uses_param_values():
    """表达式快速预览带上参数行样例：params.x 不再一律为空。"""
    from fastapi.testclient import TestClient

    from cellflow.api.main import create_app

    c = TestClient(create_app())
    body = {"expr": "int(double(baseHp) * params.global.hpRate)", "fields": {"baseHp": "int"},
            "params": {"global": {"hpRate": "float"}}, "sampleRows": [{"baseHp": 100}, {"baseHp": 60}]}
    r = c.post("/api/expressions/check", json={**body, "paramValues": {"global": {"hpRate": 1.5}}}).json()["data"]
    assert [x["value"] for x in r["preview"]] == [150, 90]
    r = c.post("/api/expressions/check", json=body).json()["data"]
    assert [x["value"] for x in r["preview"]] == [None, None]  # 没有参数样例时仍按空值传递


def test_every_catalog_function_same_under_compiled_runner():
    """函数目录里每个函数：转译执行（必要时自动回退）与解释执行结果一致。"""
    import datetime as dt

    from cellflow.engine import expr as E

    f = {"s": E.FieldInfo("string"), "n": E.FieldInfo("int"), "x": E.FieldInfo("float"), "d": E.FieldInfo("date")}
    row = {"s": " a,b ", "n": 7, "x": 2.345, "d": dt.date(2026, 3, 1)}
    calls = {
        "len": "len(s)", "upper": "upper(s)", "lower": "lower(s)", "trim": "trim(s)", "replace": "replace(s, 'a', 'z')",
        "split": "split(s, ',')", "join": "join(['a', 'b'], '-')", "format": "format('{}-{}', s, n)",
        "regexMatch": "regexMatch(trim(s), '^a,b$')", "abs": "abs(n - 10)", "round": "round(x, 2)", "floor": "floor(x)",
        "ceil": "ceil(x)", "min": "min(n, 3)", "max": "max(n, 3)", "clamp": "clamp(n, 1, 5)", "coalesce": "coalesce(s, 'z')",
        "isNull": "isNull(s)", "decimal": "decimal(x, 1)", "date": "date('2026-01-01')", "datetime": "datetime('2026-01-01 08:00:00')",
        "addDays": "addDays(d, 3)", "diffDays": "diffDays(d, date('2026-01-01'))", "formatDate": "formatDate(d, '%Y/%m/%d')",
        "sum": "sum([1, 2, 3])", "substr": "substr(s, 1, 2)",
    }
    assert set(calls) == set(E.FUNCTIONS), set(E.FUNCTIONS) ^ set(calls)
    for name, x in calls.items():
        p = E.compile_expr(x, f)
        interp = E.celpy.InterpretedRunner(E._ENV, E._ENV.compile(x), E._FUNCS)
        act = {k: E.to_cel(v) for k, v in row.items()}
        assert p.evaluate(row) == E.from_cel(interp.evaluate(act)), name
