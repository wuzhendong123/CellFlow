"""T02~T05：单元格归一化、定位、基础形态、字段清洗（PRD F1、F2、F3）。"""

import datetime as dt
from decimal import Decimal

import fixtures
import pytest
from dsl_examples import region, src_hero, src_item

from cellflow.engine.grid import ERROR, F_NOCACHE, HIDDEN_ROW, MERGED_FILLED, STRIKE, Workbook, WorkbookError
from cellflow.engine.locate import Rect, resolve_rect, suggest_locator
from cellflow.engine.shapes import parse_detail, parse_kv, parse_matrix, suggest_shape
from cellflow.engine.source import preview_region, run_source
from cellflow.engine.univer import sheet_snapshot


def codes(issues, severity=None):
    return [i["code"] for i in issues if severity is None or i["severity"] == severity]


# ---------------- T02 / F1 ----------------
def test_f1_1_sheets_and_normalization():
    wb = Workbook(fixtures.hero_config())
    assert wb.sheet_names == ["角色配置", "道具表"]
    g = Workbook(fixtures.special()).sheet("Sheet1")
    assert g.flag(3, 2) & F_NOCACHE and g.cell(3, 2) is None
    assert g.flag(4, 2) & ERROR
    assert g.flag(5, 1) & STRIKE
    assert g.flag(6, 1) & HIDDEN_ROW
    assert g.cell(9, 2) == "合并" and g.flag(9, 2) & MERGED_FILLED


def test_f1_univer_snapshot_merges_hidden_marks():
    g = Workbook(fixtures.special()).sheet("Sheet1")
    snap = sheet_snapshot(g)
    assert snap["mergeData"] == [{"startRow": 7, "endRow": 8, "startColumn": 0, "endColumn": 1}]
    assert 5 in snap["rowData"]
    kinds = {(m["row"], m["col"]): m["kind"] for m in snap["cfMarks"]}
    assert kinds[(2, 1)] == "NO_CACHE" and kinds[(3, 1)] == "ERROR"
    assert 1 not in snap["cellData"].get(8, {})  # 合并区域非左上角不重复给值
    page = sheet_snapshot(g, 1, 2)
    assert set(page["cellData"]) <= {0, 1} and page["rowCount"] == g.n_rows


def test_f1_2_too_many_cells():
    with pytest.raises(WorkbookError) as e:
        Workbook(fixtures.valuation_like(50), max_cells=100).sheet("标的明细(存续)")
    assert e.value.code == "FILE_TOO_LARGE"


def test_f1_3_not_xlsx():
    with pytest.raises(WorkbookError) as e:
        Workbook(b"name,count\na,1\n")
    assert e.value.code == "FILE_UNSUPPORTED"


def test_f1_date_1904():
    g = Workbook(fixtures.special(date1904=True)).sheet("Sheet1")
    assert g.cell(2, 3) == dt.datetime(2026, 1, 2)


# ---------------- T03 / F2 定位 ----------------
def _run(data, src):
    return run_source(src["config"], Workbook(data), src["id"])


def test_f2_3_more_rows_total_not_read_as_detail():
    res = _run(fixtures.hero_config(extra_reward_rows=20), src_hero())
    reward = res.outputs["out_reward"]
    assert len(reward) == 25
    assert all(r["rewardId"] != "总计" for r in reward.rows)
    assert res.outputs["out_total"].rows[0]["totalCount"] == sum(r["count"] for r in reward.rows)


def test_f2_shifted_region_found_by_anchor():
    res = _run(fixtures.hero_config(shift_rows=5), src_hero())
    assert len(res.outputs["out_reward"]) == 5
    assert len(res.outputs["out_hp"]) == 11
    rep = {r["regionId"]: r for r in res.locate_report}
    assert rep["rg_reward"]["offset"]["rows"] == 5


def test_f2_5_anchor_renamed_reports_candidates():
    res = _run(fixtures.hero_config(rename_reward_anchor="奖励编号"), src_hero())
    err = [i for i in res.issues if i["code"] == "ANCHOR_NOT_FOUND"]
    assert err and err[0]["related"][0]["text"] == "奖励编号"
    assert "out_reward" in res.failed_ports
    assert "out_hp" in res.outputs  # 其他区域不受影响


