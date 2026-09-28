"""端到端验收环境准备：重建 e2e 元数据库与业务库、执行迁移、建业务表、生成测试文件与示例方案 DSL。

用法（由 e2e/run.sh 调用）：python e2e/prepare.py <输出目录>
依赖环境变量：CF_E2E_MYSQL_URL（不含库名）、CF_E2E_META_DB、CF_E2E_BIZ_DB
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]

import dsl_examples  # noqa: E402
import fixtures  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


def main(out: Path) -> None:
    url, meta, biz = os.environ["CF_E2E_MYSQL_URL"], os.environ["CF_E2E_META_DB"], os.environ["CF_E2E_BIZ_DB"]
    root = create_engine(url + "/?charset=utf8mb4", future=True)
    with root.begin() as c:
        for db in (meta, biz):
            c.execute(text(f"DROP DATABASE IF EXISTS `{db}`"))
            c.execute(text(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4"))
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(f"{url}/{biz}?charset=utf8mb4", future=True)
    with eng.begin() as c:
        c.execute(text("""CREATE TABLE cfg_hero_base_hp (
            id BIGINT AUTO_INCREMENT PRIMARY KEY, job_name VARCHAR(32) NOT NULL, lv INT NOT NULL, base_hp INT NOT NULL,
            hp INT NULL, UNIQUE KEY uk_job_lv (job_name, lv)) CHARSET=utf8mb4"""))
        c.execute(text("""CREATE TABLE cfg_level_reward (
            id BIGINT PRIMARY KEY, job_name VARCHAR(32) NOT NULL, lv INT NOT NULL, item_id BIGINT NOT NULL,
            item_count INT NOT NULL, item_name VARCHAR(64) NULL) CHARSET=utf8mb4"""))
        c.execute(text("CREATE TABLE cfg_global_switch (max_open_days INT NOT NULL, double_exp TINYINT(1) NOT NULL DEFAULT 0) CHARSET=utf8mb4"))
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "hero.xlsx": fixtures.hero_config(),
        "hero_more.xlsx": fixtures.hero_config(extra_reward_rows=10),
        "hero_shifted.xlsx": fixtures.hero_config(shift_rows=3),
        "hero_newcol.xlsx": fixtures.hero_config(insert_note_col=True),
    }
    for name, data in files.items():
        (out / name).write_bytes(data)
    (out / "hero_dsl.json").write_text(json.dumps(dsl_examples.hero_dsl(), ensure_ascii=False))
    print(f"e2e env ready: meta={meta} biz={biz} files={out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
