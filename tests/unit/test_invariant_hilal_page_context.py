"""Page context, card inputs and prompt rules for Hilal.

Covers R15 (server accepts every page description), R16 (card inputs reach the
payload), R20-adversarial offline half (the prompt rules that stop invention)
and R21 (caps truncate, never refuse; JS caps equal schema caps).

New file. No existing invariant file is touched.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.hilal_chat import (
    HILAL_CARD_INPUT_LABEL_MAX,
    HILAL_CARD_INPUT_VALUE_MAX,
    HILAL_CARD_INPUTS_MAX,
    HILAL_PAGE_HEADING_MAX,
    HILAL_PAGE_NAMES,
    HILAL_PAGE_POINT_MAX,
    HILAL_PAGE_POINTS_MAX,
    HILAL_PAGE_SUMMARY_MAX,
    HilalChatAsk,
    HilalChatBoard,
    HilalChatBoardCard,
    HilalChatPageContext,
    HilalChatView,
)
from ai_market_monitor.services.hilal_chat_agent import _instructions
from ai_market_monitor.services.hilal_chat_knowledge import Evidence, HilalChatKnowledge

STATIC = Path("src/ai_market_monitor/static/hm-page-context.js")


def _js() -> str:
    return STATIC.read_text(encoding="utf-8")


def _js_number(name: str) -> int:
    match = re.search(rf"{name}\s*=\s*(\d+)", _js())
    assert match, f"{name} is not defined in hm-page-context.js"
    return int(match.group(1))


# --------------------------------------------------------------------------------
# R21: one source for caps — the test asserts equality.
# --------------------------------------------------------------------------------


def test_the_page_names_are_a_closed_list_of_eleven() -> None:
    assert len(HILAL_PAGE_NAMES) == 11
    assert set(HILAL_PAGE_NAMES) == {
        "screened_market",
        "opportunities",
        "watch_plans",
        "passport",
        "watchlist",
        "connections",
        "research",
        "settings",
        "support",
        "subscription",
        "report",
    }


@pytest.mark.parametrize(
    ("js_name", "schema_value"),
    [
        ("PAGE_HEADING_MAX", HILAL_PAGE_HEADING_MAX),
        ("PAGE_SUMMARY_MAX", HILAL_PAGE_SUMMARY_MAX),
        ("PAGE_POINTS_MAX", HILAL_PAGE_POINTS_MAX),
        ("PAGE_POINT_MAX", HILAL_PAGE_POINT_MAX),
        ("CARD_INPUTS_MAX", HILAL_CARD_INPUTS_MAX),
        ("CARD_INPUT_LABEL_MAX", HILAL_CARD_INPUT_LABEL_MAX),
        ("CARD_INPUT_VALUE_MAX", HILAL_CARD_INPUT_VALUE_MAX),
    ],
    ids=lambda item: item[0] if isinstance(item, tuple) else str(item),
)
def test_js_caps_equal_schema_caps(js_name: str, schema_value: int) -> None:
    """The browser truncates to the same numbers the server holds.

    Two lists of the same limits is the duplication this codebase keeps paying
    for. The schema is the owner; the script mirrors it; this test is what keeps
    the two from drifting apart.
    """

    assert _js_number(js_name) == schema_value, (
        f"{js_name}: browser truncates at {_js_number(js_name)} "
        f"but the schema holds {schema_value}"
    )


def test_accepted_names_in_js_cover_board_and_every_page() -> None:
    text = _js()
    for name in ("board", *HILAL_PAGE_NAMES):
        assert f'"{name}"' in text, f"{name} is not an accepted page-context name"


# --------------------------------------------------------------------------------
# R16: card inputs — one filled, one empty — validate and travel.
# --------------------------------------------------------------------------------


def test_a_card_with_one_filled_and_one_empty_input_validates() -> None:
    card = HilalChatBoardCard(
        label="Price moves",
        needs=["Threshold"],
        inputs=[
            {"label": "Direction", "filled": True, "value": "up at least"},
            {"label": "Threshold", "filled": False, "value": None},
        ],
    )
    assert card.inputs is not None
    assert len(card.inputs) == 2
    assert card.inputs[0].filled is True
    assert card.inputs[0].value == "up at least"
    assert card.inputs[1].filled is False
    assert card.inputs[1].value is None
    # `needs` is kept: the empty field is still named as missing.
    assert card.needs == ["Threshold"]


def test_an_over_long_input_value_is_truncated_never_refused() -> None:
    card = HilalChatBoardCard(
        label="Price moves",
        inputs=[{"label": "Note", "filled": True, "value": "x" * 500}],
    )
    assert card.inputs is not None
    assert len(card.inputs[0].value or "") <= HILAL_CARD_INPUT_VALUE_MAX


def test_more_inputs_than_fit_are_kept_in_order_not_refused() -> None:
    card = HilalChatBoardCard(
        label="Price moves",
        inputs=[
            {"label": f"Field {n}", "filled": True, "value": str(n)} for n in range(20)
        ],
    )
    assert card.inputs is not None
    assert len(card.inputs) == HILAL_CARD_INPUTS_MAX
    assert card.inputs[0].label == "Field 0"


# --------------------------------------------------------------------------------
# R21: over-long page descriptions truncate, never refuse the turn.
# --------------------------------------------------------------------------------


def test_an_over_long_page_summary_is_truncated_never_refused() -> None:
    view = HilalChatView(page="screened_market", screened_market={"summary": "x" * 5000})
    assert view.screened_market is not None
    assert len(view.screened_market.summary or "") <= HILAL_PAGE_SUMMARY_MAX


def test_more_points_than_fit_are_kept_in_order_not_refused() -> None:
    view = HilalChatView(
        page="settings",
        settings={"points": [f"point {n}" for n in range(30)]},
    )
    assert view.settings is not None
    assert len(view.settings.points) == HILAL_PAGE_POINTS_MAX
    assert view.settings.points[0] == "point 0"


def test_an_over_long_board_sentence_is_truncated_never_refused() -> None:
    board = HilalChatBoard(sentence="s" * 5000)
    assert board.sentence is not None
    assert len(board.sentence) <= 600


def test_an_over_long_page_name_is_truncated_never_refused() -> None:
    view = HilalChatView(page="y" * 500)
    assert view.page is not None
    assert len(view.page) <= 120


# --------------------------------------------------------------------------------
# R15: the server accepts every page description.
# --------------------------------------------------------------------------------


def test_every_page_description_reaches_ask_validation() -> None:
    view = {name: {"heading": f"{name} heading"} for name in HILAL_PAGE_NAMES}
    ask = HilalChatAsk(message="should I buy bitcoin", view=view)
    assert ask.view is not None
    for name in HILAL_PAGE_NAMES:
        assert getattr(ask.view, name) is not None


def test_an_unknown_page_name_is_still_refused() -> None:
    """Closed on purpose: a page inventing a new name is dropped, not accepted."""

    with pytest.raises(ValidationError):
        HilalChatView.model_validate({"page": "market", "no_such_page": {}})


# --------------------------------------------------------------------------------
# R16: what the person sees — inputs and pages — reaches the evidence payload.
# --------------------------------------------------------------------------------


def test_on_screen_passes_card_inputs_with_the_persons_own_note() -> None:
    view = HilalChatView(
        page="monitor_canvas",
        board=HilalChatBoard(
            sentence="Watch BTC.",
            cards=[
                HilalChatBoardCard(
                    label="Price moves",
                    needs=["Threshold"],
                    inputs=[
                        {"label": "Direction", "filled": True, "value": "up at least"},
                        {"label": "Threshold", "filled": False, "value": None},
                    ],
                )
            ],
        ),
    )
    seen = HilalChatKnowledge._on_screen(view)
    cards = seen["the_monitor_they_are_drawing"]["cards_on_the_board"]
    assert cards[0]["fields_the_person_filled_in"] == [
        {"field": "Direction", "filled": True, "value": "up at least"},
        {"field": "Threshold", "filled": False, "value": None},
    ]
    assert cards[0]["still_needs"] == ["Threshold"]
    note = seen["the_monitor_they_are_drawing"]["note"]
    assert "their own" in note
    assert "person" in note


def test_on_screen_passes_page_contexts() -> None:
    view = HilalChatView(
        page="screened_market",
        section="The list of screened coins",
        screened_market=HilalChatPageContext(
            heading="Halal Assets",
            summary="All screened coins.",
            points=["The list of screened coins"],
        ),
    )
    seen = HilalChatKnowledge._on_screen(view)
    assert seen["page"] == "screened_market"
    assert seen["part_of_the_page_in_front_of_them"] == "The list of screened coins"
    assert seen["screened_market"]["heading"] == "Halal Assets"


def test_on_screen_says_nothing_for_an_empty_view() -> None:
    assert HilalChatKnowledge._on_screen(HilalChatView()) == {}


# --------------------------------------------------------------------------------
# Prompt rules: records exist, and what the model may do with them.
# --------------------------------------------------------------------------------


def _words() -> str:
    return _instructions().lower()


def test_the_prompt_names_the_account_records() -> None:
    words = _words()
    assert "monitors" in words
    assert "plan" in words
    assert "alert" in words or "channel" in words


def test_the_prompt_repeats_the_persons_own_values_as_theirs() -> None:
    words = _words()
    assert "their own" in words


def test_the_prompt_never_recommends_a_value_for_an_input() -> None:
    words = _words()
    assert "never" in words
    assert "recommend" in words or "suggest what to put" in words


def test_statuses_are_repeated_per_named_standard_only() -> None:
    words = _words()
    assert "named standard" in words or "named review" in words


def test_our_automated_standard_is_named_as_an_automated_reading() -> None:
    words = _words()
    assert "automated" in words
    assert "never" in words


def test_no_aggregate_winner_and_never_the_default() -> None:
    words = _words()
    assert "aggregat" in words or "combin" in words or "merge" in words
    assert "default" in words


def test_the_model_may_not_output_urls() -> None:
    words = _words()
    assert "url" in words


# --------------------------------------------------------------------------------
# R21: the whole payload is bounded, and the flag means something was cut.
# --------------------------------------------------------------------------------


#: The evidence size cap, read from the setting itself so the tests cannot drift.
_CAP = Settings.model_fields["hilal_chat_evidence_max_chars"].default


def _knowledge() -> HilalChatKnowledge:
    """A knowledge service with no database, for the pure size-trimming tests."""

    return HilalChatKnowledge(
        None, SimpleNamespace(hilal_chat_evidence_max_chars=_CAP)  # type: ignore[arg-type]
    )


def test_a_maxed_out_board_is_bounded_by_fit_to_size() -> None:
    """R21: caps on page data truncate — the whole payload, not just Passports.

    A maxed-out board (32 cards with every field filled, 32 checks) plus account
    rows exceeds the evidence cap on its own. `_fit_to_size` must drop whole
    on-screen rows until the payload fits. Never refuses, never invents.
    """

    view = HilalChatView(
        page="monitor_canvas",
        board=HilalChatBoard(
            sentence="s" * 600,
            cards=[
                HilalChatBoardCard(
                    label=f"Card {n} " + "L" * 70,
                    reads="R" * 160,
                    inside="I" * 48,
                    needs=[f"need {m} " + "N" * 70 for m in range(6)],
                    inputs=[
                        {
                            "label": f"Field {k} " + "F" * 70,
                            "filled": True,
                            "value": "V" * 120,
                        }
                        for k in range(6)
                    ],
                )
                for n in range(32)
            ],
            checks=[{"tone": "warn", "text": "T" * 240} for _ in range(32)],
            watching="W" * 120,
            ways_to_be_told=["tell me"] * 8,
            controls=["Save"] * 24,
            how_to=["Drag a card onto the canvas."] * 24,
        ),
    )
    evidence = Evidence(
        on_screen=HilalChatKnowledge._on_screen(view),
        account={
            "monitors": [
                {"name": f"Monitor {n} " + "M" * 100, "state": "running"}
                for n in range(6)
            ],
            "plan": {"name": "Test plan"},
            "alert_channels": {"chosen": ["web"], "connected": ["web"]},
        },
    )
    before = len(json.dumps(evidence.to_payload(), default=str))
    assert before > _CAP, "the maxed board must exceed the cap before trimming"
    _knowledge()._fit_to_size(evidence)
    after = len(json.dumps(evidence.to_payload(), default=str))
    assert after <= _CAP, f"payload still {after} chars against the {_CAP} cap"
    assert evidence.trimmed_for_size is True
    # Whole card rows dropped last-first; the account rows survive when the
    # board alone covers the overhang.
    assert (
        len(evidence.on_screen["the_monitor_they_are_drawing"]["cards_on_the_board"])
        < 32
    )
    assert len(evidence.account["monitors"]) == 6


def test_the_trimmed_flag_stays_false_when_nothing_was_dropped() -> None:
    """The flag means "something was cut", not "the payload is big"."""

    evidence = Evidence(methodologies=[{"id": "method:m", "name": "M" * 40000}])
    assert len(json.dumps(evidence.to_payload(), default=str)) > _CAP
    _knowledge()._fit_to_size(evidence)
    assert evidence.trimmed_for_size is False


def test_the_trimmed_flag_stays_false_when_already_under_cap() -> None:
    evidence = Evidence()
    _knowledge()._fit_to_size(evidence)
    assert evidence.trimmed_for_size is False


# --------------------------------------------------------------------------------
# R21/R15: a bare string page note must never refuse the turn.
# --------------------------------------------------------------------------------


def test_a_bare_string_page_note_is_refused_by_the_view_schema() -> None:
    """Why the browser must never send a bare string.

    `HilalChatPageContext` is object-only (`extra="forbid"`). A string page note
    fails `HilalChatAsk` validation, so the message route refuses the turn with
    a 422 before the chat service ever runs (see `POST /dashboard/hilal/message`
    in `api/routers/hilal_chat.py`). The fix belongs to the JS owner, which must
    coerce the string instead of passing it through.
    """

    with pytest.raises(ValidationError):
        HilalChatView.model_validate(
            {"page": "screened_market", "screened_market": "plain string"}
        )


def _js_snapshot_note(note_js: str) -> object:
    """Run the real `snapshot()` from hm-page-context.js with one publisher.

    Copies the shipped file byte-identical into a temp dir (as `.mjs` so node
    imports it as a module), stubs the DOM, publishes one page description
    written as `note_js`, and returns what `snapshot()` hands over for it.
    """

    node = shutil.which("node")
    assert node is not None, "node is required to run the page-context script"
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "page_context_copy.mjs").write_text(
            STATIC.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (tmpdir / "harness.mjs").write_text(
            'import { publish, snapshot } from "./page_context_copy.mjs";\n'
            "globalThis.window = { innerHeight: 800 };\n"
            "globalThis.document = { body: { dataset: {} }, "
            "querySelectorAll: () => [], querySelector: () => null };\n"
            f"publish('screened_market', () => {note_js});\n"
            "const view = snapshot();\n"
            "console.log(JSON.stringify("
            "view.screened_market === undefined ? null : view.screened_market));\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [node, "harness.mjs"],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_a_string_page_note_is_coerced_to_a_summary_object() -> None:
    """The JS owner coerces a bare string instead of passing it through."""

    coerced = _js_snapshot_note('"plain string"')
    assert coerced == {"summary": "plain string"}
    view = HilalChatView.model_validate(
        {"page": "screened_market", "screened_market": coerced}
    )
    assert view.screened_market is not None
    assert view.screened_market.summary == "plain string"


def test_an_over_long_string_page_note_is_capped_never_refused() -> None:
    coerced = _js_snapshot_note('"x".repeat(5000)')
    assert coerced == {"summary": "x" * HILAL_PAGE_SUMMARY_MAX}
    view = HilalChatView.model_validate(
        {"page": "screened_market", "screened_market": coerced}
    )
    assert view.screened_market is not None
    assert len(view.screened_market.summary or "") <= HILAL_PAGE_SUMMARY_MAX


def test_a_blank_string_page_note_sends_nothing() -> None:
    assert _js_snapshot_note('"   "') is None