def test_f2_6_overlap_detected():
    src = src_hero()
    region(src, "rg_total")["locator"] = {"type": "FIXED"}
    region(src, "rg_total")["designRange"] = {"startRow": 22, "startCol": 1, "endRow": 22, "endCol": 5}
    res = _run(fixtures.hero_config(), src)
    assert "REGION_OVERLAP" in codes(res.issues)


def test_f2_7_ignore_region_masked():
    src = src_hero()
    src["config"]["regions"].append({
        "regionId": "rg_all", "name": "整片", "shape": "DETAIL", "outputPortId": "out_all",
        "designRange": {"startRow": 1, "startCol": 8, "endRow": 3, "endCol": 9}, "locator": {"type": "FIXED"},
        "excludeFrom": [], "shapeOptions": {"headerRows": 0}, "columns": [
            {"source": "H", "field": "h", "type": "string"}, {"source": "I", "field": "i", "type": "string"}],
    })
    res = _run(fixtures.hero_config(), src)
    assert all(r["h"] is None and r["i"] is None for r in res.outputs["out_all"].rows)


def test_suggest_locator():
    g = Workbook(fixtures.hero_config()).sheet("角色配置")
    loc = suggest_locator(g, Rect(17, 1, 22, 5))
    assert loc["type"] == "ANCHOR" and loc["start"]["text"] == "奖励ID"
    assert loc["end"]["rows"]["mode"] == "UNTIL_ANCHOR" and loc["end"]["rows"]["text"] == "总计"
    rect, _ = resolve_rect(g, loc, Rect(17, 1, 22, 5))
    assert rect == Rect(17, 1, 22, 5)


# ---------------- T04 / F2 形态 ----------------
def test_f2_8_kv_wide():
    res = _run(fixtures.hero_config(), src_hero())
    g = res.outputs["out_global"]
    assert len(g) == 1
    assert g.rows[0] == {"maxOpenDays": 30, "doubleExp": True, "hpRate": 1.5, "activityId": "ACT-2026-09", "cfgVersion": "v9"}
    assert g.lineage[0]["maxOpenDays"] == "角色配置!B1"


def test_f2_9_kv_duplicate_key():
    wb = Workbook(fixtures.hero_config())
    g = wb.sheet("角色配置")
    g.values[1, 0] = "开服天数上限"
    r = parse_kv(__import__("cellflow.engine.locate", fromlist=["slice_block"]).slice_block(g, Rect(1, 1, 5, 2)), {})
    assert [i["cell"] for i in r.issues if i["code"] == "KV_DUPLICATE_KEY"] == ["角色配置!A2"]


def test_f2_19_kv_long():
    src = src_hero()
    rg = region(src, "rg_global")
    rg["shapeOptions"]["outputMode"] = "LONG"
    rg["columns"] = [{"source": "key", "field": "key", "type": "string"}, {"source": "value", "field": "value", "type": "string"}]
    res = _run(fixtures.hero_config(), src)
    rows = res.outputs["out_global"].rows
    assert len(rows) == 5 and rows[0] == {"key": "开服天数上限", "value": "30"}


def test_f2_10_matrix_unpivot():
    res = _run(fixtures.hero_config(), src_hero())
    hp = res.outputs["out_hp"]
    assert {"job": "战士", "level": 1, "baseHp": 100} in hp.rows
    assert all(r["level"] in (1, 2, 3, 4) for r in hp.rows)  # 合计列排除
    assert not any(r["job"] == "法师" and r["level"] == 3 for r in hp.rows)  # 空交叉点丢弃
    assert len(hp) == 11
    i = hp.rows.index({"job": "战士", "level": 1, "baseHp": 100})
    assert hp.lineage[i] == {"job": "角色配置!A9", "level": "角色配置!B8", "baseHp": "角色配置!B9"}


