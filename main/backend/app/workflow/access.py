from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Asset
from app.workflow.tables import Approval, Artifact, Delivery


@dataclass(frozen=True)
class AccessDecision:
    allow: bool
    reason_code: str | None = None


def _checks(approval: Approval) -> dict[str, object]:
    try:
        parsed = json.loads(approval.checks_json)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _matches_asset(asset: Asset, approval: Approval, checks: dict[str, object]) -> bool:
    if checks.get("asset_id") not in (None, asset.id):
        return False
    expected = checks.get("asset_digest")
    if expected is not None and asset.sha256 is not None and expected != asset.sha256:
        return False
    if asset.sha256 is None:
        return False
    return True


async def can_access_asset(
    session: AsyncSession,
    settings: Settings,
    asset: Asset,
    *,
    download: bool = False,
) -> AccessDecision:
    """Single gate for every asset read or download.

    The workflow may evolve, but no route may bypass this fail-closed check.
    """
    del settings  # reserved for path roots and future policy inputs
    if asset.review_status == "legacy_unreviewed":
        return AccessDecision(False, "LEGACY_UNREVIEWED")
    if asset.review_status == "rejected":
        return AccessDecision(False, "ASSET_REJECTED")

    if asset.kind == "reference":
        return AccessDecision(True)

    if asset.kind != "generated":
        return AccessDecision(False, "ASSET_UNAVAILABLE")
    if asset.review_status != "approved":
        return AccessDecision(False, "ASSET_NOT_REVIEWED")

    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.project_id == asset.project_id,
            Approval.approval_type == "image",
            Approval.revoked_at.is_(None),
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    )
    if approval is None:
        return AccessDecision(False, "APPROVAL_MISSING")
    checks = _checks(approval)
    if not _matches_asset(asset, approval, checks):
        # The quality report itself is an acceptable fallback carrier of the
        # reviewed asset identity, as long as its digest is the one approved.
        if approval.artifact_id:
            artifact = await session.get(Artifact, approval.artifact_id)
            if artifact is not None:
                try:
                    payload = json.loads(artifact.payload_json)
                except ValueError:
                    payload = {}
                if isinstance(payload, dict) and payload.get("asset_id") == asset.id:
                    checks = {**checks, "asset_id": asset.id}
                else:
                    return AccessDecision(False, "DIGEST_MISMATCH")
            else:
                return AccessDecision(False, "DIGEST_MISMATCH")
        else:
            return AccessDecision(False, "DIGEST_MISMATCH")

    if download:
        delivery = await session.scalar(
            select(Delivery)
            .where(
                Delivery.asset_id == asset.id,
                Delivery.status == "published",
            )
            .order_by(Delivery.published_at.desc())
            .limit(1)
        )
        if delivery is None:
            return AccessDecision(False, "NOT_PUBLISHED")
        if delivery.asset_digest != asset.sha256:
            return AccessDecision(False, "DIGEST_MISMATCH")
        if not delivery.filename:
            return AccessDecision(False, "NOT_PUBLISHED")
    return AccessDecision(True)


__all__ = ["AccessDecision", "can_access_asset"]
