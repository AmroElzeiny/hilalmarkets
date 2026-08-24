"""Every name we give the database has to be a name PostgreSQL will accept.

PostgreSQL refuses any identifier longer than 63 characters. SQLite has no such limit, so
the whole offline test suite runs on a database that accepts names the real one rejects.
That gap is why this class of bug is only ever discovered by a deployment: the tests pass,
the container starts, and ``CREATE TABLE`` fails with ``IdentifierError``.

SQLAlchemy treats two kinds of name completely differently, and this is the part that
caught us out:

* A name **marked as produced by a naming convention** — anything wrapped in ``op.f()`` in
  a migration, and every unnamed constraint in the models, which the convention in
  ``db/base.py`` names automatically — is *shortened*. The compiler keeps the first 55
  characters and appends four hex digits of the name's own hash, so the result is under
  the limit, is stable, and is the same string every time.
* A name given as a **plain string** is *validated*. Over 63 characters it raises
  ``IdentifierError`` and the statement never runs.

Both paths are correct on their own. The failure is mixing them: the affiliate migration
wrote ``fk_affiliate_payout_requests_application_id_affiliate_applications`` as a plain
string, 66 characters, and the API container could not start. The models had the identical
constraint and were fine, because the convention had marked their copy.

So the rule is not "keep names short". It is **a name is either short enough, or marked**.
Twenty names in this schema are already over the limit and every one of them is marked;
they shorten to the same string on both sides, which is why the models and the database
agree on what each constraint is called.

The three checks below are the whole family, not the one that broke:

1. every identifier literal in every migration file,
2. every table the models define, compiled as real PostgreSQL,
3. no two shortened names landing on the same string.

The limit itself is read from SQLAlchemy's PostgreSQL dialect rather than typed here, so
this stays true if the limit ever changes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.elements import conv

import ai_market_monitor.db.models  # noqa: F401  (registers every table on the metadata)
from ai_market_monitor.db.base import Base

DIALECT = postgresql.dialect()
LIMIT: int = DIALECT.max_identifier_length

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIRECTORY = REPO_ROOT / "alembic" / "versions"
MIGRATION_FILES = sorted(MIGRATION_DIRECTORY.glob("*.py"))
TABLES = sorted(Base.metadata.sorted_tables, key=lambda table: table.name)

#: What the naming convention in ``db/base.py`` can produce.
IDENTIFIER_PREFIXES = ("fk_", "ix_", "uq_", "ck_", "pk_")

#: Names written straight into SQL rather than built by SQLAlchemy — triggers and stored
#: functions, which nothing marks and nothing shortens. PostgreSQL applies the same
#: 63-character limit to them, and a migration is where they are created.
RAW_SQL_NAME = re.compile(
    r"\b(?:"
    r"CREATE(?:\s+OR\s+REPLACE)?\s+(?:UNIQUE\s+)?"
    r"(?:INDEX|TRIGGER|FUNCTION|TABLE|VIEW|TYPE|SEQUENCE|MATERIALIZED\s+VIEW)"
    r"|ADD\s+CONSTRAINT"
    r")\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)

#: Alembic operations whose first positional argument is an identifier.
NAME_IN_FIRST_ARGUMENT = frozenset(
    {
        "create_index",
        "drop_index",
        "drop_constraint",
        "create_foreign_key",
        "create_unique_constraint",
        "create_check_constraint",
        "create_primary_key",
        "create_table",
        "drop_table",
        "rename_table",
    }
)


def _marked_literals(tree: ast.AST) -> set[int]:
    """The string literals sitting inside an ``op.f(...)`` call."""

    marked: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "f"
            and isinstance(function.value, ast.Name)
            and function.value.id == "op"
        ):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    marked.add(id(inner))
    return marked


def _identifier_nodes(tree: ast.AST) -> list[ast.Constant]:
    """Every string literal in a migration that becomes a database identifier.

    Deliberately narrow. A migration also holds long strings that are *not* identifiers —
    seeded text, docstrings, URLs — and flagging one of those as too long would be a
    finding nobody can act on.
    """

    found: dict[int, ast.Constant] = {}

    def remember(node: ast.expr | None) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found[id(node)] = node

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(IDENTIFIER_PREFIXES):
                found[id(node)] = node
            continue
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "name":
                remember(keyword.value)
                if (
                    isinstance(keyword.value, ast.Call)
                    and isinstance(keyword.value.func, ast.Attribute)
                    and keyword.value.func.attr == "f"
                ):
                    for argument in keyword.value.args:
                        remember(argument)
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr in NAME_IN_FIRST_ARGUMENT
            and isinstance(function.value, ast.Name)
            and function.value.id == "op"
            and node.args
        ):
            first = node.args[0]
            remember(first)
            if (
                isinstance(first, ast.Call)
                and isinstance(first.func, ast.Attribute)
                and first.func.attr == "f"
            ):
                for argument in first.args:
                    remember(argument)

    return list(found.values())


def _postgresql_renders(name: str) -> str:
    """What PostgreSQL is actually sent for a convention-produced name of this length."""

    probe = Table(
        "identifier_probe",
        MetaData(),
        Column("value", Integer),
        UniqueConstraint("value", name=conv(name)),
    )
    constraint = next(
        item for item in probe.constraints if isinstance(item, UniqueConstraint)
    )
    return DIALECT.identifier_preparer.format_constraint(constraint).strip('"')


def test_there_are_migrations_to_check() -> None:
    """A glob that matches nothing would make every check below pass on an empty list."""

    assert len(MIGRATION_FILES) >= 60
    assert len(TABLES) >= 150


@pytest.mark.parametrize("migration_path", MIGRATION_FILES, ids=lambda path: path.name)
def test_every_name_a_migration_writes_is_one_postgresql_accepts(
    migration_path: Path,
) -> None:
    """A plain string is validated; a marked one is shortened. Both must survive."""

    tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    marked = _marked_literals(tree)

    refused: list[str] = []
    for node in _identifier_nodes(tree):
        name = node.value
        if id(node) in marked:
            # Marked as convention-produced. SQLAlchemy shortens it rather than refusing
            # it, so the only thing to prove is that what it sends really does fit.
            rendered = _postgresql_renders(name)
            assert len(rendered) <= LIMIT, (
                f"{migration_path.name}:{node.lineno} shortened to {len(rendered)} "
                f"characters: {rendered}"
            )
            continue
        if len(name) > LIMIT:
            refused.append(
                f"{migration_path.name}:{node.lineno} writes a {len(name)}-character "
                f"plain name that PostgreSQL refuses: {name!r}. "
                f"Wrap it in op.f() so SQLAlchemy shortens it instead."
            )

    assert refused == []


@pytest.mark.parametrize("migration_path", MIGRATION_FILES, ids=lambda path: path.name)
def test_every_name_written_straight_into_sql_fits_too(migration_path: Path) -> None:
    """Triggers and stored functions go in as raw SQL, where nothing shortens anything.

    ``op.f()`` cannot help here and neither can the naming convention: whatever the string
    says is what PostgreSQL is asked for. Six triggers and six functions are created this
    way, and the limit applies to them exactly as it does to a constraint.
    """

    text = migration_path.read_text(encoding="utf-8")
    too_long = sorted(
        {name for name in RAW_SQL_NAME.findall(text) if len(name) > LIMIT}
    )
    assert too_long == []


@pytest.mark.parametrize("table", TABLES, ids=lambda table: table.name)
def test_every_table_compiles_as_real_postgresql(table: Table) -> None:
    """The models' own side of the same rule, on the dialect that actually enforces it."""

    statement = str(CreateTable(table).compile(dialect=DIALECT))
    for line in statement.splitlines():
        stripped = line.strip()
        if not stripped.startswith("CONSTRAINT "):
            continue
        emitted = stripped.split(" ", 2)[1].strip('"')
        assert len(emitted) <= LIMIT, f"{table.name} emits {len(emitted)}: {emitted}"

    for index in table.indexes:
        rendered = str(CreateIndex(index).compile(dialect=DIALECT))
        emitted = rendered.split(" INDEX ", 1)[1].split(" ON ", 1)[0].strip('"')
        assert len(emitted) <= LIMIT, f"{table.name} index emits {len(emitted)}: {emitted}"


