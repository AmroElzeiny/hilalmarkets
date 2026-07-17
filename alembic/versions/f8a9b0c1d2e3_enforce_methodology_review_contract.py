"""enforce methodology review contract

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
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

SC_RULES = {
    "schema_version": "1",
    "criteria_version": "2026.03.criteria.1",
    "source_family": "sc_malaysia_sac",
    "source_adapter": "sc_malaysia",
    "executable": True,
    "spot_only": True,
    "publication_requires_admin_approval": True,
    "required_criteria": [
        {
            "key": "canonical_asset_identity",
            "label": "Canonical asset identity",
            "description": (
                "Verify the asset, network, identifiers, and exact exchange-market mapping."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["canonical_identity"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "official_methodology_reference",
            "label": "Official asset-level reference",
            "description": (
                "Verify the retained SC Malaysia wording, authority, decision date, scope, "
                "and source snapshot without inferring unpublished reasoning."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["official_sc_reference"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "evidence_completeness",
            "label": "Evidence completeness and freshness",
            "description": (
                "Confirm mandatory factual sources are complete, current, retained, and "
                "consistent with the reviewed dossier."
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
                "Confirm that the external reference applies to this exact asset and record "
                "the limits of its regulatory and product scope."
            ),
            "required": True,
            "allowed_outcomes": OUTCOMES,
            "evidence_categories": ["official_sc_reference", "canonical_identity"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
        },
        {
            "key": "use_specific_factual_review",
            "label": "HilalMarkets use-specific factual review",
            "description": (
                "Review each listed use separately and do not extend the asset-level SC "
                "reference to an unreviewed use."
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
            "key": "asset_level_sc_reference",
            "label": "Asset-level SC Malaysia reference",
            "description": "The exact asset-level status stated by the official external source.",
            "required": True,
            "allowed_decisions": USE_DECISIONS,
            "criterion_keys": ["official_methodology_reference", "source_scope_and_identity"],
            "evidence_categories": ["official_sc_reference"],
            "default_scope": (
                "Asset-level status in the SC Malaysia regulated digital-assets "
                "framework."
            ),
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
            "default_scope": (
                "Spot ownership and research monitoring only; no execution or leverage."
            ),
            "execution_blocking_decisions": [
                "not_covered",
                "not_applicable",
                "under_review",
                "excluded",
            ],
        },
        *[
            {
                "key": key,
                "label": label,
                "description": description,
                "required": True,
                "allowed_decisions": USE_DECISIONS,
                "criterion_keys": ["use_specific_factual_review"],
                "evidence_categories": ["factual_dossier"],
                "default_scope": scope,
                "execution_blocking_decisions": [],
            }
            for key, label, description, scope in [
                (
                    "native_staking",
                    "Native staking",
                    "Native protocol staking, where applicable to this exact asset.",
                    "Native protocol staking only; third-party yield products are separate.",
                ),
                (
                    "third_party_lending",
                    "Third-party lending",
                    "Lending or borrowing products supplied by a third party.",
                    "Third-party lending and borrowing products.",
                ),
                (
                    "yield_products",
                    "Yield products",
                    "Yield, earn, or reward products beyond native asset ownership.",
                    "Third-party or protocol yield products.",
                ),
                (
                    "leveraged_products",
                    "Leveraged products",
                    "Products that introduce borrowing or leveraged exposure.",
                    "Leveraged products outside HilalMarkets spot-only scope.",
                ),
                (
                    "futures_perpetuals_derivatives",
                    "Futures, perpetuals, and derivatives",
                    "Derivative exposure rather than ownership of the reviewed spot asset.",
                    "Derivatives outside HilalMarkets spot-only scope.",
                ),
                (
                    "wrapped_bridged_representations",
                    "Wrapped and bridged representations",
                    "A separate token or representation requiring its own identity and review.",
                    "Wrapped or bridged representations are separate assets and reviews.",
                ),
                (
                    "other_material_uses",
                    "Other material uses",
                    "Any other material use that is not covered by the named categories.",
                    "Any material use not listed above requires its own review.",
                ),
            ]
        ],
    ],
}

SC_EVIDENCE_REQUIREMENTS = {
    "schema_version": "1",
    "mandatory_source_categories": [
        "canonical_identity",
        "official_sc_reference",
        "factual_dossier",
    ],
    "minimum_evidence_completeness": 1.0,
    "maximum_source_age_days": 90,
    "critical_missing_fields": [
        "canonical_asset.identity_hash",
        "external_assessment.exact_status_wording",
        "external_assessment.source_authority",
        "external_assessment.regulatory_scope",
        "dossier.evidence_package_hash",
    ],
    "contradiction_policy": "block_any_unresolved",
    "review_cadence_days": 90,
}


def upgrade() -> None:
    with op.batch_alter_table("sharia_methodologies") as batch_op:
        batch_op.add_column(sa.Column("effective_to", sa.DateTime(timezone=True)))

    with op.batch_alter_table("sharia_review_decisions") as batch_op:
        batch_op.add_column(sa.Column("methodology_version", sa.String(length=32)))
        batch_op.add_column(
            sa.Column("methodology_criteria_version", sa.String(length=80))
        )
        batch_op.add_column(sa.Column("methodology_criteria_hash", sa.String(length=64)))
        batch_op.add_column(
            sa.Column("use_case_decisions", sa.JSON(), server_default="[]", nullable=False)
        )

    methodologies = sa.table(
        "sharia_methodologies",
        sa.column("code", sa.String()),
        sa.column("rules_json", sa.JSON()),
        sa.column("evidence_requirements_json", sa.JSON()),
    )
    op.get_bind().execute(
        methodologies.update()
        .where(methodologies.c.code == "SC_MALAYSIA_SAC_REFERENCE")
        .values(
            rules_json=SC_RULES,
            evidence_requirements_json=SC_EVIDENCE_REQUIREMENTS,
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("sharia_review_decisions") as batch_op:
        batch_op.drop_column("use_case_decisions")
        batch_op.drop_column("methodology_criteria_hash")
        batch_op.drop_column("methodology_criteria_version")
        batch_op.drop_column("methodology_version")
    with op.batch_alter_table("sharia_methodologies") as batch_op:
        batch_op.drop_column("effective_to")
