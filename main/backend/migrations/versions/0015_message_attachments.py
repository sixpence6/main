"""add message attachments

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _begin() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.in_transaction():
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    _begin()
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("usage", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("content_version", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "usage IN ('none','product','style','composition')",
            name="ck_message_attachments_usage",
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0",
            name="ck_message_attachments_dimensions_positive",
        ),
        sa.CheckConstraint(
            "byte_size >= 0",
            name="ck_message_attachments_byte_size_non_negative",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", name="uq_message_attachments_asset"),
        sa.UniqueConstraint("message_id", name="uq_message_attachments_message"),
    )
    op.create_index(
        "ix_message_attachments_project", "message_attachments", ["project_id"], unique=False
    )


def downgrade() -> None:
    _begin()
    op.drop_index("ix_message_attachments_project", table_name="message_attachments")
    op.drop_table("message_attachments")
