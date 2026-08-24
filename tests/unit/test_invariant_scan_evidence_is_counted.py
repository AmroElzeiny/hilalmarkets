"""A scan row is counted, never collected.

``scan_results`` is the biggest table this product writes and every row of it carries a
``proof_summary`` JSON blob — the evidence for one check of one coin. A reader that asks
for whole rows in order to count them therefore pulls a month of evidence blobs into
whichever process asked.

That is not a theory. On 23 August 2026 ``cockpit_service.edge_health`` read thirty days
of ``scan_results`` as whole rows, once per monitor, on ``/home``. One signed-in visit
took a single api worker to 1.02 GB, the container's 1280 MB ceiling had the kernel kill
it in the middle of the request, and Caddy answered the customer **502**. The kernel wrote
it down as ``Killed process 679577 (python) anon-rss:1045268kB``; Caddy wrote it down as
six ``/home`` requests dying after 5 to 40 seconds each.

Two rules follow, and this file holds both:

1. **Every whole-row read of ``ScanResult`` is bounded.** ``select(ScanResult)`` without a
   ``.limit(...)`` anywhere in the same expression is refused. Counting, summing and
   grouping are unaffected — ``select(func.count(...), ...)`` names columns, not the
   entity, so it never matches.
2. **Counting in the database gives the same answer as counting the rows.** The tally is
   only safe to use because it is not an approximation, so it is checked against the
   row-by-row arithmetic it replaced, for every outcome the enum has.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_market_monitor.cockpit_service import ScanEvidenceTally, tally_scan_evidence
from ai_market_monitor.db.models.enums import ScanOutcome
from ai_market_monitor.engine.data_freshness import (
    RATIO_WHEN_UNKNOWN,
    measure_freshness,
    timeframe_ms,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: The entities whose rows are expensive, because each one carries an evidence blob:
#: ``ScanResult.proof_summary`` and ``Alert.proof_receipt``. Both are read by pages, both
#: grow for as long as a monitor runs, and neither is ever needed whole in order to be
#: counted. Named here so the rule reads as one rule over both.
GUARDED_ENTITIES = ("ScanResult", "Alert")


def _python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _guarded_entity_in(node: ast.AST) -> str | None:
    """``select(ScanResult)`` — the entity itself, not columns taken from it."""

    if not isinstance(node, ast.Call):
        return None
    if not (isinstance(node.func, ast.Name) and node.func.id == "select"):
        return None
    for argument in node.args:
        if isinstance(argument, ast.Name) and argument.id in GUARDED_ENTITIES:
            return argument.id
    return None


def _is_whole_entity_select(node: ast.AST) -> bool:
    return _guarded_entity_in(node) is not None


#: Columns that can only ever match one row. An equality against one of these narrows a
#: select to a single row, which is bounded whether or not anybody wrote ``.limit(1)``.
#: Demanding a limit there would only teach people to add meaningless ones.
UNIQUE_COLUMNS = {"id", "deduplication_key"}


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    current: ast.AST | None = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current
        current = parents.get(current)
    return None


def _chain_root(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    """The outermost call of ``select(...).where(...).order_by(...).limit(...)``.

    ``.where`` wraps ``select``, not the other way round, so anything asked about the
    whole statement has to be asked from the top of the chain down.
    """

    current = node
    while True:
        parent = parents.get(current)
        if isinstance(parent, ast.Attribute) and parent.value is current:
            grandparent = parents.get(parent)
            if isinstance(grandparent, ast.Call) and grandparent.func is parent:
                current = grandparent
                continue
        return current


def _where_arguments(scope: ast.AST) -> list[ast.expr]:
    """Only what is inside ``.where(...)``.

    A join condition reads like a narrowing one — ``.join(D, D.alert_id == Alert.id)``
    mentions a primary key — but it selects nothing; it only says how two tables line up.
    Counting it made an unbounded read of every delivered alert look safe.
    """

    arguments: list[ast.expr] = []
    for inner in ast.walk(scope):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "where"
        ):
            arguments.extend(inner.args)
    return arguments


def _narrows_to_one_row(scope: ast.AST) -> bool:
    """``.where(Alert.id == alert_id)`` — one row by a key that is unique.

    The unique column has to be on the **model's** side of the comparison. ``setup.id`` on
    the right of ``Alert.setup_instance_id == setup.id`` is one object's id being used to
    find *many* rows, which is the opposite of narrowing.
    """

    for argument in _where_arguments(scope):
        for inner in ast.walk(argument):
            if not isinstance(inner, ast.Compare):
                continue
            if not any(isinstance(operator, ast.Eq) for operator in inner.ops):
                continue
            left = inner.left
            if (
                isinstance(left, ast.Attribute)
                and left.attr in UNIQUE_COLUMNS
                and isinstance(left.value, ast.Name)
                and left.value.id[:1].isupper()
            ):
                return True
    return False


def _has_a_limit(scope: ast.AST) -> bool:
    return any(
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and inner.func.attr == "limit"
        for inner in ast.walk(scope)
    )


def _limited_later_through_a_name(
    root: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    """``statement = select(...)`` … ``statement = statement.limit(limit)``.

    A statement built over several lines is still bounded; the limit simply arrives
    through the name rather than in one chain.
    """

    assignment = parents.get(root)
    if not isinstance(assignment, ast.Assign) or len(assignment.targets) != 1:
        return False
    target = assignment.targets[0]
    if not isinstance(target, ast.Name):
        return False
    scope = _enclosing_function(root, parents)
    if scope is None:
        return False
    for inner in ast.walk(scope):
        if not (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "limit"
        ):
            continue
        # The limit may sit further along the chain — `statement.order_by(...).limit(n)`
        # applies to the statement even though it is not written directly against it.
        if any(
            isinstance(part, ast.Name) and part.id == target.id for part in ast.walk(inner)
        ):
            return True
    return False


def _is_bounded(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether this select can only ever bring back a countable number of rows.

    Three ways to be bounded, and a reader has to satisfy one of them:

    * a ``.limit(...)`` in the statement itself;
    * an equality on a column that is unique, which is one row by definition — demanding
      ``.limit(1)`` beside a primary key would only teach people to add meaningless ones;
    * the statement is built through a name and the limit arrives on a later line.

    Being narrowed by whoever calls the function is **not** one of them. Nothing here can
    check that, and "the caller passes a small list" is what every one of these was on the
    day it was written.
    """

    root = _chain_root(node, parents)
    return (
        _has_a_limit(root)
        or _narrows_to_one_row(root)
        or _limited_later_through_a_name(root, parents)
    )


