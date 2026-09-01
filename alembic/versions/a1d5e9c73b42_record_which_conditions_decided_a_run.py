"""Record which screening conditions decided an automated run.

Two lists per run, and each answers a question a reader is owed:

``matched_conditions``  which approved rules actually refused this coin
``proposed_matches``    which unapproved rules matched, and so changed nothing

Neither can be recomputed later, because both depend on what was approved *on the day
the verdict was made* rather than on what is approved now.

A third list, ``unchecked_conditions``, was written and removed before this migration
ever ran. It named the approved rules that reading a website cannot settle — riba
al-fadl, a debt ratio. It was dropped because it appeared identically on every coin and
nobody, owner or Shariah provider, could act on it. What the screen does not attempt is
now stated once in the methodology note rather than repeated on every verdict.

Revision ID: a1d5e9c73b42
Revises: e7c3a2f019d8
Create Date: 2026-08-31

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1d5e9c73b42"
down_revision = "e7c3a2f019d8"
branch_labels = None
depends_on = None

_COLUMNS = ("matched_conditions", "proposed_matches")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "automated_screen_runs",
            sa.Column(
                name,
                sa.JSON(),
                nullable=False,
                # Existing rows were decided before the register existed, so the honest
                # value for all three is "nothing recorded" rather than a guess.
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("automated_screen_runs", name)
