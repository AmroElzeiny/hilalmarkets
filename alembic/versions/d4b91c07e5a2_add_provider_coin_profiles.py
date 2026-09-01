"""add provider coin profiles

Holds what a market-data provider publishes about a coin that is tradeable on an
exchange but carries no Shariah result. Facts only: there is deliberately no status
column, no eligibility flag and no methodology link, because nothing gathered here is a
religious conclusion.

Every constraint and index name goes through ``op.f()``. A convention-made name is
shortened by SQLAlchemy the same way the model side shortens it; a plain string over 63
characters is refused by PostgreSQL at deploy time, and SQLite would never have told us.

Revision ID: d4b91c07e5a2
Revises: c8f42a71d6b3
Create Date: 2026-08-30 19:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4b91c07e5a2"
down_revision: str | None = "c8f42a71d6b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_coin_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.String(length=40),
            server_default="coinmarketcap",
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=180), server_default="", nullable=False),
        sa.Column("slug", sa.String(length=180), server_default="", nullable=False),
        sa.Column("official_website", sa.Text(), nullable=True),
        sa.Column("whitepaper_url", sa.Text(), nullable=True),
        sa.Column("source_code_url", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("platform", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("date_added", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchanges", sa.JSON(), nullable=False),
        sa.Column("market_cap_usd", sa.Float(), nullable=True),
        sa.Column("volume_24h_usd", sa.Float(), nullable=True),
        sa.Column("provider_rank", sa.Integer(), nullable=True),
        sa.Column(
            "research_state",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canonical_asset_id", sa.Uuid(), nullable=True),
        sa.Column(
            "provider_flagged",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("provider_notice", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["canonical_asset_id"],
            ["canonical_assets.id"],
            name=op.f("fk_provider_coin_profiles_canonical_asset_id_canonical_assets"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_coin_profiles")),
        sa.UniqueConstraint(
            "provider",
            "symbol",
            name=op.f("uq_provider_coin_profile_provider_symbol"),
        ),
    )
    op.create_index(
        op.f("ix_provider_coin_profile_research_state"),
        "provider_coin_profiles",
        ["research_state", "refreshed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_coin_profile_symbol"),
        "provider_coin_profiles",
        ["symbol"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_provider_coin_profile_symbol"),
        table_name="provider_coin_profiles",
    )
    op.drop_index(
        op.f("ix_provider_coin_profile_research_state"),
        table_name="provider_coin_profiles",
    )
    op.drop_table("provider_coin_profiles")
