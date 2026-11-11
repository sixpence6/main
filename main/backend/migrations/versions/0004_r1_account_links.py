"""R1 one-time account setup/reset links

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column("used_at", sa.String(length=32), nullable=True),
        sa.Column("revoked_at", sa.String(length=32), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.CheckConstraint("purpose IN ('setup','reset')", name="ck_account_links_purpose"),
        sa.CheckConstraint("expires_at > created_at", name="ck_account_links_expiry"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_reason IS NOT NULL",
            name="ck_account_links_revocation_reason",
        ),
        sa.CheckConstraint(
            "used_at IS NULL OR revoked_at IS NULL",
            name="ck_account_links_single_terminal_state",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_account_links_token_digest"),
    )
    op.create_index(
        "ix_account_links_account_purpose",
        "account_links",
        ["account_id", "purpose", "created_at"],
    )
    op.create_index("ix_account_links_expiry", "account_links", ["expires_at"])


def downgrade() -> None:
    op.drop_table("account_links")
