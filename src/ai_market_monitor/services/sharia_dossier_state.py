"""What state a factual research dossier is in — said once, for every reader.

A dossier is the folder of retained evidence and the factual summary built from it. Two
pipelines fill one in and five places read it back, and they did **not** agree on the
word that means "this folder is finished":

* the initial research pipeline wrote ``"completed"``;
* the source-change pipeline wrote ``"ready"`` for exactly the same situation;
* approval, publication and the worker queue accepted only ``"completed"``;
* the research pipeline's own replay check accepted either.

The result was a review case that could never be approved. The reviewer pressed
Approve and got "The factual research dossier is not complete." about a dossier that was
finished, because the reader had learned one of the two spellings and the writer had
used the other.

This module is the one owner of those words. Writers set :data:`COMPLETE`; readers ask
:func:`is_complete` or filter with :func:`complete_state_clause`. Nobody compares a
dossier state to a string of their own again.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute

#: Evidence is still being gathered.
RESEARCHING = "researching"

#: Gathering stopped without a usable factual summary — a source could not be read, or
#: the factual analysis failed. The case needs more evidence before anybody decides it.
NEEDS_EVIDENCE = "needs_evidence"

#: The folder is finished: the evidence is retained and the factual summary is written.
#: The value every writer stores from now on.
COMPLETE = "completed"

#: Every spelling of :data:`COMPLETE` that exists in stored data.
#:
#: ``"ready"`` is the historic spelling the source-change pipeline used. Rows written
#: before the two pipelines were brought together still carry it, and a stored row is
#: not rewritten by deploying new code, so readers keep accepting it. New rows only ever
#: use :data:`COMPLETE`.
COMPLETE_STATES: frozenset[str] = frozenset({COMPLETE, "ready"})

#: Every state a dossier can be in, for validation and for tests that walk the family.
ALL_STATES: frozenset[str] = frozenset({RESEARCHING, NEEDS_EVIDENCE, *COMPLETE_STATES})


def is_complete(state: str | None) -> bool:
    """True when this dossier is finished and may be reviewed."""

    return state in COMPLETE_STATES


def complete_state_clause(column: InstrumentedAttribute[str]) -> ColumnElement[bool]:
    """The same question as :func:`is_complete`, asked of the database.

    Queries that look for finished dossiers use this instead of writing their own
    equality test, so a query and an in-memory check can never disagree.
    """

    return column.in_(tuple(sorted(COMPLETE_STATES)))


def canonical_state(state: str | None) -> str:
    """The current spelling of a stored state, for display and for migrations."""

    return COMPLETE if is_complete(state) else (state or RESEARCHING)


def historic_complete_spellings() -> Iterable[str]:
    """The finished-dossier spellings that are no longer written, newest first."""

    return tuple(sorted(COMPLETE_STATES - {COMPLETE}))
