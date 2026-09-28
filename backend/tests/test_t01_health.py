from sqlalchemy import inspect

from cellflow.meta.db import get_engine
from cellflow.meta.tables import metadata


def test_healthz_all_green(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "OK"
    assert body["data"]["healthy"] is True, body


def test_migration_creates_all_tables(meta_db):
    names = set(inspect(get_engine()).get_table_names())
    expected = {t.name for t in metadata.sorted_tables}
    assert expected <= names
    assert len(expected) == 17


def test_migration_is_idempotent_and_reversible(meta_db):
    import os

    from alembic.config import Config

    from alembic import command

    here = os.path.dirname(__file__)
    cfg = Config(os.path.join(here, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "..", "alembic"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert "cf_pipeline" not in inspect(get_engine()).get_table_names()
    command.upgrade(cfg, "head")
    assert "cf_pipeline" in inspect(get_engine()).get_table_names()
