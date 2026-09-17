"""Nothing that points at a sign-in may stop that sign-in from being removed.

Unlinking Telegram and WhatsApp deletes the sign-in row for that app. The risk-note
acceptance pointed at it with ``ON DELETE RESTRICT``, so anyone who had accepted inside
the bot could never unlink. The offline suite runs on SQLite, which ignores foreign keys
unless asked, so only a real deployment found it.

The rule is checked on every reference to ``user_identities`` in the schema, not only
the one that broke: a reference is cleared (``SET NULL`` on a nullable column) or removed
with the row (``CASCADE``). A record that must outlive the sign-in keeps its own copy of
what it needs, as ``disclaimer_acceptances`` does.
"""

from __future__ import annotations

import pytest

import ai_market_monitor.db.models  # noqa: F401  (registers every table on the metadata)
from ai_market_monitor.db.base import Base

REFERENCES = sorted(
    (
        (table.name, foreign_key)
        for table in Base.metadata.sorted_tables
        for foreign_key in table.foreign_keys
        if foreign_key.column.table.name == "user_identities"
    ),
    key=lambda item: (item[0], item[1].parent.name),
)


def test_the_schema_has_references_to_sign_ins_to_check() -> None:
    assert REFERENCES, "no reference to user_identities was found; the check below is empty"


@pytest.mark.parametrize(
    "table,foreign_key",
    REFERENCES,
    ids=[f"{table}.{foreign_key.parent.name}" for table, foreign_key in REFERENCES],
)
def test_a_reference_to_a_sign_in_never_blocks_removing_it(table, foreign_key) -> None:
    rule = (foreign_key.ondelete or "NO ACTION").upper()
    assert rule in {"SET NULL", "CASCADE"}, (
        f"{table}.{foreign_key.parent.name} uses ON DELETE {rule}: removing the sign-in "
        "it points at would fail"
    )
    if rule == "SET NULL":
        assert foreign_key.parent.nullable, (
            f"{table}.{foreign_key.parent.name} is SET NULL on a NOT NULL column"
        )
