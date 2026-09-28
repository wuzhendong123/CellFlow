"""与 TECH_DESIGN §6.1 一致的示例方案 DSL（供各任务测试复用）。"""

from __future__ import annotations

import copy

SRC_HERO = {
    "id": "src_hero",
    "type": "EXCEL_SOURCE",
    "label": "角色配置表",
    "config": {
        "sheet": {"match": "EXACT", "value": "角色配置"},
        "loaderOptions": {"mergePolicy": "FILL", "hiddenRows": "KEEP_WARN", "strikethroughRows": "KEEP"},
        "regions": [
            {
                "regionId": "rg_global", "name": "全局开关", "shape": "KEY_VALUE", "outputPortId": "out_global",
                "designRange": {"startRow": 1, "startCol": 1, "endRow": 5, "endCol": 2},
                "locator": {"type": "FIXED"},
                "shapeOptions": {"keyCol": 1, "valueCol": 2, "orientation": "VERTICAL", "duplicateKey": "ERROR"},
                "columns": [
                    {"source": "开服天数上限", "field": "maxOpenDays", "type": "int", "required": True},
                    {"source": "双倍经验开关", "field": "doubleExp", "type": "bool",
                     "boolTokens": {"true": ["开", "是", "1"], "false": ["关", "否", "0"]}},
                    {"source": "血量系数", "field": "hpRate", "type": "float"},
                    {"source": "活动ID", "field": "activityId", "type": "string"},
                    {"source": "配置版本", "field": "cfgVersion", "type": "string"},
                ],
            },
            {
                "regionId": "rg_hp", "name": "职业等级血量", "shape": "MATRIX", "outputPortId": "out_hp",
                "designRange": {"startRow": 8, "startCol": 1, "endRow": 11, "endCol": 6},
                "locator": {
                    "type": "ANCHOR",
                    "start": {"text": "职业\\等级", "match": "EXACT", "offset": {"row": 0, "col": 0}},
                    "end": {"rows": {"mode": "UNTIL_BLANK_ROWS", "n": 1}, "cols": {"mode": "UNTIL_BLANK_HEADER"}},
                },
                "shapeOptions": {
                    "rowHeaderCols": 1, "colHeaderRows": 1, "rowDims": ["job"], "colDims": ["level"],
                    "valueName": "baseHp", "dropEmpty": True, "totalMarkers": ["合计", "总计"],
                    "dimParsers": {"level": {"regex": "^(?:Lv\\.?)?(\\d+)$", "type": "int"}},
                },
                "columns": [
                    {"source": "job", "field": "job", "type": "string", "isKey": True, "required": True},
                    {"source": "level", "field": "level", "type": "int", "isKey": True, "required": True},
                    {"source": "baseHp", "field": "baseHp", "type": "int", "required": True},
                ],
            },
            {
                "regionId": "rg_reward", "name": "等级奖励明细", "shape": "DETAIL", "outputPortId": "out_reward",
                "designRange": {"startRow": 17, "startCol": 1, "endRow": 22, "endCol": 5},
                "locator": {
                    "type": "ANCHOR",
                    "start": {"text": "奖励ID", "match": "EXACT", "offset": {"row": 0, "col": 0}},
                    "end": {"rows": {"mode": "UNTIL_ANCHOR", "text": "总计", "inclusive": False},
                            "cols": {"mode": "UNTIL_BLANK_HEADER"}},
                },
                "shapeOptions": {"headerRows": 1, "orientation": "ROW", "skipRows": {"blank": True, "commentPrefix": "#"}},
                "columns": [
                    {"source": "奖励ID", "field": "rewardId", "type": "long", "isKey": True, "required": True},
                    {"source": "职业", "field": "job", "type": "string", "required": True},
                    {"source": "等级", "field": "level", "type": "int", "required": True},
                    {"source": "道具ID", "field": "itemId", "type": "long", "required": True},
                    {"source": "数量", "field": "count", "type": "int", "required": True},
                ],
            },
            {
                "regionId": "rg_total", "name": "奖励总计", "shape": "SUMMARY", "outputPortId": "out_total",
                "designRange": {"startRow": 23, "startCol": 1, "endRow": 23, "endCol": 5},
                "locator": {
                    "type": "ANCHOR",
                    "start": {"text": "总计", "match": "EXACT", "offset": {"row": 0, "col": 0}},
                    "end": {"rows": {"mode": "FIXED_SIZE", "n": 1}, "cols": {"mode": "FIXED_SIZE", "n": 5}},
                },
                "shapeOptions": {"layout": "ROW", "alignWith": "rg_reward"},
                "columns": [{"source": "数量", "field": "totalCount", "type": "int", "required": True}],
            },
            {
                "regionId": "rg_note", "name": "填表说明", "shape": "IGNORE",
                "designRange": {"startRow": 1, "startCol": 8, "endRow": 30, "endCol": 12},
                "locator": {"type": "FIXED"},
            },
        ],
    },
    "ports": {"inputs": [], "outputs": [
        {"portId": "out_global", "regionId": "rg_global"}, {"portId": "out_hp", "regionId": "rg_hp"},
        {"portId": "out_reward", "regionId": "rg_reward"}, {"portId": "out_total", "regionId": "rg_total"},
    ]},
}