def _unbounded_whole_row_reads() -> list[str]:
    findings: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parents(tree)
        for node in ast.walk(tree):
            entity = _guarded_entity_in(node)
            if entity is not None and not _is_bounded(node, parents):
                findings.append(
                    f"{path.relative_to(SOURCE_ROOT)}:{node.lineno} "
                    f"select({entity}) with no .limit()"
                )
    return findings


def test_no_reader_collects_whole_scan_rows_without_a_bound() -> None:
    findings = _unbounded_whole_row_reads()
    assert findings == [], (
        "A whole scan row carries its evidence blob, so an unbounded read of them is an "
        "unbounded amount of memory in whichever process asked. Count in SQL, or add a "
        "limit:\n  " + "\n  ".join(findings)
    )


def test_the_guard_would_catch_the_read_that_caused_the_outage() -> None:
    """The check is only worth having if it fails on the code that actually broke."""

    tree = ast.parse(
        "scans = (await self.session.scalars(\n"
        "    select(ScanResult).where(\n"
        "        ScanResult.strategy_version_id.in_(version_ids),\n"
        "        ScanResult.evaluated_at >= since,\n"
        "    )\n"
        ")).all()\n"
    )
    parents = _parents(tree)
    offenders = [
        node
        for node in ast.walk(tree)
        if _is_whole_entity_select(node) and not _is_bounded(node, parents)
    ]
    assert len(offenders) == 1


