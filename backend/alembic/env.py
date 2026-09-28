from sqlalchemy import create_engine

from alembic import context
from cellflow.config import get_settings
from cellflow.meta.tables import metadata

target_metadata = metadata


def run_migrations_online() -> None:
    engine = create_engine(get_settings().meta_db_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
