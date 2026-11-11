"""persist generation acceptance intents, usage ledger and live slots

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_intents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_account_id",
            sa.String(length=36),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "operator_account_id",
            sa.String(length=36),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approval_id",
            sa.String(length=36),
            sa.ForeignKey("approvals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("requirement_revision", sa.Integer(), nullable=False),
        sa.Column(
            "artifact_id",
            sa.String(length=36),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("artifact_digest", sa.String(length=64), nullable=False),
        sa.Column("reference_asset_id", sa.String(length=36), nullable=True),
        sa.Column("reference_usage", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("image_size", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="accepted"),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "status IN ('accepted','executing','delivered','failed','abandoned')",
            name="ck_generation_intents_status",
        ),
        sa.CheckConstraint(
            "reference_usage IN ('none','product','style','composition')",
            name="ck_generation_intents_reference_usage",
        ),
        sa.UniqueConstraint(
            "project_id", "request_id", name="uq_generation_intents_project_request"
        ),
    )
    op.create_index(
        "ix_generation_intents_project_created",
        "generation_intents",
        ["project_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_generation_intents_run_created",
        "generation_intents",
        ["run_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "generation_usage",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "intent_id",
            sa.String(length=36),
            sa.ForeignKey("generation_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.String(length=36),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("window_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("window_expires_ms", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "window_expires_ms > window_start_ms", name="ck_generation_usage_window"
        ),
        sa.UniqueConstraint("intent_id", name="uq_generation_usage_intent"),
    )
    op.create_index(
        "ix_generation_usage_account_window",
        "generation_usage",
        ["account_id", "window_start_ms"],
        unique=False,
    )
    op.create_table(
        "generation_slots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "intent_id",
            sa.String(length=36),
            sa.ForeignKey("generation_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("released_at", sa.String(length=32), nullable=True),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "scope IN ('personal','instance','project')", name="ck_generation_slots_scope"
        ),
    )
    op.create_index(
        "ix_generation_slots_intent", "generation_slots", ["intent_id"], unique=False
    )
    op.create_index(
        "ix_generation_slots_active",
        "generation_slots",
        ["scope", "scope_id", "released_at"],
        unique=False,
    )
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("intent_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_generations_intent_id",
            "generation_intents",
            ["intent_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_generations_intent", "generations", ["intent_id"], unique=False)
    with op.batch_alter_table("generation_jobs") as batch:
        batch.add_column(sa.Column("intent_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_generation_jobs_intent_id",
            "generation_intents",
            ["intent_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_generation_jobs_intent", "generation_jobs", ["intent_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_intent", table_name="generation_jobs")
    with op.batch_alter_table("generation_jobs") as batch:
        batch.drop_column("intent_id")
    op.drop_index("ix_generations_intent", table_name="generations")
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("intent_id")
    op.drop_index("ix_generation_slots_active", table_name="generation_slots")
    op.drop_index("ix_generation_slots_intent", table_name="generation_slots")
    op.drop_table("generation_slots")
    op.drop_index("ix_generation_usage_account_window", table_name="generation_usage")
    op.drop_table("generation_usage")
    op.drop_index("ix_generation_intents_run_created", table_name="generation_intents")
    op.drop_index("ix_generation_intents_project_created", table_name="generation_intents")
    op.drop_table("generation_intents")
