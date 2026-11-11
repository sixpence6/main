"""add generation quota overrides

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_quota_limits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=True),
        sa.Column("personal_slots", sa.Integer(), nullable=True),
        sa.Column("instance_slots", sa.Integer(), nullable=True),
        sa.Column(
            "updated_by",
            sa.String(length=36),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("scope", "scope_id", name="uq_generation_quota_limits_scope"),
        sa.CheckConstraint(
            "scope IN ('instance','account')",
            name="ck_generation_quota_limits_scope",
        ),
        sa.CheckConstraint(
            "daily_limit IS NULL OR daily_limit > 0",
            name="ck_generation_quota_limits_daily",
        ),
        sa.CheckConstraint(
            "personal_slots IS NULL OR personal_slots > 0",
            name="ck_generation_quota_limits_personal",
        ),
        sa.CheckConstraint(
            "instance_slots IS NULL OR instance_slots > 0",
            name="ck_generation_quota_limits_instance",
        ),
        sa.CheckConstraint(
            "scope = 'instance' OR instance_slots IS NULL",
            name="ck_generation_quota_limits_account_instance",
        ),
    )


def downgrade() -> None:
    op.drop_table("generation_quota_limits")
