"""allow audited local tracking closure on generation intents

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generation_intents", recreate="always") as batch:
        batch.drop_constraint("ck_generation_intents_status", type_="check")
        batch.create_check_constraint(
            "ck_generation_intents_status",
            "status IN ('accepted','executing','delivered','failed','abandoned','closed')",
        )


def downgrade() -> None:
    op.execute("UPDATE generation_intents SET status = 'abandoned' WHERE status = 'closed'")
    with op.batch_alter_table("generation_intents", recreate="always") as batch:
        batch.drop_constraint("ck_generation_intents_status", type_="check")
        batch.create_check_constraint(
            "ck_generation_intents_status",
            "status IN ('accepted','executing','delivered','failed','abandoned')",
        )
