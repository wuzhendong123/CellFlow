"""系统设置（TECH_DESIGN §10.8，D20）：表绑定/方案配置 > 系统设置 > 内置默认值。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from cellflow.errors import CFError
from cellflow.meta.db import get_engine
from cellflow.meta.tables import system_setting

# 设置键 → (默认值, 分组, 说明, 校验)
DEFAULTS: dict[str, tuple[Any, str, str, str]] = {
    "file.maxSizeMB": (20, "文件", "上传文件大小上限（MB）", "posint"),
    "file.maxCells": (500000, "文件", "单个 Sheet 有效单元格上限", "posint"),
    "preview.sampleRows": (200, "预览", "试跑预览每个区域的采样行数", "posint"),
    "guard.maxRowChangeRatio": (0.5, "安全闸", "G3 行数波动上限（比例）", "ratio"),
    "guard.maxDeleteRatio": (0.3, "安全闸", "G4 删除比例上限（比例）", "ratio"),
    "guard.forbidEmpty": (True, "安全闸", "G2 清空保护", "bool"),
    "guard.driftPolicy": ("REJECT", "安全闸", "G7 漂移处理：REJECT / OVERWRITE", "enum:REJECT,OVERWRITE"),
    "join.maxOutputRows": (1000000, "关联", "关联输出行数绝对上限", "posint"),
    "regression.fileCount": (5, "回归", "方案发布前回归使用的历史文件数", "posint"),
    "retention.releases": (50, "保留期", "保留最近多少个发布的快照", "posint"),
    "retention.days": (180, "保留期", "快照与原始文件保留天数", "posint"),
    "retention.backupTables": (3, "保留期", "业务库中保留的备份表份数", "posint"),
    "retention.testJobDays": (7, "保留期", "试跑任务结果保留天数", "posint"),
    "write.lockWaitTimeoutSec": (3, "写入", "RENAME 等待元数据锁的秒数", "posint"),
    "write.renameRetries": (5, "写入", "RENAME 失败重试次数", "posint"),
    "write.maxConcurrentPerDatasource": (2, "写入", "每个数据源同时写入的任务数", "posint"),
    "callback.maxAttempts": (10, "Open API", "回调最大重试次数", "posint"),
    "openapi.defaultRateLimitPerMin": (60, "Open API", "新建调用方的默认限流（次/分钟）", "posint"),
}


def validate(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise CFError("SETTING_UNKNOWN", f"未知的设置项 {key}", 400)
    kind = DEFAULTS[key][3]
    ok = True
    if kind == "posint":
        ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    elif kind == "ratio":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
    elif kind == "bool":
        ok = isinstance(value, bool)
    elif kind.startswith("enum:"):
        ok = value in kind[5:].split(",")
    if not ok:
        raise CFError("SETTING_INVALID", f"设置项 {key} 的值不合法：{value!r}", 400)
    return value


def get_all() -> dict[str, Any]:
    values = {k: v[0] for k, v in DEFAULTS.items()}
    try:
        with get_engine().connect() as c:
            for row in c.execute(select(system_setting)).mappings():
                if row["setting_key"] in values:
                    values[row["setting_key"]] = row["value_json"]
    except Exception:  # 元数据库不可用时退回内置默认值
        pass
    return values


def get(key: str) -> Any:
    return get_all()[key]


def describe() -> list[dict]:
    current = get_all()
    rows = {}
    with get_engine().connect() as c:
        for row in c.execute(select(system_setting)).mappings():
            rows[row["setting_key"]] = row
    out = []
    for k, (default, group, desc, _) in DEFAULTS.items():
        r = rows.get(k)
        out.append({
            "key": k, "group": group, "description": desc, "value": current[k], "default": default,
            "changed": r is not None and r["value_json"] != default,
            "updatedBy": r["updated_by"] if r else None,
            "updatedAt": r["updated_at"].isoformat() if r and r["updated_at"] else None,
        })
    return out


def update(changes: dict[str, Any], operator: str) -> dict[str, tuple[Any, Any]]:
    for k, v in changes.items():
        validate(k, v)
    before = get_all()
    diff = {}
    with get_engine().begin() as c:
        for k, v in changes.items():
            c.execute(system_setting.delete().where(system_setting.c.setting_key == k))
            c.execute(system_setting.insert().values(setting_key=k, value_json=v, updated_by=operator))
            diff[k] = (before[k], v)
    return diff


def effective_guard(binding_guards: dict | None) -> dict:
    s = get_all()
    g = {
        "maxRowChangeRatio": s["guard.maxRowChangeRatio"],
        "maxDeleteRatio": s["guard.maxDeleteRatio"],
        "forbidEmpty": s["guard.forbidEmpty"],
        "driftPolicy": s["guard.driftPolicy"],
        "minRows": None,
        "maxRows": None,
    }
    g.update({k: v for k, v in (binding_guards or {}).items() if v is not None})
    return g
