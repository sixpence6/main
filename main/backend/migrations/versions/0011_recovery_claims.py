"""add persistent workflow recovery claims

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("recovery_claim_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("recovery_claimed_at", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_column("recovery_claimed_at")
        batch.drop_column("recovery_claim_id")
