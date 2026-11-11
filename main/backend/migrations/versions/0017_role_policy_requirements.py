"""Persist role policy and accumulated requirements for each workflow run.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _begin() -> None:
    bind = op.get_bind()
    if (
        not op.get_context().as_sql
        and bind.dialect.name == "sqlite"
        and not bind.connection.driver_connection.in_transaction
    ):
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    _begin()
    op.add_column(
        "workflow_runs",
        sa.Column("role_policy", sa.String(24), nullable=False, server_default="legacy"),
    )
    op.add_column("workflow_runs", sa.Column("requirements_state_json", sa.Text(), nullable=True))


def downgrade() -> None:
    _begin()
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM workflow_runs WHERE role_policy <> 'legacy' OR requirements_state_json IS NOT NULL"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Persisted workflow requirements must be preserved; downgrade refused")
    op.drop_column("workflow_runs", "requirements_state_json")
    op.drop_column("workflow_runs", "role_policy")
