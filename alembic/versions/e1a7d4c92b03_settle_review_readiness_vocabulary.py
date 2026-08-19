"""Settle the words a finished dossier and a decidable case are stored with.

Two stored values made review cases impossible to approve, and no code change alone
fixes them, because a row already written is not rewritten by deploying new code.

1. ``asset_research_dossiers.state`` held two spellings for one situation. The initial
   research pipeline wrote ``completed``; the source-change pipeline wrote ``ready`` for
   exactly the same finished folder of evidence. Approval accepted only ``completed``, so
   a reviewer pressing Approve was told "The factual research dossier is not complete."
   about a dossier that was finished. Every stored ``ready`` becomes ``completed``.

2. The methodology import pack wrote ``maximum_source_age_days = 1`` into the three
   active methodologies. The pack states no evidence-age policy at all; the number was
   invented in code, and it meant retained evidence expired one day after it was
   gathered. Every case became undecidable the day after its research ran. The rows are
   moved to 90 days — the value this repository governed for SC Malaysia before the pack
   replaced it, and the default of ``SHARIA_PACK_EVIDENCE_MAX_AGE_DAYS``.

3. Cases still waiting for a decision were left pointing at a methodology version the
   pack had archived. Approval refuses those with "The review methodology is not active",
   and no screen offered any way to move them, so a *version number* changing underneath
   a case stranded it for good. Each one is moved to the current active version of the
   same authority. Only cases nobody has decided are moved: a recorded decision keeps the
   exact version it was taken under, which is what makes the record readable later.

None of this touches a Shariah status, a decision, or a published Passport. The first
corrects a spelling, the second restores an evidence-age policy that was overwritten, and
the third points undecided cases at the methodology version that is actually in force.

Revision ID: e1a7d4c92b03
Revises: d9f31c4e6a72
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e1a7d4c92b03"
down_revision: str | None = "d9f31c4e6a72"
branch_labels: str | None = None
depends_on: str | None = None

#: The spelling every writer uses now.
_COMPLETE = "completed"

#: The spelling the source-change pipeline used for the same thing.
_HISTORIC_COMPLETE = "ready"

#: Days of evidence life restored where the import pack wrote 1.
_GOVERNED_EVIDENCE_AGE_DAYS = 90

#: Only the value the code invented is corrected. A number an operator set on purpose is
#: left exactly as it is.
_INVENTED_EVIDENCE_AGE_DAYS = 1

#: Cases a person has already decided. Their methodology version is never moved: the
#: decision was taken under it, and its own publication checks that it still matches.
#: This is ``sharia_import_pack.DECIDED_CASE_STATES``, repeated here because a migration
#: must keep working when the application code around it changes.
_DECIDED_CASE_STATES = ("approved", "published", "rejected", "stored", "superseded")

#: How ``ShariaMethodologyStatus.ACTIVE`` is stored: the enum's **value**, lower case, not
#: its Python name. Writing ``'ACTIVE'`` here matched no row at all and did nothing
#: silently — a migration that quietly moves nothing is worse than one that fails.
_ACTIVE_METHODOLOGY_STATUS = "active"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE asset_research_dossiers SET state = :complete WHERE state = :historic"
        ).bindparams(complete=_COMPLETE, historic=_HISTORIC_COMPLETE)
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, evidence_requirements_json FROM sharia_methodologies "
            "WHERE evidence_requirements_json IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        requirements = row[1]
        if not isinstance(requirements, dict):
            continue
        updated = dict(requirements)
        changed = False
        for key in ("maximum_source_age_days", "review_cadence_days"):
            if updated.get(key) == _INVENTED_EVIDENCE_AGE_DAYS:
                updated[key] = _GOVERNED_EVIDENCE_AGE_DAYS
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    "UPDATE sharia_methodologies SET evidence_requirements_json = :value "
                    "WHERE id = :id"
                ).bindparams(
                    sa.bindparam("value", value=updated, type_=sa.JSON()),
                    sa.bindparam("id", value=row[0]),
                )
            )
    _move_undecided_cases_to_the_active_methodology(bind)


def _move_undecided_cases_to_the_active_methodology(bind: sa.engine.Connection) -> None:
    """Point cases still waiting for a decision at the version now in force."""

    placeholders = ", ".join(f":state{index}" for index in range(len(_DECIDED_CASE_STATES)))
    statement = sa.text(
        "UPDATE sharia_review_cases AS c "
        "SET methodology_id = active.id "
        "FROM sharia_methodologies AS archived "
        "JOIN sharia_methodologies AS active "
        "  ON active.code = archived.code AND active.status = :active_status "
        "WHERE c.methodology_id = archived.id "
        "  AND archived.status <> :active_status "
        "  AND c.done_at IS NULL "
        f"  AND c.state NOT IN ({placeholders})"
    ).bindparams(
        sa.bindparam("active_status", value=_ACTIVE_METHODOLOGY_STATUS),
        *(
            sa.bindparam(f"state{index}", value=state)
            for index, state in enumerate(_DECIDED_CASE_STATES)
        ),
    )
    try:
        bind.execute(statement)
    except (sa.exc.OperationalError, sa.exc.ProgrammingError):
        # SQLite has no UPDATE ... FROM in older versions. The same move, one row at a
        # time, so a development database gets the identical result.
        rows = bind.execute(
            sa.text(
                "SELECT c.id, active.id FROM sharia_review_cases AS c "
                "JOIN sharia_methodologies AS archived ON archived.id = c.methodology_id "
                "JOIN sharia_methodologies AS active "
                "  ON active.code = archived.code AND active.status = :active_status "
                "WHERE archived.status <> :active_status AND c.done_at IS NULL "
                f"AND c.state NOT IN ({placeholders})"
            ).bindparams(
                sa.bindparam("active_status", value=_ACTIVE_METHODOLOGY_STATUS),
                *(
                    sa.bindparam(f"state{index}", value=state)
                    for index, state in enumerate(_DECIDED_CASE_STATES)
                ),
            )
        ).fetchall()
        for case_id, methodology_id in rows:
            bind.execute(
                sa.text(
                    "UPDATE sharia_review_cases SET methodology_id = :methodology_id "
                    "WHERE id = :case_id"
                ).bindparams(methodology_id=methodology_id, case_id=case_id)
            )


def downgrade() -> None:
    # Two things are not put back. The historic dossier spelling meant the same thing, so
    # restoring it would only make approval refuse finished dossiers again. A case moved
    # to the methodology version in force is not moved back to an archived one, because
    # nothing could then decide it. Only the evidence-age number is reversed.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, evidence_requirements_json FROM sharia_methodologies "
            "WHERE evidence_requirements_json IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        requirements = row[1]
        if not isinstance(requirements, dict):
            continue
        updated = dict(requirements)
        changed = False
        for key in ("maximum_source_age_days", "review_cadence_days"):
            if updated.get(key) == _GOVERNED_EVIDENCE_AGE_DAYS:
                updated[key] = _INVENTED_EVIDENCE_AGE_DAYS
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    "UPDATE sharia_methodologies SET evidence_requirements_json = :value "
                    "WHERE id = :id"
                ).bindparams(
                    sa.bindparam("value", value=updated, type_=sa.JSON()),
                    sa.bindparam("id", value=row[0]),
                )
            )
