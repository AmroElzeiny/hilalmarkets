"""Index scan results by when they were evaluated.

Every question the product asks about a monitor's recent behaviour is the same shape:
*this version, since this moment*. Edge Health asks it for the last thirty days, decay
detection asks it for two windows, and the monitor cards ask it once per monitor on the
dashboard's front page.

``scan_results`` had no index that could answer it. The two it carried were
``(strategy_version_id, candle_closed_at)`` and ``(outcome, completion_score)`` — the
first is the right columns in the wrong order of usefulness, because the filter is on
``evaluated_at``, which is when the check *ran*, not on ``candle_closed_at``, which is
when the candle it read had closed. Those are different columns and the planner cannot
use one for the other.

So every one of those questions read the whole table. On 24 August 2026 that table was
549 MB holding 130,066 rows — and only 7.6 days of a 30-day retention window, so it had
not finished growing. Two of those reads were measured sitting in ``ClientWrite`` for 34
and 23 seconds at once.

The index is deliberately plain. A covering index with the grouped columns included would
turn the counts into index-only scans, but it would also add three columns to every one of
the half-million rows this table is heading for, on a server whose constraint is memory.
Range-scanning the two columns that do the filtering is the part that was missing.

Revision ID: c8f42a71d6b3
Revises: b7e41c8d2f95
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op

revision: str = "c8f42a71d6b3"
down_revision: str | None = "b7e41c8d2f95"
branch_labels: str | None = None
depends_on: str | None = None

#: Written through ``op.f()`` like every other name in this directory: it marks the string
#: as already produced by the naming convention, so a name over PostgreSQL's 63-character
#: limit is shortened deterministically instead of refused, and the model side — which
#: goes through the same convention — shortens it to exactly the same thing.
INDEX_NAME = "ix_scan_result_version_evaluated"


def upgrade() -> None:
    op.create_index(
        op.f(INDEX_NAME),
        "scan_results",
        ["strategy_version_id", "evaluated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f(INDEX_NAME), table_name="scan_results")
