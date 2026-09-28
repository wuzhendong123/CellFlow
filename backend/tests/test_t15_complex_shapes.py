"""T15：分组明细、表单型、重复块、多 Sheet 同构合并（PRD F2-13~F2-18）。"""

import datetime as dt

import fixtures

from cellflow.engine.grid import Workbook
from cellflow.engine.source import run_source


def run(cfg, data=None):
    return run_source(cfg, Workbook(data or fixtures.complex_shapes()), "s")


def test_f2_13_grouped_detail():
    cfg = {"sheet": {"match": "EXACT", "value": "装备"}, "regions": [{
        "regionId": "g", "name": "装备", "shape": "GROUPED_DETAIL", "outputPortId": "o",
        "designRange": {"startRow": 1, "startCol": 1, "endRow": 8, "endCol": 3}, "locator": {"type": "AUTO_EXPAND"},
        "shapeOptions": {"headerRows": 1, "groupField": "category",
                         "groupRowDetector": {"mode": "REGEX", "pattern": "^【(.+)】$"}, "subtotalDetector": {"pattern": "^小计$"}},
        "columns": [{"source": "名称", "field": "name", "type": "string"}, {"source": "攻击", "field": "atk", "type": "int"},
                    {"source": "category", "field": "category", "type": "string"}],
    }]}
    r = run(cfg)
    assert r.outputs["o"].rows == [
        {"name": "铁剑", "atk": 10, "category": "武器类"}, {"name": "钢剑", "atk": 20, "category": "武器类"},
        {"name": "皮甲", "atk": 0, "category": "防具类"},
    ]
    assert r.outputs["o"].lineage[2]["category"] == "装备!A6"


def test_f2_14_form_and_shift():
    region = {"regionId": "f", "name": "活动", "shape": "FORM", "outputPortId": "o",
              "designRange": {"startRow": 3, "startCol": 2, "endRow": 4, "endCol": 6},
              "locator": {"type": "ANCHOR", "start": {"text": "名称：", "offset": {"row": 0, "col": 0}},
                          "end": {"rows": {"mode": "FIXED_SIZE", "n": 2}, "cols": {"mode": "FIXED_SIZE", "n": 5}}},
              "shapeOptions": {"fields": {"名称": {"row": 0, "col": 1}, "品质": {"row": 0, "col": 4}, "开始": {"row": 1, "col": 1}}},
              "columns": [{"source": "名称", "field": "name", "type": "string"}, {"source": "品质", "field": "quality", "type": "string"},
                          {"source": "开始", "field": "start", "type": "date"}]}
    cfg = {"sheet": {"match": "EXACT", "value": "活动"}, "regions": [region]}
    r = run(cfg)
    assert r.outputs["o"].rows == [{"name": "中秋活动", "quality": "史诗", "start": dt.date(2026, 9, 25)}]
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(fixtures.complex_shapes()))
    wb["活动"].insert_rows(1, 5)
    buf = io.BytesIO()
    wb.save(buf)
    r = run(cfg, buf.getvalue())
    assert r.outputs["o"].rows[0]["name"] == "中秋活动" and r.outputs["o"].lineage[0]["name"] == "活动!C8"


def _hero_cfg():
    return {"sheet": {"match": "EXACT", "value": "英雄"}, "regions": [{
        "regionId": "h", "name": "英雄卡片", "shape": "REPEATING_BLOCK", "outputPortId": "o",
        "designRange": {"startRow": 1, "startCol": 1, "endRow": 200, "endCol": 2}, "locator": {"type": "FIXED"},
        "shapeOptions": {"blockAnchor": "^英雄[:：].*", "blockSize": {"rows": 4, "cols": 2}, "innerShape": "FORM",
                         "innerOptions": {"fields": {"编号": {"row": 0, "col": 0}, "名称": {"row": 1, "col": 1},
                                                     "攻击": {"row": 2, "col": 1}}}},
        "columns": [{"source": "编号", "field": "code", "type": "string"}, {"source": "名称", "field": "name", "type": "string"},
                    {"source": "攻击", "field": "atk", "type": "int"}],
    }]}


def test_f2_15_repeating_blocks_grow():
    r = run(_hero_cfg())
    assert len(r.outputs["o"]) == 12 and r.outputs["o"].rows[0]["_block"] == 1 and r.outputs["o"].rows[11]["atk"] == 111
    r = run(_hero_cfg(), fixtures.complex_shapes(blocks=15))
    assert len(r.outputs["o"]) == 15


def test_f2_16_repeating_overlap():
    r = run(_hero_cfg(), fixtures.complex_shapes(overlap_blocks=True))
    assert any(i["code"] == "REPEATING_BLOCK_OVERLAP" for i in r.issues) and "o" in r.failed_ports


def _server_cfg():
    return {"sheet": {"match": "REGEX", "value": "^S\\d+服$", "asColumn": "server"}, "regions": [{
        "regionId": "s", "name": "区服道具", "shape": "DETAIL", "outputPortId": "o",
        "designRange": {"startRow": 1, "startCol": 1, "endRow": 4, "endCol": 2}, "locator": {"type": "AUTO_EXPAND"},
        "shapeOptions": {"headerRows": 1},
        "columns": [{"source": "道具ID", "field": "itemId", "type": "long", "required": True},
                    {"source": "数量", "field": "count", "type": "int", "required": True}],
    }]}


def test_f2_17_sheet_set_union_and_new_sheet():
    r = run(_server_cfg())
    o = r.outputs["o"]
    assert len(o) == 9 and {x["server"] for x in o.rows} == {"S1服", "S2服", "S3服"}
    r = run(_server_cfg(), fixtures.complex_shapes(servers=("S1服", "S2服", "S3服", "S4服")))
    assert {x["server"] for x in r.outputs["o"].rows} == {"S1服", "S2服", "S3服", "S4服"}


def test_f2_18_sheet_set_missing_column_names_sheet():
    r = run(_server_cfg(), fixtures.complex_shapes(s2_missing_col=True))
    miss = [i for i in r.issues if i["code"] == "HEADER_MISSING_REQUIRED"]
    assert miss and miss[0]["sheet"] == "S2服"


def test_dag_infers_sheet_set_and_block_columns():
    from cellflow.engine.dag import analyze

    dsl = {"nodes": [{"id": "src", "type": "EXCEL_SOURCE", "config": _server_cfg()},
                     {"id": "h", "type": "EXCEL_SOURCE", "config": _hero_cfg()}], "edges": []}
    a = analyze(dsl)
    assert [c.field for c in a.schemas[("src", "o")].columns] == ["itemId", "count", "server"]
    assert "_block" in [c.field for c in a.schemas[("h", "o")].columns]