def test_a_bounded_whole_row_read_is_allowed() -> None:
    """Reading rows is fine when how many is decided up front."""

    tree = ast.parse(
        "rows = (await session.scalars(\n"
        "    select(ScanResult)\n"
        "    .where(ScanResult.strategy_version_id == version_id)\n"
        "    .order_by(ScanResult.evaluated_at.desc())\n"
        "    .limit(200)\n"
        ")).all()\n"
    )
    parents = _parents(tree)
    offenders = [
        node
        for node in ast.walk(tree)
        if _is_whole_entity_select(node) and not _is_bounded(node, parents)
    ]
    assert offenders == []


def test_counting_columns_is_never_mistaken_for_collecting_rows() -> None:
    """The replacement must not itself trip the rule it exists to satisfy."""

    tree = ast.parse(
        "totals = await session.execute(\n"
        "    select(\n"
        "        ScanResult.outcome,\n"
        "        ScanResult.timeframe,\n"
        "        func.count().label('checks'),\n"
        "    ).group_by(ScanResult.outcome, ScanResult.timeframe)\n"
        ")\n"
    )
    parents = _parents(tree)
    offenders = [
        node
        for node in ast.walk(tree)
        if _is_whole_entity_select(node) and not _is_bounded(node, parents)
    ]
    assert offenders == []


# --- The tally is the same arithmetic, not an approximation of it -------------------


def _count_the_long_way(
    rows: list[tuple[ScanOutcome, str | None, int | None]],
) -> ScanEvidenceTally:
    """What the page did before: one pass over every row, held in memory."""

    measured = [
        measure_freshness(lateness_ms=lateness, timeframe=timeframe)
        for _, timeframe, lateness in rows
    ]
    known = [item for item in measured if item.is_known]
    return ScanEvidenceTally(
        total=len(rows),
        usable=sum(1 for outcome, _, _ in rows if outcome != ScanOutcome.ERROR),
        confirmed=sum(1 for outcome, _, _ in rows if outcome == ScanOutcome.CONFIRMED),
        forming=sum(
            1
            for outcome, _, _ in rows
            if outcome in {ScanOutcome.FORMING, ScanOutcome.NEAR_MISS}
        ),
        freshness_known=len(known),
        freshness_current=sum(1 for item in known if item.is_current),
        freshness_ratio_total=sum(item.ratio for item in known),
    )


@pytest.mark.parametrize("outcome", list(ScanOutcome))
@pytest.mark.parametrize("timeframe", ["1m", "5m", "1h", "4h", "1d"])
@pytest.mark.parametrize("candles_behind", [0, 1, 2, 3, 10])
def test_grouped_counts_equal_row_by_row_counts(
    outcome: ScanOutcome,
    timeframe: str,
    candles_behind: int,
) -> None:
    """Every outcome, every timeframe, every distance behind — one rule, not one case."""

    lateness = timeframe_ms(timeframe) * candles_behind
    repeats = 7
    grouped = tally_scan_evidence([(outcome, timeframe, lateness, repeats)])
    one_by_one = _count_the_long_way([(outcome, timeframe, lateness)] * repeats)
    assert grouped == one_by_one


def test_several_groups_add_up_the_same_way() -> None:
    """A monitor watches many coins on many periods; the groups must simply add."""

    rows: list[tuple[ScanOutcome, str | None, int | None]] = []
    groups: list[tuple[ScanOutcome, str | None, int | None, int]] = []
    for index, outcome in enumerate(ScanOutcome):
        timeframe = ["1m", "5m", "1h"][index % 3]
        lateness = 60_000 * index
        count = index + 1
        groups.append((outcome, timeframe, lateness, count))
        rows.extend([(outcome, timeframe, lateness)] * count)
    assert tally_scan_evidence(groups) == _count_the_long_way(rows)


def test_a_check_with_no_measurable_lateness_is_unknown_not_late() -> None:
    """Missing evidence scores the unknown ratio, and it comes from one owner."""

    tally = tally_scan_evidence([(ScanOutcome.CONFIRMED, None, None, 4)])
    assert tally.total == 4
    assert tally.freshness_known == 0
    assert tally.freshness_ratio == RATIO_WHEN_UNKNOWN


def test_an_empty_window_scores_nothing_rather_than_dividing_by_zero() -> None:
    tally = tally_scan_evidence([])
    assert tally.total == 0
    assert tally.coverage_ratio == 0.0
    assert tally.freshness_ratio == RATIO_WHEN_UNKNOWN
