"""The automated path proposes. It never publishes, and it never becomes a status.

This is the rule the whole product rests on: a Shariah status is assigned by a governed
review process and by nothing else. An automated reading of a project's website is
research — useful, worth showing, and not a ruling.

The tests here are deliberately about *shape* rather than about behaviour, because the
failure they guard against is a future edit rather than a present bug. Somebody adding
a helpful line to the pipeline should have to delete one of these tests to do it, and
deleting a test is a thing a reviewer sees.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from ai_market_monitor.db.models import AutomatedScreenRun, ProviderCoinProfile
from ai_market_monitor.services import automated_screen_pipeline
from ai_market_monitor.services.automated_research_reader import (
    VERDICT_ORDER,
    VERDICT_PRESENTATION,
)
from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
    METHODOLOGY_SYSTEM_CODE,
)
from ai_market_monitor.services.sharia_evidence_screen import EvidenceVerdict

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "src" / "ai_market_monitor" / "services" / "automated_screen_pipeline.py"
SCREEN = ROOT / "src" / "ai_market_monitor" / "services" / "sharia_evidence_screen.py"
CRAWLER = ROOT / "src" / "ai_market_monitor" / "services" / "coin_evidence_crawler.py"
VOCABULARY = (
    ROOT / "src" / "ai_market_monitor" / "services" / "sharia_evidence_vocabulary.py"
)

#: Names that only appear where a real Shariah status is being written.
FORBIDDEN_NAMES = (
    "AssetShariaAssessment",
    "ExternalAssessment",
    "PublishedAssetAssessment",
    "ReviewDecision",
    "AssetShariaStatusHistory",
    "ComplianceReview",
)


def _code_names(path: Path) -> set[str]:
    """Every name the code actually uses, with comments and docstrings left out.

    Read from the parsed tree rather than from the file's text on purpose. A first
    version of this searched the raw source and failed on the module's own docstring,
    which says in plain words that it never writes an ``AssetShariaAssessment`` — the
    check was refusing the sentence that explains the rule it enforces. What matters is
    what the code does, and only the tree knows that.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                names.add(node.asname)
    return names


@pytest.mark.parametrize("name", FORBIDDEN_NAMES)
@pytest.mark.parametrize(
    "path", [PIPELINE, SCREEN, CRAWLER, VOCABULARY], ids=lambda p: p.name
)
def test_the_automated_path_never_touches_an_authoritys_tables(name, path):
    """It may read a project's website. It may not write anybody's verdict."""

    assert name not in _code_names(path), (
        f"{path.name} uses {name}. The automated screen must never write, amend or "
        "imply a governed Shariah status."
    )


def test_nothing_in_the_pipeline_assigns_published():
    """`published` is set by the approval route and by nothing else.

    Checked by parsing rather than by searching for a string, so a spelling such as
    ``setattr(run, "published", True)`` cannot slip past a grep.
    """

    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "published":
            assert isinstance(node.ctx, ast.Load), (
                "The automated pipeline assigns `published`. Only the application's "
                "own approval route may do that."
            )
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setattr":
            names = [
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
            assert "published" not in names


def test_the_stored_row_defaults_to_unpublished():
    """A row that nobody approved is unpublished, without anybody remembering to say so."""

    assert AutomatedScreenRun.__table__.c.published.default.arg is False
    assert AutomatedScreenRun.__table__.c.published.nullable is False


def test_the_provider_profile_carries_no_status_column():
    """The provider table holds facts. A status column there would be a second answer."""

    forbidden = {"status", "verdict", "eligible", "sharia_status", "methodology_id"}
    present = {column.name for column in ProviderCoinProfile.__table__.columns}
    assert not (present & forbidden), present & forbidden


def test_every_result_says_who_decided_it():
    """A reader must never meet one of these answers without being told what made it."""

    from ai_market_monitor.services.coin_evidence_crawler import EvidenceFolder
    from ai_market_monitor.services.sharia_evidence_screen import decide

    payload = decide("ANY", "Any", EvidenceFolder(symbol="ANY")).as_dict()
    assert payload["human_reviewed"] is False
    assert payload["methodology"] == METHODOLOGY_SYSTEM_CODE
    assert payload["disclosure"] == AUTOMATED_DISCLOSURE


def test_the_passport_payload_says_no_scholar_reviewed_it():
    from ai_market_monitor.services.coin_evidence_crawler import EvidenceFolder
    from ai_market_monitor.services.sharia_evidence_screen import decide

    folder = EvidenceFolder(symbol="ANY")
    payload = automated_screen_pipeline.passport_payload(
        decide("ANY", "Any", folder), None, folder
    )
    assert payload["human_reviewed"] is False
    assert payload["disclosure"] == AUTOMATED_DISCLOSURE


def test_the_page_has_words_for_every_verdict_the_screen_can_return():
    """A verdict with no wording would reach a reader as a raw field name."""

    assert set(VERDICT_PRESENTATION) == {item.value for item in EvidenceVerdict}
    assert set(VERDICT_ORDER) == {item.value for item in EvidenceVerdict}


def test_the_list_view_never_selects_the_evidence_columns():
    """Five list views once loaded full evidence JSON and read 1.6 GB to draw twelve rows.

    The list must name the columns it draws. `select(AutomatedScreenRun)` would load the
    reasons, the quotations and the activity lists for every row on the page.
    """

    from ai_market_monitor.services.automated_research_reader import (
        AutomatedResearchReader,
    )

    source = textwrap.dedent(inspect.getsource(AutomatedResearchReader.rows))
    tree = ast.parse(source)
    # Strip the docstring before looking: this method's own docstring explains the rule
    # by naming the forbidden call, and matching on text refused the explanation.
    body = tree.body[0]
    assert isinstance(body, ast.AsyncFunctionDef)
    statements = body.body[1:] if ast.get_docstring(body) else body.body

    for node in statements:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and getattr(inner.func, "id", "") == "select":
                whole_model = [
                    arg for arg in inner.args if getattr(arg, "id", "") == "AutomatedScreenRun"
                ]
                assert not whole_model, (
                    "The list view selects the whole row, which loads the reasons, the "
                    "quotations and the activity lists for every coin on the page. Name "
                    "the columns it draws."
                )
            if isinstance(inner, ast.Attribute) and inner.attr in {"reasons", "evidence"}:
                assert getattr(inner.value, "id", "") != "AutomatedScreenRun", (
                    f"The list view reads {inner.attr}, which is the heavy column."
                )