def test_matrix_metric_level_on_row_axis():
    """真实样例：行为指标、列为货币的横向矩阵 → 每个货币一行。"""
    g = Workbook(fixtures.valuation_like()).sheet("盯市汇总")
    from cellflow.engine.locate import slice_block

    r = parse_matrix(slice_block(g, Rect(6, 1, 10, 5)), {
        "colHeaderRows": 1, "rowHeaderCols": 1, "rowDims": ["metric"], "colDims": ["currency"],
        "metricLevel": "metric", "valueName": "v", "dropEmpty": False,
    })
    # 第 6 行「Currency 货币」本身是列头
    assert r.headers[0] == "currency"
    rows = [dict(zip(r.headers, x, strict=True)) for x in r.rows]
    hkd = next(x for x in rows if x["currency"] == "HKD")
    assert hkd["Collateral Balance 保证金账户"] == 100.5


def test_f2_detail_column_orientation_real_layout():
    g = Workbook(fixtures.valuation_like()).sheet("盯市汇总")
    from cellflow.engine.locate import slice_block

    r = parse_detail(slice_block(g, Rect(6, 1, 10, 5)), {"headerRows": 1, "orientation": "COLUMN"})
    assert r.headers[0] == "Currency 货币"
    assert [x[0] for x in r.rows] == ["HKD", "CNY", "USD", "JPY"]
    assert r.lineage[1]["Cash Balance 预付金账户"] == "盯市汇总!C9"


def test_f2_11_multi_level_header():
    wb = Workbook(fixtures.hero_config())
    g = wb.sheet("角色配置")
    import numpy as np

    from cellflow.engine.locate import Block

    vals = np.array([["名称", "属性", None], [None, "攻击", "防御"], ["剑", 10, 5]], dtype=object)
    r = parse_detail(Block("S", Rect(1, 1, 3, 3), vals, np.zeros((3, 3), dtype=np.uint16)), {"headerRows": 2})
    assert r.headers == ["名称", "属性.攻击", "属性.防御"]
    assert g is not None


def test_suggest_shape():
    g = Workbook(fixtures.hero_config()).sheet("角色配置")
    from cellflow.engine.locate import slice_block

    assert suggest_shape(slice_block(g, Rect(1, 1, 5, 2))) == "KEY_VALUE"
    assert suggest_shape(slice_block(g, Rect(8, 1, 11, 6))) == "MATRIX"
    assert suggest_shape(slice_block(g, Rect(17, 1, 22, 5))) == "DETAIL"


# ---------------- T05 / F3 字段 ----------------
def test_f2_4_inserted_column_bound_by_name():
    res = _run(fixtures.hero_config(insert_note_col=True), src_hero())
    r = res.outputs["out_reward"].rows[0]
    assert r == {"rewardId": 1001, "job": "战士", "level": 1, "itemId": 5001, "count": 10}
    warn = [i for i in res.issues if i["code"] == "HEADER_NEW_COLUMN"]
    assert warn and "备注" in warn[0]["message"] and warn[0]["severity"] == "WARN"


def test_missing_required_column():
    src = src_hero()
    region(src, "rg_reward")["columns"].append({"source": "权重", "field": "weight", "type": "int", "required": True})
    res = _run(fixtures.hero_config(), src)
    assert "HEADER_MISSING_REQUIRED" in codes(res.issues, "ERROR")


def _coerce(value, type_, **spec):
    from cellflow.engine.columns import coerce_value
    from cellflow.engine.types import parse_type

    return coerce_value(value, parse_type(type_), spec, False, 0)


def test_f3_1_id_precision():
    from cellflow.engine.types import CoerceError

    with pytest.raises(CoerceError) as e:
        _coerce(123456789012345678.0, "long")
    assert e.value.code == "ID_PRECISION_LOST"
    assert _coerce(211500201.0, "string") == "211500201"


def test_f3_2_int():
    from cellflow.engine.types import CoerceError

    assert _coerce(1.0, "int") == 1
    with pytest.raises(CoerceError):
        _coerce(1.5, "int")
    assert _coerce("1,000", "int") == 1000


