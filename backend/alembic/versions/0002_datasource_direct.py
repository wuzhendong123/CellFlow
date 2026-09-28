"""数据源支持控制台直接填写连接信息（口令加密存储）

Revision ID: 0002_datasource_direct
Revises: 0001_initial
"""
import sqlalchemy as sa

from alembic import op

revision = "0002_datasource_direct"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

NEW = [
    sa.Column("conn_mode", sa.String(8), nullable=False, server_default="REF"),
    sa.Column("host", sa.String(255)),
    sa.Column("port", sa.Integer),
    sa.Column("username", sa.String(128)),
    sa.Column("password_enc", sa.Text),
]


def upgrade() -> None:
    # 0001 按当前表定义建表：全新库已经有这些列，只给旧库补列
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("cf_datasource")}
    for c in NEW:
        if c.name not in cols:
            op.add_column("cf_datasource", c)
    op.alter_column("cf_datasource", "host_ref", existing_type=sa.String(128), nullable=True)


def downgrade() -> None:
    for c in reversed(NEW):
        op.drop_column("cf_datasource", c.name)
    op.alter_column("cf_datasource", "host_ref", existing_type=sa.String(128), nullable=False)
