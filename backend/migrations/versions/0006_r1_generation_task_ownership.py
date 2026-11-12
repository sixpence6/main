"""persist generation task ownership and authorization

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_account_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("operator_account_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("authorization_ref", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_generations_owner_account",
            "accounts",
            ["owner_account_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_generations_operator_account",
            "accounts",
            ["operator_account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_generations_owner_created", ["owner_account_id", "created_at"], unique=False
        )
    op.execute(
        "UPDATE generations SET owner_account_id = ("
        "SELECT owner_account_id FROM projects WHERE projects.id = generations.project_id"
        ") WHERE owner_account_id IS NULL"
    )
    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_account_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("operator_account_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("authorization_ref", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_generation_jobs_owner_account",
            "accounts",
            ["owner_account_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_generation_jobs_operator_account",
            "accounts",
            ["operator_account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_generation_jobs_owner_created", ["owner_account_id", "created_at"], unique=False
        )
    op.execute(
        "UPDATE generation_jobs SET owner_account_id = ("
        "SELECT owner_account_id FROM projects WHERE projects.id = generation_jobs.project_id"
        ") WHERE owner_account_id IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_generation_jobs_owner_created")
        batch_op.drop_constraint("fk_generation_jobs_operator_account", type_="foreignkey")
        batch_op.drop_constraint("fk_generation_jobs_owner_account", type_="foreignkey")
        batch_op.drop_column("authorization_ref")
        batch_op.drop_column("operator_account_id")
        batch_op.drop_column("owner_account_id")
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_index("ix_generations_owner_created")
        batch_op.drop_constraint("fk_generations_operator_account", type_="foreignkey")
        batch_op.drop_constraint("fk_generations_owner_account", type_="foreignkey")
        batch_op.drop_column("authorization_ref")
        batch_op.drop_column("operator_account_id")
        batch_op.drop_column("owner_account_id")
