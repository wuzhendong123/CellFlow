"""初始元数据表（TECH_DESIGN §3）

Revision ID: 0001_initial
Revises:
"""
from alembic import op
from cellflow.meta.tables import metadata

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 初始版本直接按表定义建表；后续结构变更使用增量迁移
    metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    metadata.drop_all(op.get_bind(), checkfirst=True)