SRC_ITEM = {
    "id": "src_item",
    "type": "EXCEL_SOURCE",
    "label": "道具主表",
    "config": {
        "sheet": {"match": "EXACT", "value": "道具表"},
        "regions": [{
            "regionId": "rg_item", "name": "道具", "shape": "DETAIL", "outputPortId": "out_item",
            "designRange": {"startRow": 1, "startCol": 1, "endRow": 4, "endCol": 3},
            "locator": {"type": "AUTO_EXPAND", "blankRowsToStop": 2},
            "shapeOptions": {"headerRows": 1},
            "columns": [
                {"source": "道具ID", "field": "itemId", "type": "long", "isKey": True, "required": True},
                {"source": "名称", "field": "name", "type": "string", "required": True},
                {"source": "品质", "field": "quality", "type": "enum", "enumMap": {"普通": 1, "稀有": 2, "史诗": 3}},
            ],
        }],
    },
    "ports": {"inputs": [], "outputs": [{"portId": "out_item", "regionId": "rg_item"}]},
}


def src_hero() -> dict:
    return copy.deepcopy(SRC_HERO)


def src_item() -> dict:
    return copy.deepcopy(SRC_ITEM)


def region(src: dict, rid: str) -> dict:
    return next(r for r in src["config"]["regions"] if r["regionId"] == rid)


def e(i, sn, sp, tn, tp):
    return {"id": i, "source": {"nodeId": sn, "portId": sp}, "target": {"nodeId": tn, "portId": tp}}


def sink(node_id, dataset, table, keys, mapping, guards=None):
    return {"id": node_id, "type": "SINK", "label": f"输出:{dataset}", "config": {
        "dataset": dataset,
        "binding": {"table": table, "strategy": "SWAP", "keyFields": keys,
                    "columnMapping": [{"field": f, "column": c} for f, c in mapping], "guards": guards},
    }}


def hero_dsl(with_derive: bool = True) -> dict:
    """§6.1 示例方案：两源 + 关联 + 校验（参数与引用输入）+ 派生列 + 三个输出。"""
    nodes = [
        src_hero(), src_item(),
        {"id": "join_reward_item", "type": "JOIN", "label": "奖励关联道具", "config": {
            "joinType": "LEFT", "leftAlias": "reward", "rightAlias": "item",
            "on": [{"left": "itemId", "right": "itemId"}], "expectedCardinality": "MANY_TO_ONE",
            "conflictPolicy": {"mode": "EXPLICIT_THEN_PREFIX", "aliases": {"item.name": "itemName"}, "keyColumns": "MERGE"},
            "select": ["reward.*", "item.name", "item.quality"],
            "explosionGuard": {"maxOutputRows": 1000000, "maxAmplification": 1.0},
            "unmatchedPolicy": "SIDE_OUTPUT"}},
        {"id": "val_reward", "type": "VALIDATOR", "label": "奖励校验", "config": {
            "params": {"total": "in_params"}, "refs": {"item": "in_ref_item"},
            "rules": [
                {"ruleId": "r1", "type": "NOT_NULL", "fields": ["itemId", "count"], "severity": "ERROR"},
                {"ruleId": "r2", "type": "EXPR", "expr": "count > 0", "severity": "ERROR", "message": "奖励数量必须大于 0"},
                {"ruleId": "r3", "type": "FOREIGN_KEY", "field": "itemId", "ref": {"input": "item", "field": "itemId"}, "severity": "ERROR"},
                {"ruleId": "r4", "type": "UNIQUE", "fields": ["job", "level", "itemId"], "severity": "WARN"},
                {"ruleId": "r5", "type": "RECONCILE", "detailAgg": "sum(count)",
                 "summary": {"param": "total", "field": "totalCount"}, "tolerance": 0, "severity": "ERROR"},
            ]}},
        sink("sink_hp", "hero_base_hp", "cfg_hero_base_hp", ["job", "level"],
             [("job", "job_name"), ("level", "lv"), ("baseHp", "base_hp")] + ([("hp", "hp")] if with_derive else [])),
        sink("sink_reward", "level_reward", "cfg_level_reward", ["rewardId"],
             [("rewardId", "id"), ("job", "job_name"), ("level", "lv"), ("itemId", "item_id"), ("count", "item_count"),
              ("itemName", "item_name")]),
        sink("sink_global", "global_switch", "cfg_global_switch", None,
             [("maxOpenDays", "max_open_days"), ("doubleExp", "double_exp")]),
    ]
    edges = [
        e("e2", "src_hero", "out_reward", "join_reward_item", "in_left"),
        e("e3", "src_item", "out_item", "join_reward_item", "in_right"),
        e("e4", "join_reward_item", "out_main", "val_reward", "in_main"),
        e("e5", "src_hero", "out_total", "val_reward", "in_params"),
        e("e8", "src_item", "out_item", "val_reward", "in_ref_item"),
        e("e6", "val_reward", "out_pass", "sink_reward", "in"),
        e("e7", "src_hero", "out_global", "sink_global", "in"),
    ]
    if with_derive:
        nodes.append({"id": "derive_hp", "type": "DERIVE", "label": "血量系数", "config": {
            "params": {"global": "in_params"},
            "columns": [{"field": "hp", "expr": "int(double(baseHp) * params.global.hpRate)", "type": "int"}],
            "onError": "ERROR"}})
        edges += [e("e1", "src_hero", "out_hp", "derive_hp", "in"), e("e9", "src_hero", "out_global", "derive_hp", "in_params"),
                  e("e10", "derive_hp", "out", "sink_hp", "in")]
    else:
        edges.append(e("e1", "src_hero", "out_hp", "sink_hp", "in"))
    return {"dslVersion": "1.0", "pipelineCode": "hero_config", "nodes": nodes, "edges": edges}
