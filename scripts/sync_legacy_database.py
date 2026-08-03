"""Copy the rows of an older HilalMarkets database into the one the app runs on.

Why this exists
---------------
The application can be pointed at a different database than the one it used before —
a local SQLite file during development, a Postgres container afterwards. The new
database starts empty, so the screened assets disappear and the account has to be
created again. Nothing was lost; the app is simply reading somewhere else.

This copies the old rows into the current database without inventing anything:

* Rows are copied exactly as they were stored. No Sharia status, publication state or
  review decision is created, changed or promoted here. A row that was unpublished in
  the old database is still unpublished in the new one.
* A row that already exists in the target is matched, not duplicated. Matching uses
  the table's own unique constraints — the same rule the database itself enforces.
  When a match is found, every foreign key that pointed at the old row is rewritten to
  point at the row the target already has. That is what lets one account, seeded once
  on each side, end up as one account rather than two.
* Nothing in the target is deleted or overwritten. The copy only adds.
* Running it twice adds nothing the second time.

Usage
-----
    python scripts/sync_legacy_database.py --source ./ai_market_monitor.db --dry-run
    python scripts/sync_legacy_database.py --source ./ai_market_monitor.db

``--target`` defaults to the DATABASE_URL the application itself would use.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Table, UniqueConstraint, create_engine, inspect, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import ai_market_monitor.db.models  # noqa: E402,F401  (registers every table)
from ai_market_monitor.core.config import get_settings  # noqa: E402
from ai_market_monitor.db.base import Base  # noqa: E402

#: Alembic owns this one. Copying it would claim a schema state that is not there.
SKIPPED_TABLES = frozenset({"alembic_version"})


@dataclass
class TableReport:
    name: str
    read: int = 0
    inserted: int = 0
    matched: int = 0
    orphaned: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    orphan_reasons: set[str] = field(default_factory=set)


def _sync_url(url: str) -> str:
    """Turn the application's async URL into the plain one this script drives."""

    return (
        url.replace("+aiosqlite", "")
        .replace("+asyncpg", "+psycopg")
        .replace("+aiomysql", "+pymysql")
    )


def _source_url(value: str) -> str:
    if "://" in value:
        return _sync_url(value)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        raise SystemExit(f"No database file at {path}")
    return f"sqlite:///{path.as_posix()}"


def _single_primary_key(table: Table) -> str | None:
    columns = list(table.primary_key.columns)
    return columns[0].name if len(columns) == 1 else None


def _unique_column_groups(table: Table) -> list[tuple[str, ...]]:
    """Every set of columns the database itself treats as identifying a row.

    Used to recognise a row the target already holds under a different generated id —
    the same account seeded twice, the same methodology imported on both sides.
    """

    groups: list[tuple[str, ...]] = []
    primary = _single_primary_key(table)
    for constraint in table.constraints:
        # Only a unique constraint identifies a row. A foreign-key constraint also has
        # `.columns`, and treating one as identifying made unrelated rows look like the
        # same row: everything sharing a parent collapsed into the first row copied.
        if not isinstance(constraint, UniqueConstraint):
            continue
        names = tuple(column.name for column in constraint.columns)
        if names and names != (primary,):
            groups.append(names)
    for index in table.indexes:
        if not index.unique:
            continue
        names = tuple(column.name for column in index.columns)
        if names and names != (primary,) and names not in groups:
            groups.append(names)
    return groups


def _foreign_key_targets(table: Table) -> dict[str, str]:
    """Column name -> the table it points at, for rewriting matched ids."""

    mapping: dict[str, str] = {}
    for column in table.columns:
        for foreign_key in column.foreign_keys:
            mapping[column.name] = foreign_key.column.table.name
    return mapping


