"""Record how an official link was found, and settle what counts as the same page.

Two things are wrong in the data this fixes.

**Nobody ever checked the links.** An official source carried a ``verification_state``
and nothing else, and the only code that wrote one set it to ``verified`` at the moment
the row was created — before the address had ever been fetched. "Verified" therefore
meant "somebody typed it". The Sharia research pipeline reads *only* verified sources,
so a link that had gone dead was still being handed to a reviewer as evidence. Five new
columns let a row say what is actually known about it:

``confidence``            how much the link is worth, 0 to 1
``discovery_layer``       who proposed it — a person, the approved identity, or a rule
``last_checked_at``       when it was last fetched; ``NULL`` means never
``content_published_at``  the newest dated item found, so staleness is measurable
``check_detail``          what the last check found, kept as a diagnostic

Existing rows get ``confidence`` 0.0 and ``last_checked_at`` ``NULL``. That is the
truthful starting point: nothing checked them, and the resolver will. Their
``verification_state`` is deliberately left alone — this adds knowledge, it does not
withdraw evidence from cases that are mid-review.

**Two spellings of one page were stored as two pages.** ``normalized_url`` decides when
two addresses are the same, and four separate private copies of that rule existed. Two
stripped a trailing slash and two kept it, so ``https://site.example/blog/`` and
``https://site.example/blog`` were one page to the importers and two pages to the
governance and identity code. The same page could therefore be registered twice under
one asset, fetched twice, and counted twice in a dossier's evidence completeness.

The code now has one owner for that rule, and it strips. This migration brings the
stored rows to the same spelling. Where stripping makes two rows collide, the earliest
row is kept, any evidence snapshots belonging to the later ones are repointed at it so
no retrieved evidence is lost, and the duplicates are removed.

The rule is written out again below rather than imported. A migration is a record of
what was done on a particular day; if the application's rule changes later, this file
must keep meaning what it meant when it ran.

Revision ID: a4d17e6b93c8
Revises: f2c60b83a915
Create Date: 2026-08-21
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op

revision: str = "a4d17e6b93c8"
down_revision: str | None = "f2c60b83a915"
branch_labels: str | None = None
depends_on: str | None = None


def _canonical(value: str) -> str:
    """The comparison form of a URL, frozen as it stood on 21 Aug 2026."""

    parsed = urlsplit((value or "").strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", ""))


def _settle_duplicate_spellings() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, canonical_asset_id, normalized_url, created_at "
            "FROM official_sources"
        )
    ).fetchall()
    groups: dict[tuple[object, str], list[tuple[object, object]]] = {}
    rewrites: list[tuple[object, str]] = []
    for row in rows:
        target = _canonical(row.normalized_url)
        groups.setdefault((row.canonical_asset_id, target), []).append(
            (row.created_at, row.id)
        )
        if target != row.normalized_url:
            rewrites.append((row.id, target))

    for members in groups.values():
        if len(members) < 2:
            continue
        # Oldest wins. It is the one existing snapshots and audit trails already name.
        ordered = sorted(members, key=lambda item: (str(item[0]), str(item[1])))
        keeper = ordered[0][1]
        for _created, loser in ordered[1:]:
            bind.execute(
                sa.text(
                    "UPDATE source_snapshots SET official_source_id = :keeper "
                    "WHERE official_source_id = :loser"
                ),
                {"keeper": keeper, "loser": loser},
            )
            bind.execute(
                sa.text("DELETE FROM official_sources WHERE id = :loser"),
                {"loser": loser},
            )
            rewrites = [item for item in rewrites if item[0] != loser]

    for row_id, target in rewrites:
        bind.execute(
            sa.text(
                "UPDATE official_sources SET normalized_url = :target WHERE id = :id"
            ),
            {"target": target, "id": row_id},
        )


def upgrade() -> None:
    op.add_column(
        "official_sources",
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "official_sources",
        sa.Column("discovery_layer", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "official_sources",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "official_sources",
        sa.Column("content_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "official_sources",
        sa.Column("check_detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    _settle_duplicate_spellings()


def downgrade() -> None:
    # The removed duplicate rows are not restored. They were two names for one page, and
    # inventing a second row back would recreate the defect rather than the data.
    op.drop_column("official_sources", "check_detail")
    op.drop_column("official_sources", "content_published_at")
    op.drop_column("official_sources", "last_checked_at")
    op.drop_column("official_sources", "discovery_layer")
    op.drop_column("official_sources", "confidence")