def test_f3_3_nulls_and_required():
    from cellflow.engine.types import CoerceError

    for v in ["-", "  ", None, "N/A"]:
        assert _coerce(v, "string") is None
    assert _coerce("无", "string", nullTokens=["无"]) is None
    with pytest.raises(CoerceError) as e:
        _coerce("-", "string", required=True)
    assert e.value.code == "REQUIRED_EMPTY"


def test_f3_4_bool():
    from cellflow.engine.types import CoerceError

    assert _coerce("√", "bool") is True and _coerce("关", "bool") is False and _coerce(1, "bool") is True
    with pytest.raises(CoerceError):
        _coerce("也许", "bool")


def test_f3_5_enum():
    from cellflow.engine.types import CoerceError

    m = {"普通": 1, "稀有": 2, "史诗": 3}
    assert _coerce("稀有", "enum", enumMap=m) == 2
    with pytest.raises(CoerceError) as e:
        _coerce("传说", "enum", enumMap=m)
    assert e.value.code == "ENUM_UNKNOWN"


def test_f3_6_split_list_struct():
    v = _coerce("1001:5|1002:3", "list<struct<itemId:long,count:int>>", split={"item": "|", "kv": ":"})
    assert v == [{"itemId": 1001, "count": 5}, {"itemId": 1002, "count": 3}]


def test_f3_7_width_and_invisible():
    assert _coerce("１００", "int") == 100
    assert _coerce(" 甲​ ", "string") == "甲"


def test_f3_8_dates_and_decimal():
    assert _coerce(dt.datetime(2026, 8, 20), "date") == dt.date(2026, 8, 20)
    assert _coerce("2026/08/20", "date") == dt.date(2026, 8, 20)
    assert _coerce(46254, "date") == dt.date(2026, 8, 20)
    assert _coerce(196425417.021949, "decimal(20,2)") == Decimal("196425417.02")


def test_f3_9_error_cells_and_nocache_drop_row():
    src = {"id": "s", "config": {"sheet": {"match": "EXACT", "value": "Sheet1"}, "regions": [{
        "regionId": "r", "name": "明细", "shape": "DETAIL", "outputPortId": "o",
        "designRange": {"startRow": 1, "startCol": 1, "endRow": 6, "endCol": 2}, "locator": {"type": "FIXED"},
        "shapeOptions": {"headerRows": 1},
        "columns": [{"source": "名称", "field": "name", "type": "string"}, {"source": "数量", "field": "qty", "type": "int"}],
    }]}}
    res = run_source(src["config"], Workbook(fixtures.special()), "s")
    by_code = {i["code"]: i for i in res.issues}
    assert by_code["CELL_FORMULA_NO_CACHE"]["cell"] == "Sheet1!B3"
    assert by_code["CELL_ERROR_VALUE"]["cell"] == "Sheet1!B4"
    assert "HIDDEN_ROWS_READ" in by_code
    names = [r["name"] for r in res.outputs["o"].rows]
    assert names == ["甲", "丁", "戊"]  # 乙、丙因转换失败被移除（C7）


def test_reserved_field_names():
    from cellflow.engine.columns import validate_column_specs

    errs = validate_column_specs([{"field": "params"}, {"field": "in"}, {"field": "1abc"}, {"field": "ok"}])
    assert len(errs) == 3


def test_item_source_auto_expand():
    res = _run(fixtures.hero_config(), src_item())
    assert [r["quality"] for r in res.outputs["out_item"].rows] == [1, 2, 3]


def test_preview_region_real_like_detail():
    wb = Workbook(fixtures.valuation_like(40))
    r = preview_region(wb, "标的明细(存续)", {
        "regionId": "d", "name": "标的", "shape": "DETAIL", "outputPortId": "o",
        "designRange": {"startRow": 2, "startCol": 1, "endRow": 3, "endCol": 9},
        "locator": {"type": "AUTO_EXPAND"}, "shapeOptions": {"headerRows": 1},
        "columns": [{"source": "资金账户", "field": "account", "type": "string"},
                    {"source": "交易达成日", "field": "tradeDate", "type": "date"}],
    })
    assert r["output"]["total"] == 40
    assert {row["data"]["account"] for row in r["output"]["rows"]} == {"211500201"}  # 数字与文本混用统一为文本
