"""One explicit final authorization and one image attempt per prepared intent.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _begin():
    bind = op.get_bind()
    if (
        not op.get_context().as_sql
        and bind.dialect.name == "sqlite"
        and not bind.connection.driver_connection.in_transaction
    ):
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    _begin()
    with op.batch_alter_table("prepared_prompts", recreate="always") as batch:
        batch.drop_constraint("ck_prepared_prompt_status", type_="check")
        batch.create_check_constraint(
            "ck_prepared_prompt_status",
            "status IN ('awaiting_confirmation','authorized','invalidated')",
        )
    op.create_table(
        "final_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        *[
            sa.Column(name, sa.String(36), sa.ForeignKey(target), nullable=False, unique=unique)
            for name, target, unique in [
                ("project_id", "projects.id", False),
                ("run_id", "workflow_runs.id", False),
                ("intent_id", "generation_intents.id", True),
                ("snapshot_id", "prepared_prompts.id", True),
                ("job_id", "generation_jobs.id", True),
                ("generation_id", "generations.id", True),
                ("owner_account_id", "accounts.id", False),
                ("operator_account_id", "accounts.id", False),
                ("session_id", "sessions.id", False),
            ]
        ],
        sa.Column("command_id", sa.String(36), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("project_id", "command_id", name="uq_final_command"),
    )


def downgrade():
    _begin()
    if op.get_bind().execute(sa.text("SELECT count(*) FROM final_authorizations")).scalar():
        raise RuntimeError("Final authorization facts must be preserved; downgrade refused")
    op.drop_table("final_authorizations")
    with op.batch_alter_table("prepared_prompts", recreate="always") as batch:
        batch.drop_constraint("ck_prepared_prompt_status", type_="check")
        batch.create_check_constraint(
            "ck_prepared_prompt_status", "status IN ('awaiting_confirmation','invalidated')"
        )
