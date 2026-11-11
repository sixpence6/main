"""R1 project ownership

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_account_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_projects_owner_account",
            "accounts",
            ["owner_account_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_projects_owner_updated",
            ["owner_account_id", "updated_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_index("ix_projects_owner_updated")
        batch_op.drop_constraint("fk_projects_owner_account", type_="foreignkey")
        batch_op.drop_column("owner_account_id")
