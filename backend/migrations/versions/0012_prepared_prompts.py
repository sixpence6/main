"""Persist reviewed prompt waiting facts, without inventing historical authorization.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _begin_sqlite_transaction():
    # pysqlite legacy transaction control does not BEGIN for DDL. Include both
    # this table and Alembic's version write in the outer migration transaction.
    bind = op.get_bind()
    if (
        not op.get_context().as_sql
        and bind.dialect.name == "sqlite"
        and not bind.connection.driver_connection.in_transaction
    ):
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    _begin_sqlite_transaction()
    op.create_table(
        "prepared_prompts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "intent_id", sa.String(36), sa.ForeignKey("generation_intents.id"), nullable=False
        ),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("approval_id", sa.String(36), sa.ForeignKey("approvals.id"), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("intent_id", "artifact_id", name="uq_prepared_prompt_candidate"),
        sa.CheckConstraint(
            "status IN ('awaiting_confirmation','invalidated')", name="ck_prepared_prompt_status"
        ),
    )


def downgrade():
    _begin_sqlite_transaction()
    # Downgrading a waiting/approved request would erase a safety boundary.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM prepared_prompts")).scalar():
        raise RuntimeError("Prepared prompt facts must be preserved; downgrade refused")
    op.drop_table("prepared_prompts")
