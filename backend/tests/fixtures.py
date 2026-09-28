"""测试夹具 Excel（全部为合成数据，不含任何真实客户信息）。

- hero_config：PRD §5 的示例文件（角色配置 + 道具表）
- valuation_like：按真实样例「客户估值报告」的版式合成（多段汇总 + 双语表头 + 第 2 行表头的明细）
- special：合并单元格、隐藏行、公式无缓存值、错误值、删除线、1904 日期等 L1 场景
"""

from __future__ import annotations

import datetime as dt
import io

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils.datetime import CALENDAR_MAC_1904


def _bytes(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def hero_config(
    extra_reward_rows: int = 0,
    insert_note_col: bool = False,
    rename_reward_anchor: str | None = None,
    total_count: int | None = None,
    reward_overrides: dict | None = None,
    hp_overrides: dict | None = None,
    shift_rows: int = 0,
    item_rows: list | None = None,
) -> bytes:
    """角色配置：A1:B5 全局开关（键值）、A8 起血量矩阵、A17 起奖励明细 + 总计行、H1:L30 填表说明；道具表：道具主表。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "角色配置"
    base = shift_rows
    kv = [("开服天数上限", 30), ("双倍经验开关", "开"), ("血量系数", 1.5), ("活动ID", "ACT-2026-09"), ("配置版本", "v9")]
    for i, (k, v) in enumerate(kv, start=1):
        ws.cell(base + i, 1, k)
        ws.cell(base + i, 2, v)
    hp = {"战士": [100, 120, 150, 180], "法师": [60, 70, None, 90], "射手": [80, 95, 110, 130]}
    if hp_overrides:
        hp.update(hp_overrides)
    r0 = base + 8
    ws.cell(r0, 1, "职业\\等级")
    for j, lv in enumerate(["Lv1", "Lv2", "Lv3", "Lv4", "合计"], start=2):
        ws.cell(r0, j, lv)
    for i, (job, vals) in enumerate(hp.items(), start=1):
        ws.cell(r0 + i, 1, job)
        for j, v in enumerate(vals, start=2):
            ws.cell(r0 + i, j, v)
        ws.cell(r0 + i, 6, sum(x for x in vals if x))
    # 奖励明细
    r1 = base + 17
    headers = ["奖励ID", "职业", "等级", "道具ID", "数量"]
    if insert_note_col:
        headers = ["奖励ID", "职业", "备注", "等级", "道具ID", "数量"]
    if rename_reward_anchor:
        headers[0] = rename_reward_anchor
    for j, h in enumerate(headers, start=1):
        ws.cell(r1, j, h)
    rewards = [
        (1001, "战士", 1, 5001, 10),
        (1002, "战士", 2, 5002, 20),
        (1003, "法师", 1, 5001, 5),
        (1004, "法师", 2, 5003, 8),
        (1005, "射手", 1, 5002, 12),
    ]
    for k in range(extra_reward_rows):
        rewards.append((2000 + k, "射手", 3, 5001, 1))
    if reward_overrides:
        rewards = [reward_overrides.get(r[0], r) for r in rewards]
    for i, rw in enumerate(rewards, start=1):
        vals = list(rw)
        if insert_note_col:
            vals = [vals[0], vals[1], "备注", *vals[2:]]
        for j, v in enumerate(vals, start=1):
            ws.cell(r1 + i, j, v)
    tr = r1 + len(rewards) + 1
    ws.cell(tr, 1, "总计")
    cnt_col = 6 if insert_note_col else 5
    ws.cell(tr, cnt_col, total_count if total_count is not None else sum(r[4] for r in rewards if r[4] is not None))
    for r in range(1, 31):
        ws.cell(r, 8, "填表说明：请勿修改表头" if r == 1 else None)
    ws.cell(2, 9, "说明文字")
    # 道具表
    it = wb.create_sheet("道具表")
    for j, h in enumerate(["道具ID", "名称", "品质"], start=1):
        it.cell(1, j, h)
    items = item_rows or [(5001, "金币", "普通"), (5002, "钻石", "稀有"), (5003, "神器碎片", "史诗")]
    for i, row in enumerate(items, start=2):
        for j, v in enumerate(row, start=1):
            it.cell(i, j, v)
    return _bytes(wb)


def valuation_like(detail_rows: int = 30) -> bytes:
    """合成的「客户估值报告」版式：汇总页多段区块（合并单元格的段标题、横向货币矩阵、键值段），明细页第 2 行为表头。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "盯市汇总"
    ws["A2"], ws["B2"] = "Client Name 客户全称", "DEMO CLIENT LIMITED"
    ws.merge_cells("B2:C2")
    ws["A3"], ws["B3"] = "Valuation Date 基准日", dt.datetime(2026, 8, 20)
    ws["A5"] = "Account Balance 资金账户余额"
    ws.merge_cells("A5:B5")
    cur = ["HKD", "CNY", "USD", "JPY"]
    rows = [
        ("Currency 货币", cur),
        ("Collateral Balance 保证金账户", [100.5, 2000.25, 300.0, 0]),
        ("Pending Collateral 保证金账户待结算", [0, 0, 0, 0]),
        ("Cash Balance 预付金账户", [5000.0, 60000.0, 70.5, 0]),
        ("FX(HKD)", [1, 1.1666, 7.8436, 0.0495]),
    ]
    for i, (label, vals) in enumerate(rows, start=6):
        ws.cell(i, 1, label)
        for j, v in enumerate(vals, start=2):
            ws.cell(i, j, v)
    ws["A14"] = "Margin Balance 盯市汇总"
    ws.merge_cells("A14:B14")
    kv = [
        ("Currency 货币", "HKD"),
        ("Credit Support Balance 资金账户余额", 196425.02),
        ("Variation Margin 合约估值(VM)", -1573.87),
        ("Initial Amount 期初保障金额(IA)", 112091.32),
        ("IA %", 0.4109),
        ("CSA Agreement 协议类型", "one-way"),
    ]
    for i, (k, v) in enumerate(kv, start=15):
        ws.cell(i, 1, k)
        ws.cell(i, 2, v)
    ws["L20"] = 1  # 版面外的零散单元格
    # 明细页：第 1 行空，第 2 行表头，尾部有空表头列
    d = wb.create_sheet("标的明细(存续)")
    hdr = ["交易确认书编号", "主确认书编号", "标的代码", "标的名称", "期初价格(交易货币)", "期初数量", "交易达成日", "交易货币", "资金账户"]
    for j, h in enumerate(hdr, start=1):
        d.cell(2, j, h)
    for i in range(detail_rows):
        vals = [
            f"DEMO007-H{i:05d}",
            "DEMO007",
            f"{600000 + i} CH Equity",
            f"标的{i}",
            round(10 + i * 0.37, 4),
            100 * (i + 1),
            dt.datetime(2026, 7, 1) + dt.timedelta(days=i),
            "CNY",
            211500201 if i % 2 else "211500201",  # 同一列数字与文本混用
        ]
        for j, v in enumerate(vals, start=1):
            d.cell(3 + i, j, v)
    d.auto_filter.ref = f"A2:I{2 + detail_rows}"
    d.freeze_panes = "C3"
    return _bytes(wb)


def special(date1904: bool = False) -> bytes:
    """L1 场景：合并单元格、隐藏行、公式无缓存值、错误值、删除线、加粗、富文本/空白字符。"""
    wb = openpyxl.Workbook()
    if date1904:
        wb.epoch = CALENDAR_MAC_1904
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"], ws["B1"], ws["C1"] = "名称", "数量", "日期"
    ws["A1"].font = Font(bold=True)
    ws["A2"], ws["B2"], ws["C2"] = "甲", 1, dt.datetime(2026, 1, 2)
    ws["A3"], ws["B3"] = "乙", "=B2*2"  # 公式：openpyxl 不计算，保存后无缓存值
    ws["A4"], ws["B4"] = "丙", "#N/A"
    ws["A5"], ws["B5"] = "丁", 5
    ws["A5"].font = Font(strike=True)
    ws["A6"], ws["B6"] = "戊", 6
    ws.row_dimensions[6].hidden = True
    ws["A8"] = "合并"
    ws.merge_cells("A8:B9")
    ws["A11"] = "  带不可见字符​ "
    return _bytes(wb)
