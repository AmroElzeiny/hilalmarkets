"""Add immutable approved-watchlist snapshots.

An approval bound to a Favorites list could only be checked by recomputing a hash from
the live membership rows. Once a row changed there was nothing left that said what the
approval had covered. This table stores the membership itself, keyed by its own content
hash, so the record survives every later edit.

Revision ID: c3d91e2f70aa
Revises: b7f42a8d9c11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d91e2f70aa"
down_revision: str | None = "b7f42a8d9c11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approved_watchlist_snapshots",
        sa.Column("watchlist_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(length=80), nullable=False),
        sa.Column("exchange", sa.String(length=40), nullable=False),
        sa.Column("market_type", sa.String(length=20), nullable=False),
        sa.Column(
            "quote_currencies",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("members", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("member_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unresolved_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["approved_watchlists.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_approved_watchlist_snapshot_hash"),
    )
    op.create_index(
        "ix_approved_watchlist_snapshot_list",
        "approved_watchlist_snapshots",
        ["watchlist_id", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approved_watchlist_snapshot_list",
        table_name="approved_watchlist_snapshots",
    )
    op.drop_table("approved_watchlist_snapshots")
