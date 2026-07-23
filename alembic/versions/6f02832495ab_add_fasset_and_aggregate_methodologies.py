"""add Fasset source support and aggregate methodology view

Revision ID: 6f02832495ab
Revises: 5ef17213849a
Create Date: 2026-07-23 12:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "6f02832495ab"
down_revision: str | None = "5ef17213849a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOMES = ["pass", "qualification", "fail", "not_applicable", "needs_evidence"]
USE_DECISIONS = [
    "covered",
    "qualified",
    "not_covered",
    "not_applicable",
    "under_review",
    "excluded",
]

FASSET_RULES = {
    "schema_version": "1",
    "criteria_version": "2026.07.criteria.1",
    "source_family": "fasset_shariah_reports",
    "source_adapter": "fasset",
    "executable": True,
    "spot_only": True,
    "publication_requires_admin_approval": True,
    "required_criteria": [
        {
            "key": "canonical_asset_identity",
            "label": "Canonical asset identity",
            "description": (
                "Verify the exact asset, network, identifiers, and exchange-market mapping."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["canonical_identity"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "official_methodology_reference",
            "label": "Published Fasset verdict",
            "description": (
                "Verify the retained Fasset profile, explicit verdict wording, asset identity, "
                "scope, and source snapshot without extending the source conclusion."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["official_fasset_reference"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "evidence_completeness",
            "label": "Evidence completeness and freshness",
            "description": (
                "Confirm mandatory factual sources are retained, current, and consistent with "
                "the reviewed profile and dossier."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["factual_dossier"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "source_scope_and_identity",
            "label": "Source scope and asset match",
            "description": (
                "Confirm that the Fasset verdict applies to this exact asset and record the "
                "limits of its asset-level and product-level scope."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": [
                "official_fasset_reference",
                "canonical_identity",
            ],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "use_specific_factual_review",
            "label": "HilalMarkets use-specific factual review",
            "description": (
                "Review each product use separately and do not extend an asset-level verdict "
                "to an unreviewed activity."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["factual_dossier"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
    ],
    "use_cases": [
        {
            "key": "asset_level_fasset_reference",
            "label": "Asset-level Fasset reference",
            "description": "The exact asset-level verdict published in the Fasset profile.",
            "required": True,
            "allowed_decisions": USE_DECISIONS,
            "criterion_keys": [
                "official_methodology_reference",
                "source_scope_and_identity",
            ],
            "evidence_categories": ["official_fasset_reference"],
            "default_scope": "Asset-level verdict in the retained Fasset Shariah profile.",
            "execution_blocking_decisions": [
                "not_covered",
                "not_applicable",
                "under_review",
                "excluded",
            ],
        },
        {
            "key": "spot_ownership_and_monitoring",
            "label": "Spot ownership and market monitoring",
            "description": "HilalMarkets spot-only ownership and non-execution monitoring scope.",
            "required": True,
            "allowed_decisions": USE_DECISIONS,
            "criterion_keys": ["use_specific_factual_review"],
            "evidence_categories": ["factual_dossier"],
            "default_scope": "Spot ownership and monitoring only; no leverage or execution.",
            "execution_blocking_decisions": [
                "not_covered",
                "not_applicable",
                "under_review",
                "excluded",
            ],
        },
    ],
}

FASSET_EVIDENCE = {
    "schema_version": "1",
    "mandatory_source_categories": [
        "canonical_identity",
        "official_fasset_reference",
        "factual_dossier",
    ],
    "minimum_evidence_completeness": 1.0,
    "maximum_source_age_days": 10,
    "critical_missing_fields": [
        "canonical_asset.identity_hash",
        "external_assessment.exact_status_wording",
        "external_assessment.source_authority",
        "external_assessment.structured_facts",
        "dossier.evidence_package_hash",
    ],
    "contradiction_policy": "block_any_unresolved",
    "review_cadence_days": 10,
}

ALL_RULES = {
    "schema_version": "1",
    "criteria_version": "2026.07.aggregate.1",
    "source_family": "approved_methodology_union",
    "source_adapter": "aggregate",
    "executable": True,
    "aggregate_view": True,
    "publication_requires_admin_approval": False,
    "source_priority_codes": [
        "SC_MALAYSIA_SAC_REFERENCE",
        "FASSET_SHARIAH_REPORTS",
    ],
    "required_criteria": [
        {
            "key": "active_published_source_assessment",
            "label": "Active published source assessment",
            "description": (
                "Include only an active, immutable assessment published under a real approved "
                "source methodology."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["published_methodology_assessment"],
            "qualification_rules": {},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "deduplicated_source_provenance",
            "label": "Deduplicated source provenance",
            "description": (
                "Keep one deterministic asset result while preserving the methodology and "
                "Passport that produced it."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["published_methodology_assessment"],
            "qualification_rules": {},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
    ],
    "use_cases": [
        {
            "key": "spot_ownership_and_monitoring",
            "label": "Spot ownership and market monitoring",
            "description": (
                "The approved use decision inherited from the selected source assessment."
            ),
            "required": True,
            "allowed_decisions": USE_DECISIONS,
            "criterion_keys": ["active_published_source_assessment"],
            "evidence_categories": ["published_methodology_assessment"],
            "default_scope": "Union view only; the source Passport remains authoritative.",
            "execution_blocking_decisions": [
                "not_covered",
                "not_applicable",
                "under_review",
                "excluded",
            ],
        }
    ],
}

ALL_EVIDENCE = {
    "schema_version": "1",
    "mandatory_source_categories": ["published_methodology_assessment"],
    "minimum_evidence_completeness": 1.0,
    "maximum_source_age_days": 3650,
    "critical_missing_fields": [
        "asset_assessment.id",
        "asset_assessment.methodology_id",
        "published_asset_assessment.integrity_hash",
    ],
    "contradiction_policy": "block_any_unresolved",
    "review_cadence_days": 10,
}


def upgrade() -> None:
    with op.batch_alter_table("external_assessments") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_family",
                sa.String(length=80),
                server_default="sc_malaysia_sac",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("source_reference", sa.String(length=160)))
        batch_op.add_column(
            sa.Column("structured_facts", sa.JSON(), server_default="{}", nullable=False)
        )
        batch_op.alter_column(
            "sac_meeting_number",
            existing_type=sa.String(length=80),
            nullable=True,
        )
        batch_op.alter_column(
            "decision_date",
            existing_type=sa.Date(),
            nullable=True,
        )
        batch_op.create_index(
            "ix_external_assessment_family_symbol",
            ["source_family", "asset_symbol"],
            unique=False,
        )

    now = datetime.now(UTC)
    methodology = sa.table(
        "sharia_methodologies",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("family_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("version", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("governing_body", sa.String()),
        sa.column("reviewer_group", sa.String()),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
        sa.column("rules_json", sa.JSON()),
        sa.column("evidence_requirements_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    bind.execute(
        methodology.update()
        .where(methodology.c.code.like("TRACEDGE_DEV_TEST_%"))
        .values(status="archived", effective_to=now, updated_at=now)
    )
    op.bulk_insert(
        methodology,
        [
            {
                "id": uuid4(),
                "code": "ALL_APPROVED_METHODOLOGIES",
                "family_id": None,
                "name": "All",
                "version": "2026.07",
                "description": (
                    "A deduplicated view of active published assessments from all approved "
                    "methodologies. It is not a separate Sharia ruling."
                ),
                "status": "active",
                "governing_body": "Source methodologies shown on each Passport",
                "reviewer_group": "HilalMarkets governance reviewers",
                "published_at": now,
                "effective_from": now,
                "effective_to": None,
                "rules_json": ALL_RULES,
                "evidence_requirements_json": ALL_EVIDENCE,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": uuid4(),
                "code": "FASSET_SHARIAH_REPORTS",
                "family_id": None,
                "name": "Fasset",
                "version": "2026.07",
                "description": (
                    "Asset-level verdicts and profile facts retained from Fasset's published "
                    "Shariah Reports, with separate HilalMarkets identity and use review."
                ),
                "status": "active",
                "governing_body": "Fasset published Shariah Reports",
                "reviewer_group": "HilalMarkets governance reviewers",
                "published_at": now,
                "effective_from": now,
                "effective_to": None,
                "rules_json": FASSET_RULES,
                "evidence_requirements_json": FASSET_EVIDENCE,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    methodology = sa.table(
        "sharia_methodologies",
        sa.column("code", sa.String()),
        sa.column("status", sa.String()),
        sa.column("effective_to", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    bind.execute(
        methodology.delete().where(
            methodology.c.code.in_(
                ["ALL_APPROVED_METHODOLOGIES", "FASSET_SHARIAH_REPORTS"]
            )
        )
    )
    bind.execute(
        methodology.update()
        .where(methodology.c.code.like("TRACEDGE_DEV_TEST_%"))
        .values(status="active", effective_to=None)
    )
    external_assessment = sa.table(
        "external_assessments",
        sa.column("source_family", sa.String()),
        sa.column("sac_meeting_number", sa.String()),
        sa.column("decision_date", sa.Date()),
    )
    bind.execute(
        external_assessment.delete().where(
            external_assessment.c.source_family != "sc_malaysia_sac"
        )
    )
    bind.execute(
        external_assessment.update()
        .where(external_assessment.c.sac_meeting_number.is_(None))
        .values(sac_meeting_number="Legacy imported assessment")
    )
    bind.execute(
        external_assessment.update()
        .where(external_assessment.c.decision_date.is_(None))
        .values(decision_date=sa.func.current_date())
    )
    with op.batch_alter_table("external_assessments") as batch_op:
        batch_op.drop_index("ix_external_assessment_family_symbol")
        batch_op.alter_column(
            "decision_date",
            existing_type=sa.Date(),
            nullable=False,
        )
        batch_op.alter_column(
            "sac_meeting_number",
            existing_type=sa.String(length=80),
            nullable=False,
        )
        batch_op.drop_column("structured_facts")
        batch_op.drop_column("source_reference")
        batch_op.drop_column("source_family")