def _copyable_tables(
    source: Engine,
    target: Engine,
    skipped: frozenset[str],
) -> Iterator[Table]:
    source_tables = set(inspect(source).get_table_names())
    target_tables = set(inspect(target).get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name in SKIPPED_TABLES or table.name in skipped:
            continue
        if table.name in source_tables and table.name in target_tables:
            yield table


def _shared_columns(table: Table, source: Engine) -> list[str]:
    """Only the columns both sides have.

    An older file predates the newest migrations, so it simply does not carry the
    newest columns. Those keep their database default in the target rather than
    blocking the copy.
    """

    present = {column["name"] for column in inspect(source).get_columns(table.name)}
    return [column.name for column in table.columns if column.name in present]


def _match_existing(
    connection: Connection,
    table: Table,
    row: dict[str, Any],
    groups: Sequence[tuple[str, ...]],
    primary: str,
) -> Any | None:
    for names in groups:
        if any(row.get(name) is None for name in names):
            continue
        statement = select(table.c[primary]).where(
            *[table.c[name] == row[name] for name in names]
        )
        found = connection.execute(statement).scalar_one_or_none()
        if found is not None:
            return found
    return None


def _missing_parent(
    connection: Connection,
    table: Table,
    row: dict[str, Any],
    foreign_keys: dict[str, str],
) -> str | None:
    """Name the first foreign key whose row is not in the target, if any."""

    for name, referenced in foreign_keys.items():
        value = row.get(name)
        if value is None:
            continue
        parent = Base.metadata.tables.get(referenced)
        if parent is None:
            continue
        parent_key = _single_primary_key(parent)
        if parent_key is None:
            continue
        found = connection.execute(
            select(parent.c[parent_key]).where(parent.c[parent_key] == value)
        ).scalar_one_or_none()
        if found is None:
            return f"{name} points at a {referenced} row the old database did not contain"
    return None


def sync(
    source_url: str,
    target_url: str,
    *,
    dry_run: bool,
    skipped: frozenset[str] = frozenset(),
) -> int:
    source = create_engine(source_url)
    target = create_engine(target_url)
    #: old id -> the id that row has in the target, per table.
    identity: dict[str, dict[Any, Any]] = {}
    reports: list[TableReport] = []

    with source.connect() as reader, target.begin() as writer:
        for table in _copyable_tables(source, target, skipped):
            report = TableReport(table.name)
            reports.append(report)
            primary = _single_primary_key(table)
            columns = _shared_columns(table, source)
            if primary is None or primary not in columns:
                report.errors.append("no single-column primary key; skipped")
                continue
            groups = [
                names
                for names in _unique_column_groups(table)
                if all(name in columns for name in names)
            ]
            foreign_keys = {
                name: referenced
                for name, referenced in _foreign_key_targets(table).items()
                if name in columns
            }
            selection = select(*[table.c[name] for name in columns])
            for record in reader.execute(selection).mappings():
                report.read += 1
                row = dict(record)
                for name, referenced in foreign_keys.items():
                    remapped = identity.get(referenced, {})
                    if row[name] in remapped:
                        row[name] = remapped[row[name]]
                existing = _match_existing(writer, table, row, groups, primary)
                if existing is None:
                    existing = writer.execute(
                        select(table.c[primary]).where(table.c[primary] == row[primary])
                    ).scalar_one_or_none()
                if existing is not None:
                    report.matched += 1
                    identity.setdefault(table.name, {})[record[primary]] = existing
                    continue
                # Refuse a row whose parent is missing rather than inventing the parent.
                # The old file did not enforce foreign keys, so it can contain a row that
                # points at something that was never there.
                missing = _missing_parent(writer, table, row, foreign_keys)
                if missing is not None:
                    report.orphaned += 1
                    report.orphan_reasons.add(missing)
                    continue
                savepoint = writer.begin_nested()
                try:
                    writer.execute(table.insert().values(**row))
                except SQLAlchemyError as error:
                    savepoint.rollback()
                    report.failed += 1
                    if len(report.errors) < 3:
                        report.errors.append(str(error.orig or error).strip()[:200])
                else:
                    savepoint.commit()
                    report.inserted += 1
                    identity.setdefault(table.name, {})[record[primary]] = row[primary]
        if dry_run:
            writer.rollback()

    _print(reports, dry_run=dry_run)
    return 1 if any(report.failed for report in reports) else 0


def _print(reports: list[TableReport], *, dry_run: bool) -> None:
    active = [report for report in reports if report.read or report.errors]
    width = max((len(report.name) for report in active), default=10)
    print(f"{'table'.ljust(width)}   read  added  already there  incomplete  failed")
    inserted = matched = orphaned = failed = 0
    for report in active:
        print(
            f"{report.name.ljust(width)} {report.read:6d} {report.inserted:6d} "
            f"{report.matched:14d} {report.orphaned:11d} {report.failed:7d}"
        )
        for message in sorted(report.orphan_reasons):
            print(f"{' ' * width}   - {message}")
        for message in report.errors:
            print(f"{' ' * width}   ! {message}")
        inserted += report.inserted
        matched += report.matched
        orphaned += report.orphaned
        failed += report.failed
    print(
        f"\n{inserted} rows added, {matched} already present, "
        f"{orphaned} left out because the old database was itself incomplete, "
        f"{failed} could not be copied."
    )
    if dry_run:
        print("Dry run: nothing was written.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        help="Old database: a SQLite file path, or a full SQLAlchemy URL.",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Database to copy into. Defaults to the application's DATABASE_URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be copied and write nothing.",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="TABLE",
        help=(
            "Do not copy this table. Repeatable. Use it for the account tables when the "
            "same person already signed up again in the target: copying `users` there "
            "would add a second, unreachable account rather than restoring the first."
        ),
    )
    arguments = parser.parse_args()
    target_url = _sync_url(arguments.target or get_settings().database_url)
    source_url = _source_url(arguments.source)
    print(f"source: {source_url}")
    print(f"target: {_redacted(target_url)}\n")
    return sync(
        source_url,
        target_url,
        dry_run=arguments.dry_run,
        skipped=frozenset(arguments.skip),
    )


def _redacted(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


if __name__ == "__main__":
    raise SystemExit(main())
