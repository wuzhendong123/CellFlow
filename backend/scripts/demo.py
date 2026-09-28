"""本地演示数据（./cellflow.sh demo）：建演示业务表、数据源、方案 hero_config（已发布 v1）、调用方，并生成几份演示 Excel。

可重复执行：已存在的对象直接复用。只用于本地 docker 环境。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import dsl_examples  # noqa: E402
import fixtures  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from cellflow.config import resolve_ref  # noqa: E402
from cellflow.services import client_apps, datasources, files, pipelines  # noqa: E402

WHO = "demo"
BIZ_DB = "cellflow_biz"
OUT = Path(os.environ.get("CF_DEMO_DIR", "/demo"))

TABLES = [
    """CREATE TABLE IF NOT EXISTS cfg_hero_base_hp (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, job_name VARCHAR(32) NOT NULL, lv INT NOT NULL, base_hp INT NOT NULL,
        hp INT NULL, UNIQUE KEY uk_job_lv (job_name, lv)) CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS cfg_level_reward (
        id BIGINT PRIMARY KEY, job_name VARCHAR(32) NOT NULL, lv INT NOT NULL, item_id BIGINT NOT NULL,
        item_count INT NOT NULL, item_name VARCHAR(64) NULL) CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS cfg_global_switch (
        max_open_days INT NOT NULL, double_exp TINYINT(1) NOT NULL DEFAULT 0) CHARSET=utf8mb4""",
]

SAMPLES = {
    "hero.xlsx": ("标准文件：首次提交会写入 3 张业务表", lambda: fixtures.hero_config()),
    "hero_changed.xlsx": ("改了战士血量：写入后可看到逐字段修改", lambda: fixtures.hero_config(hp_overrides={"战士": [110, 130, 160, 190]})),
    "hero_more.xlsx": ("奖励明细多 10 行：行数波动超 50%，会被安全闸拦截，可在控制台放行", lambda: fixtures.hero_config(extra_reward_rows=10)),
    "hero_shifted.xlsx": ("内容整体下移 3 行：锚点定位仍能找到", lambda: fixtures.hero_config(shift_rows=3)),
    "hero_newcol.xlsx": ("插入了「备注」列：校验失败，不会写表", lambda: fixtures.hero_config(insert_note_col=True)),
}


def main() -> None:
    eng = create_engine(f"{resolve_ref('BIZ_MYSQL')}/{BIZ_DB}?charset=utf8mb4", future=True)
    with eng.begin() as c:
        for ddl in TABLES:
            c.execute(text(ddl))
    print(f"✓ 业务库 {BIZ_DB}：cfg_hero_base_hp / cfg_level_reward / cfg_global_switch")

    ds = next((d for d in datasources.list_all() if d["name"] == "本地业务库"), None)
    if not ds:
        ds = datasources.save({"name": "本地业务库", "host_ref": "BIZ_MYSQL", "db_name": BIZ_DB, "credential_ref": None})
    print(f"✓ 数据源「本地业务库」（引用名 BIZ_MYSQL）id={ds['id']}")

    p = next((x for x in pipelines.list_pipelines(None, None, None) if x["code"] == "hero_config"), None)
    if not p:
        f = files.save_upload(fixtures.hero_config(), "hero.xlsx", WHO)
        p = pipelines.create({"code": "hero_config", "name": "角色配置（演示）", "datasourceId": ds["id"],
                              "sampleFileId": f["fileId"], "description": "演示方案：两源 + 关联 + 校验 + 派生列 + 三个输出"}, WHO)
        d = pipelines.get_draft(p["id"])
        saved = pipelines.save_draft(p["id"], dsl_examples.hero_dsl(), d["draftVersion"], WHO)
        pipelines.publish(p["id"], saved["draftVersion"], "演示首版", WHO, "127.0.0.1")
    print(f"✓ 方案 hero_config 已发布，id={p['id']}")

    app = next((a for a in client_apps.list_all() if a["name"] == "演示调用方"), None)
    if not app:
        app = client_apps.save({"name": "演示调用方", "secretRef": "DEMO_APP_SECRET", "allowedPipelines": ["hero_config"],
                                "callbackAllowlist": []})
    print(f"✓ 调用方「演示调用方」AppKey={app['appKey']}（签名密钥引用名 DEMO_APP_SECRET）")

    OUT.mkdir(parents=True, exist_ok=True)
    for name, (desc, gen) in SAMPLES.items():
        (OUT / name).write_bytes(gen())
        print(f"  demo/{name:<18} {desc}")


if __name__ == "__main__":
    main()
