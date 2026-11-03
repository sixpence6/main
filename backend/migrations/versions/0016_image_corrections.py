"""add image corrections

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _begin() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.in_transaction():
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    _begin()
    op.create_table(
        "image_corrections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("intent_id", sa.String(length=36), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=False),
        sa.Column("source_asset_id", sa.String(length=36), nullable=False),
        sa.Column("source_asset_sha256", sa.String(length=64), nullable=False),
        sa.Column("operator_account_id", sa.String(length=36), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="prepared"
        ),
        sa.Column("adapter_id", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("request_digest", sa.String(length=64), nullable=True),
        sa.Column("correction_generation_id", sa.String(length=36), nullable=True),
        sa.Column("correction_job_id", sa.String(length=36), nullable=True),
        sa.Column("result_asset_id", sa.String(length=36), nullable=True),
        sa.Column("result_asset_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "status IN ('prepared','authorized','reviewing','approved','rejected','failed')",
            name="ck_image_corrections_status",
        ),
        sa.ForeignKeyConstraint(["correction_generation_id"], ["generations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["correction_job_id"], ["generation_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["intent_id"], ["generation_intents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_job_id"], ["generation_jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correction_generation_id", name="uq_image_corrections_generation"),
        sa.UniqueConstraint("correction_job_id", name="uq_image_corrections_job"),
        sa.UniqueConstraint("intent_id", name="uq_image_corrections_intent"),
    )
    op.create_index(
        "ix_image_corrections_project_run",
        "image_corrections",
        ["project_id", "run_id"],
        unique=False,
    )
    op.create_index(
        "ix_image_corrections_source",
        "image_corrections",
        ["source_job_id"],
        unique=False,
    )


def downgrade() -> None:
    _begin()
    op.drop_index("ix_image_corrections_source", table_name="image_corrections")
    op.drop_index(
        "ix_image_corrections_project_run", table_name="image_corrections"
    )
    op.drop_table("image_corrections")
