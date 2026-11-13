"""lead tags and internal note

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("note")
        batch.drop_column("tags")
