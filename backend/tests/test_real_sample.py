"""真实样例文件验证（文件含客户数据，不入库）。

设置环境变量 CF_REAL_SAMPLE=<xlsx 路径> 后运行；未设置时跳过。
覆盖真实版式：合并单元格的段标题、横向货币矩阵、键值段、第 2 行表头 + 尾部空表头的明细、同列数字/文本混用。
"""

import os

import pytest

from cellflow.engine.grid import Workbook
from cellflow.engine.source import run_source

PATH = os.environ.get("CF_REAL_SAMPLE")
pytestmark = pytest.mark.skipif(not PATH, reason="未提供 CF_REAL_SAMPLE")


def _anchor(text, rows=None, cols=None, match="EXACT", row_off=1):
    return {
        "type": "ANCHOR",
        "start": {"text": text, "match": match, "offset": {"row": row_off, "col": 0}},
        "end": {"rows": rows or {"mode": "UNTIL_BLANK_ROWS", "n": 1}, "cols": cols or {"mode": "UNTIL_BLANK_HEADER"}},
    }


def test_summary_sheet_sections():
    cfg = {"sheet": {"match": "EXACT", "value": "盯市汇总"}, "regions": [
        {"regionId": "client", "name": "客户", "shape": "KEY_VALUE", "outputPortId": "o_client",
         "locator": _anchor("Client Name 客户全称", rows={"mode": "FIXED_SIZE", "n": 2}, cols={"mode": "FIXED_SIZE", "n": 2}, row_off=0),
         "shapeOptions": {"keyCol": 1, "valueCol": 2},
         "columns": [{"source": "Client Name 客户全称", "field": "clientName", "type": "string", "required": True},
                     {"source": "Valuation Date 基准日", "field": "valuationDate", "type": "date", "required": True}]},
        {"regionId": "balance", "name": "资金账户余额", "shape": "DETAIL", "outputPortId": "o_balance",
         "locator": _anchor("资金账户余额", match="CONTAINS"),
         "shapeOptions": {"headerRows": 1, "orientation": "COLUMN"},
         "columns": [{"source": "Currency 货币", "field": "currency", "type": "string", "isKey": True},
                     {"source": "Collateral Balance 保证金账户", "field": "collateral", "type": "decimal(20,2)"},
                     {"source": "Cash Balance 预付金账户", "field": "cash", "type": "decimal(20,2)"},
                     {"source": "FX(HKD)", "field": "fx", "type": "float"}]},
        {"regionId": "margin", "name": "盯市汇总", "shape": "KEY_VALUE", "outputPortId": "o_margin",
         "locator": _anchor("Margin Balance 盯市汇总", cols={"mode": "FIXED_SIZE", "n": 2}),
         "shapeOptions": {"keyCol": 1, "valueCol": 2},
         "columns": [{"source": "Currency 货币", "field": "currency", "type": "string"},
                     {"source": "IA %", "field": "iaPct", "type": "float"},
                     {"source": "CSA Agreement 协议类型", "field": "csa", "type": "string"}]},
    ]}
    res = run_source(cfg, Workbook(open(PATH, "rb").read()), "real")
    errors = [i for i in res.issues if i["severity"] == "ERROR"]
    assert not errors, errors
    assert res.outputs["o_client"].rows[0]["valuationDate"].isoformat() == "2026-08-20"
    assert [r["currency"] for r in res.outputs["o_balance"].rows] == ["HKD", "CNY", "USD", "JPY"]
    assert res.outputs["o_margin"].rows[0]["csa"] == "one-way"


def test_detail_sheets_header_on_row_2():
    wb = Workbook(open(PATH, "rb").read())
    for sheet, n in [("合约保证金", 1485), ("资金流水(当月)", 8125), ("资金余额", 27)]:
        cfg = {"sheet": {"match": "EXACT", "value": sheet}, "regions": [{
            "regionId": "d", "name": sheet, "shape": "DETAIL", "outputPortId": "o",
            "designRange": {"startRow": 2, "startCol": 1, "endRow": 3, "endCol": 2},
            "locator": {"type": "AUTO_EXPAND"}, "shapeOptions": {"headerRows": 1},
            "columns": [{"source": "交易对手", "field": "counterparty", "type": "string", "required": True}],
        }]}
        res = run_source(cfg, wb, "real")
        assert len(res.outputs["o"]) == n, sheet