@pytest.mark.parametrize("table", TABLES, ids=lambda table: table.name)
def test_shortening_never_makes_two_constraints_share_one_name(table: Table) -> None:
    """Shortening keeps a hash of the full name, so two long names must stay apart."""

    emitted = [
        _postgresql_renders(str(constraint.name))
        for constraint in table.constraints
        if constraint.name is not None
    ]
    assert len(emitted) == len(set(emitted)), f"{table.name}: {sorted(emitted)}"


def test_shortening_never_makes_two_indexes_share_one_name() -> None:
    """An index name is unique across the whole schema in PostgreSQL, not just its table."""

    seen: dict[str, str] = {}
    clashes: list[str] = []
    for table in TABLES:
        for index in table.indexes:
            emitted = _postgresql_renders(str(index.name))
            if emitted in seen and seen[emitted] != f"{table.name}.{index.name}":
                clashes.append(f"{emitted}: {seen[emitted]} and {table.name}.{index.name}")
            seen[emitted] = f"{table.name}.{index.name}"
    assert clashes == []


def test_the_models_and_the_migration_agree_on_the_name_that_broke_the_container() -> None:
    """The affiliate foreign key, both sides, on PostgreSQL.

    66 characters as written. This is the specific name that stopped the API container
    from starting, kept as a case because the two sides agreeing is the whole point of
    marking a name rather than hand-shortening it.
    """

    full = "fk_affiliate_payout_requests_application_id_affiliate_applications"
    assert len(full) > LIMIT

    from_the_models = str(
        CreateTable(Base.metadata.tables["affiliate_payout_requests"]).compile(
            dialect=DIALECT
        )
    )
    shortened = _postgresql_renders(full)

    assert len(shortened) <= LIMIT
    assert shortened in from_the_models
    assert full not in from_the_models

    migration = next(
        path for path in MIGRATION_FILES if path.name.startswith("b7e41c8d2f95")
    )
    tree = ast.parse(migration.read_text(encoding="utf-8"))
    marked = _marked_literals(tree)
    written = [node for node in _identifier_nodes(tree) if node.value == full]
    assert written, "the migration no longer writes this constraint"
    assert all(id(node) in marked for node in written)
