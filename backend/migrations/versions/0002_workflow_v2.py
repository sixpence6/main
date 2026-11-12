"""workflow v2 foundation

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("review_status", sa.String(length=24), nullable=False, server_default="quarantined")
        )
        batch_op.add_column(sa.Column("review_note", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_assets_review_status", ["review_status"], unique=False)

    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("workflow_job_id", sa.String(length=36), nullable=True))

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("intent_route", sa.String(length=16), nullable=True),
        sa.Column("intent_action", sa.String(length=24), nullable=True),
        sa.Column("requirement_revision", sa.Integer(), nullable=False),
        sa.Column("issue_code", sa.String(length=64), nullable=True),
        sa.Column("issue_message", sa.String(length=800), nullable=True),
        sa.Column("template_id", sa.String(length=48), nullable=True),
        sa.Column("template_params_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("status IN ('active','needs_input','blocked','completed','failed')", name="ck_workflow_runs_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "sequence_no", name="uq_workflow_runs_project_sequence"),
    )
    op.create_index("ix_workflow_runs_project_created", "workflow_runs", ["project_id", "created_at"])

    op.create_table(
        "role_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=8), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("capability", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("model_fingerprint", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.String(length=32), nullable=False),
        sa.Column("finished_at", sa.String(length=32), nullable=True),
        sa.CheckConstraint("capability IN ('TEXT','VISION','IMAGE_GEN')", name="ck_role_calls_capability"),
        sa.CheckConstraint("status IN ('ok','invalid','error','timeout')", name="ck_role_calls_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_role_calls_run_created", "role_calls", ["run_id", "started_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("status IN ('candidate','approved','rejected','superseded')", name="ck_artifacts_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "kind", "revision_no", name="uq_artifacts_run_kind_revision"),
    )
    op.create_index("ix_artifacts_run_kind", "artifacts", ["run_id", "kind"])

    op.create_table(
        "artifact_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("reviewer_role", sa.String(length=40), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("target_kind IN ('artifact','image')", name="ck_artifact_reviews_target_kind"),
        sa.CheckConstraint("decision IN ('pass','revise','reject','unknown')", name="ck_artifact_reviews_decision"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifact_reviews_artifact", "artifact_reviews", ["artifact_id", "created_at"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("approval_type", sa.String(length=32), nullable=False),
        sa.Column("artifact_digest", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=32), nullable=False),
        sa.Column("requirement_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("referenced_material_digest", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("model_fingerprint", sa.String(length=200), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("revoked_at", sa.String(length=32), nullable=True),
        sa.Column("revocation_reason", sa.String(length=64), nullable=True),
        sa.CheckConstraint("approved_by IN ('user','output_reviewer','quality_compliance')", name="ck_approvals_approved_by"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_project_type_revoked", "approvals", ["project_id", "approval_type", "revoked_at"])

    op.create_table(
        "revision_budgets",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("max_allowed", sa.Integer(), nullable=False),
        sa.CheckConstraint("used >= 0 AND used <= max_allowed", name="ck_revision_budgets_used"),
        sa.CheckConstraint("max_allowed = 3", name="ck_revision_budgets_max"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "artifact_kind"),
    )

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("generation_id", sa.String(length=36), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("prompt_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("confirmed_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("submit_attempted", sa.Integer(), nullable=False),
        sa.Column("remote_task_id", sa.String(length=255), nullable=True),
        sa.Column("result_asset_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("status IN ('created','submit_pending','submitted','remote_pending','result_fetched','failed','unknown','quarantined','approved','rejected','published')", name="ck_generation_jobs_status"),
        sa.ForeignKeyConstraint(["generation_id"], ["generations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_generation_jobs_project_created", "generation_jobs", ["project_id", "created_at"])

    op.create_table(
        "deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("generation_job_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=120), nullable=False),
        sa.Column("caption", sa.String(length=800), nullable=False),
        sa.Column("asset_digest", sa.String(length=64), nullable=False),
        sa.Column("approved_at", sa.String(length=32), nullable=True),
        sa.Column("published_at", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("status IN ('approved','published','revoked')", name="ck_deliveries_status"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generation_job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["manifest_artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", name="uq_deliveries_asset"),
    )
    op.create_index("ix_deliveries_project_status", "deliveries", ["project_id", "status"])

    op.create_table(
        "legacy_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ref_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approved_by", sa.String(length=32), nullable=True),
        sa.Column("note", sa.String(length=800), nullable=True),
        sa.Column("reviewed_at", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("kind IN ('message','generation','asset')", name="ck_legacy_items_kind"),
        sa.CheckConstraint("status IN ('legacy_unreviewed','approved')", name="ck_legacy_items_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "ref_id", name="uq_legacy_items_kind_ref"),
    )
    op.create_index("ix_legacy_items_project", "legacy_items", ["project_id", "status"])

    op.create_table(
        "diagnostic_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_diagnostic_events_created", "diagnostic_events", ["created_at"])

    # v1 model output becomes legacy_unreviewed; user-authored content stays readable.
    op.execute("UPDATE assets SET review_status = 'legacy_unreviewed' WHERE kind = 'generated'")
    op.execute(
        "INSERT INTO legacy_items (id, project_id, kind, ref_id, status, created_at) "
        "SELECT lower(hex(randomblob(16))), project_id, 'message', id, 'legacy_unreviewed', created_at "
        "FROM messages WHERE role = 'assistant'"
    )
    op.execute(
        "INSERT INTO legacy_items (id, project_id, kind, ref_id, status, created_at) "
        "SELECT lower(hex(randomblob(16))), project_id, 'generation', id, 'legacy_unreviewed', created_at "
        "FROM generations"
    )
    op.execute(
        "INSERT INTO legacy_items (id, project_id, kind, ref_id, status, created_at) "
        "SELECT lower(hex(randomblob(16))), project_id, 'asset', id, 'legacy_unreviewed', created_at "
        "FROM assets WHERE kind = 'generated'"
    )


def downgrade() -> None:
    op.drop_table("diagnostic_events")
    op.drop_table("legacy_items")
    op.drop_table("deliveries")
    op.drop_table("generation_jobs")
    op.drop_table("revision_budgets")
    op.drop_table("approvals")
    op.drop_table("artifact_reviews")
    op.drop_table("artifacts")
    op.drop_table("role_calls")
    op.drop_table("workflow_runs")
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_column("workflow_job_id")
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index("ix_assets_review_status")
        batch_op.drop_column("review_note")
        batch_op.drop_column("review_status")
        batch_op.drop_column("sha256")
