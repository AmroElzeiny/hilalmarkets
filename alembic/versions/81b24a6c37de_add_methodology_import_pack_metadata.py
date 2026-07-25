"""add methodology import-pack provenance and rights gates

Revision ID: 81b24a6c37de
Revises: 70a1395b26cf
Create Date: 2026-07-24 12:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "81b24a6c37de"
down_revision: str | None = "70a1395b26cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
METHOD_VERSION = "2026.07-pack.1"

OUTCOMES = ["pass", "qualification", "fail", "not_applicable", "needs_evidence"]
USE_DECISIONS = [
    "covered",
    "qualified",
    "not_covered",
    "not_applicable",
    "under_review",
    "excluded",
]


def _rules(*, source_family: str, source_adapter: str, authority_label: str) -> dict:
    authority_key = f"asset_level_{source_adapter}_reference"
    return {
        "schema_version": "1",
        "criteria_version": "import-pack.1.0.0",
        "source_family": source_family,
        "source_adapter": source_adapter,
        "executable": True,
        "spot_only": True,
        "publication_requires_admin_approval": True,
        "required_criteria": [
            {
                "key": "canonical_asset_identity",
                "label": "Canonical asset identity",
                "description": (
                    "Verify name, chain, native or token type, contract addresses, official "
                    "website, and exact market mapping. A ticker alone is insufficient."
                ),
                "required": True,
                "allowed_outcomes": OUTCOMES,
                "evidence_categories": ["canonical_identity"],
                "qualification_rules": {"written_reason_required": True},
                "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
            },
            {
                "key": "official_methodology_reference",
                "label": f"Published {authority_label} reference",
                "description": (
                    "Verify the retained source wording, authority, date, identity, scope, and "
                    "source snapshot without extending the external conclusion."
                ),
                "required": True,
                "allowed_outcomes": OUTCOMES,
                "evidence_categories": ["official_external_reference"],
                "qualification_rules": {"written_reason_required": True},
                "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
            },
            {
                "key": "evidence_completeness",
                "label": "Evidence completeness and freshness",
                "description": (
                    "Confirm mandatory official sources are retained, current, complete, and "
                    "consistent with the reviewed factual profile."
                ),
                "required": True,
                "allowed_outcomes": OUTCOMES,
                "evidence_categories": ["factual_dossier"],
                "qualification_rules": {"written_reason_required": True},
                "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
            },
            {
                "key": "source_scope_and_identity",
                "label": "Source scope and exact asset match",
                "description": (
                    "Confirm the external result applies to this exact asset and record its "
                    "jurisdictional, asset-level, product, and rights limitations."
                ),
                "required": True,
                "allowed_outcomes": OUTCOMES,
                "evidence_categories": [
                    "official_external_reference",
                    "canonical_identity",
                ],
                "qualification_rules": {"written_reason_required": True},
                "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
            },
            {
                "key": "use_specific_factual_review",
                "label": "HilalMarkets use-specific factual review",
                "description": (
                    "Review each product use separately; an asset-level external result does "
                    "not automatically cover staking, lending, yield, wrappers, or derivatives."
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
                "key": authority_key,
                "label": f"Asset-level {authority_label} reference",
                "description": "The exact asset-level status stated by the external source.",
                "required": True,
                "allowed_decisions": USE_DECISIONS,
                "criterion_keys": [
                    "official_methodology_reference",
                    "source_scope_and_identity",
                ],
                "evidence_categories": ["official_external_reference"],
                "default_scope": "Only the exact asset-level external reference.",
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
                "description": "HilalMarkets spot-only, non-execution monitoring scope.",
                "required": True,
                "allowed_decisions": USE_DECISIONS,
                "criterion_keys": ["use_specific_factual_review"],
                "evidence_categories": ["factual_dossier"],
                "default_scope": "Spot monitoring only; no leverage or trade execution.",
                "execution_blocking_decisions": [
                    "not_covered",
                    "not_applicable",
                    "under_review",
                    "excluded",
                ],
            },
        ],
    }


def _evidence(*, maximum_age_days: int, rights_clearance: bool = False) -> dict:
    critical = [
        "canonical_asset.identity_hash",
        "external_assessment.source_row_id",
        "external_assessment.exact_status_wording",
        "external_assessment.source_authority",
        "external_assessment.source_snapshot_id",
        "dossier.evidence_package_hash",
    ]
    return {
        "schema_version": "1",
        "mandatory_source_categories": [
            "canonical_identity",
            "official_external_reference",
            "factual_dossier",
        ],
        "minimum_evidence_completeness": 1.0,
        "maximum_source_age_days": maximum_age_days,
        "critical_missing_fields": critical,
        "contradiction_policy": "block_any_unresolved",
        "review_cadence_days": maximum_age_days,
        "publication_rights_clearance_required": rights_clearance,
    }


def upgrade() -> None:
    with op.batch_alter_table("external_assessments") as batch_op:
        batch_op.add_column(sa.Column("methodology_id", sa.Uuid()))
        batch_op.add_column(sa.Column("source_row_id", sa.String(length=160)))
        batch_op.add_column(sa.Column("normalized_status", sa.String(length=80)))
        batch_op.add_column(sa.Column("publication_gate", sa.String(length=120)))
        batch_op.add_column(sa.Column("rights_state", sa.String(length=160)))
        batch_op.add_column(
            sa.Column(
                "commercial_display_allowed",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "manual_verification_required",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("rights_clearance_reference", sa.Text()))
        batch_op.add_column(sa.Column("rights_cleared_by_user_id", sa.Uuid()))
        batch_op.add_column(sa.Column("rights_cleared_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column("source_detail_extraction_state", sa.String(length=120))
        )
        batch_op.add_column(sa.Column("source_detail_snapshot_id", sa.Uuid()))
        batch_op.add_column(
            sa.Column("source_detail_fields", sa.JSON(), server_default="{}", nullable=False)
        )
        batch_op.add_column(sa.Column("passport_seed_id", sa.String(length=180)))
        batch_op.add_column(
            sa.Column(
                "passport_seed_snapshot",
                sa.JSON(),
                server_default="{}",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("enrichment_task_id", sa.String(length=180)))
        batch_op.add_column(
            sa.Column(
                "enrichment_state",
                sa.String(length=40),
                server_default="not_queued",
                nullable=False,
            )
        )
        batch_op.create_foreign_key(
            "fk_external_assessments_methodology_id_sharia_methodologies",
            "sharia_methodologies",
            ["methodology_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_external_assessments_rights_cleared_by_user_id_users",
            "users",
            ["rights_cleared_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_external_source_detail_snapshot",
            "source_snapshots",
            ["source_detail_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_external_assessment_methodology_source_row",
            ["methodology_id", "source_row_id"],
        )
        batch_op.create_index(
            "ix_external_assessment_enrichment_state",
            ["enrichment_state", "mapping_state"],
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
    codes = [
        "SC_MALAYSIA_SAC_REFERENCE",
        "FASSET_SHARIAH_REPORTS",
        "SHARIAH_REVIEW_BUREAU",
    ]
    bind.execute(
        methodology.update()
        .where(
            methodology.c.code.in_(codes),
            methodology.c.status == "active",
        )
        .values(status="archived", effective_to=now, updated_at=now)
    )
    existing_versions = set(
        bind.execute(
            sa.select(methodology.c.code).where(
                methodology.c.code.in_(codes),
                methodology.c.version == METHOD_VERSION,
            )
        ).scalars()
    )
    definitions = [
        {
            "code": "SC_MALAYSIA_SAC_REFERENCE",
            "name": "SC Malaysia SAC Digital Assets Reference",
            "description": (
                "Official asset-level references published by the Shariah Advisory Council "
                "of the Securities Commission Malaysia. Unpublished coin-specific reasoning "
                "is never reconstructed."
            ),
            "governing_body": (
                "Shariah Advisory Council of the Securities Commission Malaysia"
            ),
            "rules_json": _rules(
                source_family="sc_malaysia_sac",
                source_adapter="sc_malaysia",
                authority_label="SC Malaysia SAC",
            ),
            "evidence_requirements_json": _evidence(maximum_age_days=1),
        },
        {
            "code": "FASSET_SHARIAH_REPORTS",
            "name": "Fasset Shariah Reports",
            "description": (
                "Asset-level Fasset report references retained separately from HilalMarkets "
                "factual research and gated by human review and rights review."
            ),
            "governing_body": "Fasset",
            "rules_json": _rules(
                source_family="fasset_shariah_reports",
                source_adapter="fasset",
                authority_label="Fasset",
            ),
            "evidence_requirements_json": _evidence(
                maximum_age_days=1,
                rights_clearance=True,
            ),
        },
        {
            "code": "SHARIAH_REVIEW_BUREAU",
            "name": "Shariah Review Bureau",
            "description": (
                "Versioned external preliminary-research references retained with strict "
                "commercial-display rights controls and separate HilalMarkets factual review."
            ),
            "governing_body": "Shariyah Review Bureau W.L.L.",
            "rules_json": _rules(
                source_family="shariah_review_bureau",
                source_adapter="srb",
                authority_label="Shariah Review Bureau",
            ),
            "evidence_requirements_json": _evidence(
                maximum_age_days=1,
                rights_clearance=True,
            ),
        },
    ]
    for definition in definitions:
        if definition["code"] in existing_versions:
            bind.execute(
                methodology.update()
                .where(
                    methodology.c.code == definition["code"],
                    methodology.c.version == METHOD_VERSION,
                )
                .values(
                    status="active",
                    effective_to=None,
                    updated_at=now,
                )
            )
            continue
        bind.execute(
            methodology.insert().values(
                id=uuid4(),
                family_id=None,
                version=METHOD_VERSION,
                status="active",
                reviewer_group="HilalMarkets governance reviewers",
                published_at=now,
                effective_from=now,
                effective_to=None,
                created_at=now,
                updated_at=now,
                **definition,
            )
        )


def downgrade() -> None:
    methodology = sa.table(
        "sharia_methodologies",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("version", sa.String()),
        sa.column("status", sa.String()),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    now = datetime.now(UTC)
    codes = [
        "SC_MALAYSIA_SAC_REFERENCE",
        "FASSET_SHARIAH_REPORTS",
        "SHARIAH_REVIEW_BUREAU",
    ]
    bind.execute(
        methodology.delete().where(
            methodology.c.code.in_(codes),
            methodology.c.version == METHOD_VERSION,
        )
    )
    for code in codes:
        previous_id = bind.execute(
            sa.select(methodology.c.id)
            .where(
                methodology.c.code == code,
                methodology.c.status == "archived",
            )
            .order_by(methodology.c.published_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if previous_id is not None:
            bind.execute(
                methodology.update()
                .where(methodology.c.id == previous_id)
                .values(status="active", effective_to=None, updated_at=now)
            )
    with op.batch_alter_table("external_assessments") as batch_op:
        batch_op.drop_index("ix_external_assessment_enrichment_state")
        batch_op.drop_constraint(
            "uq_external_assessment_methodology_source_row",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_external_assessments_rights_cleared_by_user_id_users",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_external_source_detail_snapshot",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_external_assessments_methodology_id_sharia_methodologies",
            type_="foreignkey",
        )
        batch_op.drop_column("enrichment_state")
        batch_op.drop_column("enrichment_task_id")
        batch_op.drop_column("passport_seed_snapshot")
        batch_op.drop_column("passport_seed_id")
        batch_op.drop_column("source_detail_fields")
        batch_op.drop_column("source_detail_snapshot_id")
        batch_op.drop_column("source_detail_extraction_state")
        batch_op.drop_column("rights_cleared_at")
        batch_op.drop_column("rights_cleared_by_user_id")
        batch_op.drop_column("rights_clearance_reference")
        batch_op.drop_column("manual_verification_required")
        batch_op.drop_column("commercial_display_allowed")
        batch_op.drop_column("rights_state")
        batch_op.drop_column("publication_gate")
        batch_op.drop_column("normalized_status")
        batch_op.drop_column("source_row_id")
        batch_op.drop_column("methodology_id")
