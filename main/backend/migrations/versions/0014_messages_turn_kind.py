"""add message turn kind

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _begin() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.in_transaction():
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    _begin()
    with op.batch_alter_table("messages", recreate="always") as batch:
        batch.add_column(
            sa.Column("turn_kind", sa.String(length=16), nullable=False, server_default="workflow")
        )
        batch.create_check_constraint(
            "ck_messages_turn_kind", "turn_kind IN ('workflow','conversation','prompt')"
        )


def downgrade() -> None:
    _begin()
    with op.batch_alter_table("messages", recreate="always") as batch:
        batch.drop_constraint("ck_messages_turn_kind", type_="check")
        batch.drop_column("turn_kind")
