"""Add automated screen runs and the evidence receipts behind them.

Every constraint and index name goes through ``op.f()``. Two of the names below are over
PostgreSQL's 63-character limit before shortening, and ``op.f()`` is the difference
between "shortened the same way the model shortens it" and "the deployment will not
start". SQLite accepts both, so the offline suite can never tell them apart.

Revision ID: e7c3a2f019d8
Revises: d4b91c07e5a2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7c3a2f019d8"
down_revision = "d4b91c07e5a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automated_screen_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=180), nullable=False, server_default=""),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("activities", sa.JSON(), nullable=False),
        sa.Column("blocking_activities", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("holder_return", sa.String(length=40), nullable=True),
        sa.Column("holder_return_basis", sa.Text(), nullable=True),
        sa.Column("open_questions", sa.JSON(), nullable=False),
        sa.Column("documents_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "primary_documents_read", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automated_screen_runs")),
        sa.UniqueConstraint("symbol", name=op.f("uq_automated_screen_run_symbol")),
    )
    op.create_index(
        op.f("ix_automated_screen_run_verdict"),
        "automated_screen_runs",
        ["verdict", "decided_at"],
        unique=False,
    )

    op.create_table(
        "coin_evidence_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("characters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seeded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coin_evidence_documents")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["automated_screen_runs.id"],
            name=op.f("fk_coin_evidence_documents_run_id_automated_screen_runs"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "symbol", "url", name=op.f("uq_coin_evidence_document_symbol_url")
        ),
    )
    op.create_index(
        op.f("ix_coin_evidence_document_symbol"),
        "coin_evidence_documents",
        ["symbol", "category"],
        unique=False,
    )

    # The long-range market numbers the exchange ticker cannot answer. Server defaults
    # so the columns can be added to a table that already holds rows.
    op.add_column(
        "provider_coin_profiles",
        sa.Column("market_numbers", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "provider_coin_profiles",
        sa.Column("market_numbers_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("provider_coin_profiles", "market_numbers_at")
    op.drop_column("provider_coin_profiles", "market_numbers")
    op.drop_index(
        op.f("ix_coin_evidence_document_symbol"), table_name="coin_evidence_documents"
    )
    op.drop_table("coin_evidence_documents")
    op.drop_index(
        op.f("ix_automated_screen_run_verdict"), table_name="automated_screen_runs"
    )
    op.drop_table("automated_screen_runs")
