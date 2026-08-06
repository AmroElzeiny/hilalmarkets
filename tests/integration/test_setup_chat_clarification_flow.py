"""The open question owns the next turn, and the shown question is the stored one.

Every case here is a transcript that used to end in a generic reply while the question
it was answering was still open, or in a question the assistant would not have accepted
the answer to.  They are grouped by the defect each one proves is gone:

* one resolver — the same answer works typed and clicked, in five languages;
* one option authority — nothing is displayed that cannot be executed;
* one canonical step — the shown question is the stored question, always;
* nothing is lost — an answer the reader cannot use changes no stored value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.active_question import (
    AnswerDomain,
    AnswerOutcome,
    canonical_values,
    display_options,
    labels_for,
    normalize_answer_text,
    resolve_active_answer,
)
from ai_market_monitor.engine.conversation_language import ConversationLanguage
from ai_market_monitor.schemas.setup_agent import SetupConversationContext
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2
from ai_market_monitor.schemas.timeframes import (
    COMMON_TIMEFRAMES,
    ORDERED_TIMEFRAMES,
    SUPPORTED_TIMEFRAMES,
)
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentTurnInput,
    SetupChatAgent,
)

pytestmark = pytest.mark.anyio

ALERT_REQUEST = "Inform me once any coin increases 5%"

#: The reply this whole flow used to end in.
GENERIC_MISS = "nothing is set up"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="setup-agent-secret-with-at-least-32-characters",
        openai_api_key=SecretStr("test-key"),
        sharia_screening_enforced=False,
        setup_agent_max_estimated_cost_usd_per_turn=5,
    )


def _responses_body(text: str) -> dict[str, Any]:
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 20, "output_tokens": 8},
    }


@dataclass
class _Planner:
    """A model that reports one supported-but-incomplete percentage request.

    Only the *first* turn needs it. Every later turn in these transcripts is an answer
    to an open question, and answering an open question must never need a model call —
    that is one of the things being proved.
    """

    source_text: str = ALERT_REQUEST
    missing: tuple[str, ...] = ("trigger_timeframe", "reference_point")
    calls: int = 0
    payloads: list[dict[str, Any]] = field(default_factory=list)

    def envelope(self, message: str) -> str:
        # The span must be words the turn really contains, exactly as production
        # requires, so the stub quotes the message it was actually given. It only
        # reports the incomplete request for the sentence that really is incomplete.
        said = message or self.source_text
        incomplete = said.strip() == self.source_text
        return json.dumps(
            {
                "segments": [
                    {
                        "segment_ref": "segment_1",
                        "exact_source_text": said,
                        "segment_kind": (
                            "STRATEGY_INSTRUCTION" if incomplete else "CONVERSATIONAL_CONTEXT"
                        ),
                    }
                ],
                "semantic_intents": [],
                "clarification_answers": [],
                "questions_to_answer": [],
                "supported_incomplete_intents": (
                    [{"segment_ref": "segment_1", "missing_fields": list(self.missing)}]
                    if incomplete
                    else []
                ),
                "unsupported_intents": [],
                "approval_intent": None,
                "overall_confidence": 0.95,
            }
        )

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            name = body["text"]["format"]["name"]
            payload = json.loads(body["input"])
            self.payloads.append(payload)
            if name == "hilalmarkets_setup_turn_intent":
                self.calls += 1
                said = str(payload.get("current_user_turn") or "")
                return httpx.Response(200, json=_responses_body(self.envelope(said)))
            return httpx.Response(
                200,
                json=_responses_body(
                    json.dumps({"message": "Done.", "clarification_question_id": None})
                ),
            )

        return httpx.MockTransport(handler)


class _Session:
    """Carry draft and conversation forward exactly as the launch service does."""

    def __init__(self, planner: _Planner | None = None) -> None:
        self.planner = planner or _Planner()
        self.agent = SetupChatAgent(_settings(), transport=self.planner.transport())
        self.draft = StrategyDraftV2()
        self.conversation = SetupConversationContext()
        self.turns = 0
        self.replies: list[str] = []

    async def say(self, message: str) -> Any:
        self.turns += 1
        result = await self.agent.run_turn(
            SetupAgentTurnInput(
                message=message,
                source_turn_id=f"turn-{self.turns}",
                draft=self.draft,
                conversation=self.conversation,
                setup_mode=self.draft.mode,
                active_language=self.conversation.active_language,
            )
        )
        self.draft = result.draft
        self.conversation = result.conversation
        self.replies.append(result.message)
        self.assert_one_step()
        return result

    def assert_one_step(self) -> None:
        """The question on screen is the question that will validate the next answer."""

        workflow = self.conversation.pending_workflow
        if workflow is None:
            return
        contract = self.conversation.active_question
        assert contract is not None, "a stored workflow with no question on screen"
        assert workflow.matches(contract), (
            f"displayed {contract.question_id}/{contract.step_revision} but stored "
            f"{workflow.question_id}/{workflow.step_revision}"
        )

    @property
    def question(self) -> str:
        contract = self.conversation.active_question
        return contract.question if contract is not None else ""

    @property
    def accepted(self) -> dict[str, Any]:
        workflow = self.conversation.pending_workflow
        return dict(workflow.accepted_values) if workflow is not None else {}


# ---------------------------------------------------------------------------------
# One resolver: the same words work everywhere, typed or clicked
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "all",
        "All",
        "ALL",
        "all coins",
        "all eligible coins",
        "All eligible spot assets",
        "all-eligible spot assets",
        "  All eligible spot assets!  ",
        "eligible_market",
        "the whole market",
        "everything",
        "كل العملات",
        "toutes les cryptos",
        "todas las monedas",
        "весь рынок",
    ],
)
def test_every_way_of_saying_the_whole_market_resolves_to_one_value(message: str) -> None:
    resolution = resolve_active_answer(message, domain=AnswerDomain.UNIVERSE_MODE)
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == "eligible_market"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("my favorites", "approved_watchlist"),
        ("My Favourites", "approved_watchlist"),
        ("mes favoris", "approved_watchlist"),
        ("specific coins", "explicit_assets"),
        ("Specific eligible assets", "explicit_assets"),
        ("عملات محددة", "explicit_assets"),
    ],
)
def test_the_other_scopes_are_answerable_in_words_too(message: str, expected: str) -> None:
    resolution = resolve_active_answer(message, domain=AnswerDomain.UNIVERSE_MODE)
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == expected


@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_a_button_label_always_answers_its_own_button(language: ConversationLanguage) -> None:
    """Typing what the button says can never be worse than clicking it."""

    for domain in (
        AnswerDomain.UNIVERSE_MODE,
        AnswerDomain.REFERENCE_POINT,
        AnswerDomain.COMPARATOR,
        AnswerDomain.TIMEFRAME,
    ):
        values = canonical_values(domain)
        shown = display_options(domain, values)
        labels = labels_for(domain, shown, language)
        for value, label in labels.items():
            resolution = resolve_active_answer(
                label,
                domain=domain,
                allowed_options=values,
                offered_values=shown,
                display_labels=labels,
            )
            assert resolution.outcome is AnswerOutcome.RESOLVED, f"{language} {label}"
            assert resolution.canonical_value == value, f"{language} {label}"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("1 minute", "1m"),
        ("1 min", "1m"),
        ("1m", "1m"),
        ("1 MINUTE", "1m"),
        ("  1-minute ", "1m"),
        ("1 minuto", "1m"),
        ("1 минута", "1m"),
        ("دقيقة", "1m"),
        ("1h", "1h"),
        ("1 hour", "1h"),
        ("one hour", "1h"),
        ("hourly", "1h"),
        ("4 hours", "4h"),
        ("4 heures", "4h"),
        ("4 ساعات", "4h"),
        ("1 day", "1d"),
        ("daily", "1d"),
        ("1 día", "1d"),
        ("يوم", "1d"),
        ("5m", "5m"),
        ("15 minutes", "15m"),
    ],
)
def test_the_canonical_registry_answers_every_period_word(message: str, expected: str) -> None:
    resolution = resolve_active_answer(message, domain=AnswerDomain.TIMEFRAME)
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == expected


@pytest.mark.parametrize("timeframe", sorted(SUPPORTED_TIMEFRAMES))
def test_every_executable_period_is_answerable_as_plain_text(timeframe: str) -> None:
    """Nothing the compiler can run may be unreachable through ordinary words."""

    resolution = resolve_active_answer(timeframe, domain=AnswerDomain.TIMEFRAME)
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == timeframe


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Candle open", "candle_open"),
        ("candle open", "candle_open"),
        ("open", "candle_open"),
        ("the open", "candle_open"),
        ("افتتاح الشمعة", "candle_open"),
        ("Ouverture de la bougie", "candle_open"),
        ("apertura de la vela", "candle_open"),
        ("открытие свечи", "candle_open"),
        ("previous close", "previous_close"),
        ("Previous candle close", "previous_close"),
        ("cierre anterior", "previous_close"),
    ],
)
def test_the_reference_point_reads_the_same_in_every_language(
    message: str, expected: str
) -> None:
    resolution = resolve_active_answer(message, domain=AnswerDomain.REFERENCE_POINT)
    assert resolution.outcome is AnswerOutcome.RESOLVED
    assert resolution.canonical_value == expected


# ---------------------------------------------------------------------------------
# Tolerance without guessing
# ---------------------------------------------------------------------------------


def test_one_keyboard_slip_asks_instead_of_correcting() -> None:
    """``q`` sits under ``1``. That is worth asking about, never worth assuming."""

    resolution = resolve_active_answer(
        "qh",
        domain=AnswerDomain.TIMEFRAME,
        offered_values=COMMON_TIMEFRAMES,
    )
    assert resolution.outcome is AnswerOutcome.CONFIRM_CANDIDATE
    assert resolution.canonical_value == "1h"
    assert resolution.keeps_the_question


def test_a_near_miss_never_becomes_a_stored_value() -> None:
    resolution = resolve_active_answer("qh", domain=AnswerDomain.TIMEFRAME)
    assert not resolution.stores_a_value


@pytest.mark.parametrize("message", ["7 hours", "3 days", "90 minutes"])
def test_a_real_period_the_platform_cannot_run_is_refused_not_rounded(message: str) -> None:
    resolution = resolve_active_answer(message, domain=AnswerDomain.TIMEFRAME)
    assert resolution.outcome is not AnswerOutcome.RESOLVED
    assert resolution.canonical_value not in SUPPORTED_TIMEFRAMES


def test_a_period_outside_a_governed_restriction_is_explained_not_silently_dropped() -> None:
    resolution = resolve_active_answer(
        "1 minute",
        domain=AnswerDomain.TIMEFRAME,
        allowed_options=("1h", "4h", "1d"),
    )
    assert resolution.outcome is AnswerOutcome.UNSUPPORTED
    assert resolution.keeps_the_question
    assert set(resolution.candidates) == {"1h", "4h", "1d"}


@pytest.mark.parametrize("message", ["cancel", "Cancel.", "forget it", "إلغاء", "annuler"])
def test_only_an_explicit_cancellation_ends_a_question(message: str) -> None:
    resolution = resolve_active_answer(message, domain=AnswerDomain.TIMEFRAME)
    assert resolution.outcome is AnswerOutcome.CANCELLED


@pytest.mark.parametrize(
    "message", ["5% or 7%", "between 3 and 9 percent", "some number"]
)
def test_two_numbers_in_one_answer_are_never_narrowed_to_one(message: str) -> None:
    resolution = resolve_active_answer(message, domain=AnswerDomain.PERCENT)
    assert not resolution.stores_a_value


# ---------------------------------------------------------------------------------
# Property-ish sweeps over every option of every option-based question
# ---------------------------------------------------------------------------------

_DECORATIONS = ["{0}", " {0} ", "{0}.", "{0}!", "{0}?", "  {0}  ", "{0},"]


@pytest.mark.parametrize("decoration", _DECORATIONS)
@pytest.mark.parametrize(
    "domain",
    [
        AnswerDomain.UNIVERSE_MODE,
        AnswerDomain.REFERENCE_POINT,
        AnswerDomain.COMPARATOR,
        AnswerDomain.TIMEFRAME,
        AnswerDomain.SCAN_WINDOW,
    ],
)
def test_punctuation_and_spacing_never_change_which_option_was_chosen(
    domain: AnswerDomain, decoration: str
) -> None:
    for value in display_options(domain):
        plain = resolve_active_answer(value, domain=domain)
        decorated = resolve_active_answer(decoration.format(value), domain=domain)
        assert decorated.outcome is plain.outcome, f"{domain} {value}"
        assert decorated.canonical_value == plain.canonical_value, f"{domain} {value}"


@pytest.mark.parametrize(
    "domain",
    [
        AnswerDomain.UNIVERSE_MODE,
        AnswerDomain.REFERENCE_POINT,
        AnswerDomain.COMPARATOR,
        AnswerDomain.TIMEFRAME,
    ],
)
def test_case_never_changes_which_option_was_chosen(domain: AnswerDomain) -> None:
    for value in display_options(domain):
        for variant in (value.upper(), value.lower(), value.title()):
            resolution = resolve_active_answer(variant, domain=domain)
            assert resolution.outcome is AnswerOutcome.RESOLVED, f"{domain} {variant}"
            assert resolution.canonical_value == value, f"{domain} {variant}"


@pytest.mark.parametrize(
    "domain",
    [
        AnswerDomain.UNIVERSE_MODE,
        AnswerDomain.REFERENCE_POINT,
        AnswerDomain.COMPARATOR,
        AnswerDomain.TIMEFRAME,
    ],
)
def test_one_edit_from_an_option_never_lands_on_a_different_option(
    domain: AnswerDomain,
) -> None:
    """A typo may be confirmed or refused. It may never resolve to something else."""

    shown = display_options(domain)
    for value in shown:
        for position in range(len(value)):
            typo = value[:position] + value[position + 1 :]
            if not typo or typo in set(canonical_values(domain)):
                # Deleting a character can land exactly on another real option — `gte`
                # becomes `gt`. That is an exact answer, not a typo, and reading it as
                # itself is right.
                continue
            resolution = resolve_active_answer(
                typo, domain=domain, offered_values=shown
            )
            if resolution.outcome is AnswerOutcome.RESOLVED:
                assert resolution.canonical_value == value, f"{domain} {typo}"


def test_normalisation_is_idempotent() -> None:
    for text in ("All Eligible Spot Assets!", " 1-Minute ", "كل العملات", "Mes Favoris"):
        once = normalize_answer_text(text)
        assert normalize_answer_text(once) == once


# ---------------------------------------------------------------------------------
# One option authority
# ---------------------------------------------------------------------------------


def test_every_displayed_period_is_one_the_compiler_can_run() -> None:
    assert set(COMMON_TIMEFRAMES) <= SUPPORTED_TIMEFRAMES


@pytest.mark.parametrize(
    "domain",
    [
        AnswerDomain.UNIVERSE_MODE,
        AnswerDomain.REFERENCE_POINT,
        AnswerDomain.COMPARATOR,
        AnswerDomain.MOVEMENT_DIRECTION,
        AnswerDomain.TIMEFRAME,
        AnswerDomain.SCAN_WINDOW,
    ],
)
def test_no_question_offers_a_value_it_cannot_execute(domain: AnswerDomain) -> None:
    assert set(display_options(domain)) <= set(canonical_values(domain))


def test_the_period_question_can_still_be_answered_beyond_what_it_shows() -> None:
    """A shortlist is for reading, never a restriction on what can be said."""

    beyond = set(ORDERED_TIMEFRAMES) - set(COMMON_TIMEFRAMES)
    assert beyond, "the shortlist is the whole registry; this test proves nothing"
    for timeframe in sorted(beyond):
        resolution = resolve_active_answer(timeframe, domain=AnswerDomain.TIMEFRAME)
        assert resolution.outcome is AnswerOutcome.RESOLVED


# ---------------------------------------------------------------------------------
# The reported transcripts, through the real agent
# ---------------------------------------------------------------------------------


async def test_a_minute_answer_is_accepted_as_the_canonical_one_minute_period() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    assert session.conversation.active_question is not None

    await session.say("1 minute")
    assert session.accepted.get("trigger_timeframe") == "1m"


async def test_a_typo_keeps_the_workflow_alive_and_never_says_nothing_is_set_up() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    opening = session.question
    before = dict(session.accepted)

    result = await session.say("qh")

    assert GENERIC_MISS not in result.message.casefold()
    assert session.conversation.active_question is not None
    assert session.accepted == before, "an unreadable answer changed a stored value"
    assert "1h" in result.message or opening in result.message


@pytest.mark.parametrize("answer", ["qh", "purple bananas", "2 minutes"])
async def test_the_question_is_asked_once_however_the_answer_failed(answer: str) -> None:
    """One question in the reply, and only one. The retry sentence must not restate it.

    Which question depends on how the answer failed. A near miss puts a narrower one on
    screen — *did you mean 1h?* — and the list of periods must not come with it: two
    questions in one reply is what a beginner answers wrongly. Either way the stored
    question is unchanged, so the next answer still lands on the same field.
    """

    session = _Session()
    await session.say(ALERT_REQUEST)
    question = session.question

    result = await session.say(answer)

    assert result.message.count(question) <= 1, result.message
    assert result.message.count("?") == 1, result.message
    assert session.question == question, "the stored question never changed"


async def test_a_typo_is_answered_without_calling_a_model() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    calls_after_start = session.planner.calls

    await session.say("qh")

    assert session.planner.calls == calls_after_start


async def test_an_hourly_answer_advances_to_the_next_missing_field() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    first = session.question

    await session.say("1h")

    assert session.accepted.get("trigger_timeframe") == "1h"
    assert session.question != first


async def test_the_period_question_never_comes_back_once_it_is_answered() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    period_question = session.question

    await session.say("1h")
    await session.say("Candle open")

    assert session.question != period_question
    assert session.accepted.get("trigger_timeframe") == "1h"


async def test_each_step_gets_its_own_question_identity() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    first = session.conversation.active_question
    assert first is not None

    await session.say("1h")
    second = session.conversation.active_question
    assert second is not None

    assert second.question_id != first.question_id
    assert second.step_revision > first.step_revision
    assert second.workflow_id == first.workflow_id


async def test_an_answer_to_a_stale_step_does_not_resolve_the_current_one() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    stale = session.conversation.active_question
    assert stale is not None

    await session.say("1h")
    current = session.conversation.pending_workflow
    assert current is not None

    assert not current.matches(stale)


async def test_repeating_the_same_answer_is_harmless() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("1h")
    after_first = dict(session.accepted)

    await session.say("1h")

    assert session.accepted.get("trigger_timeframe") == after_first.get("trigger_timeframe")


async def test_cancelling_stops_the_question_and_keeps_nothing() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)

    await session.say("cancel")

    assert session.conversation.active_question is None
    assert session.conversation.pending_workflow is None
    assert session.draft.condition_ast is None
    assert not session.draft.approval.approved


async def test_an_unreadable_answer_never_mutates_the_strategy() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)

    result = await session.say("qh")

    assert result.execution is None
    assert session.draft.condition_ast is None
    assert not session.draft.approval.approved


@pytest.mark.parametrize(
    ("period", "reference"),
    [
        ("1h", "Candle open"),
        ("1 hour", "open"),
        ("hourly", "previous close"),
        ("4 hours", "Previous candle close"),
    ],
)
async def test_free_text_and_button_wording_reach_the_same_state(
    period: str, reference: str
) -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say(period)
    await session.say(reference)

    accepted = session.accepted
    assert accepted.get("trigger_timeframe") in SUPPORTED_TIMEFRAMES
    assert accepted.get("reference_point") in {"candle_open", "previous_close"}


async def test_a_complete_new_instruction_is_allowed_to_take_the_turn() -> None:
    """The one escape that is not cancellation: a whole new, buildable request."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    calls_after_start = session.planner.calls

    await session.say("alert me when BTC/USDT drops 3% on the 4h candle")

    assert session.planner.calls > calls_after_start, (
        "a complete new request must reach the planner, not be treated as an answer"
    )


async def test_a_half_finished_new_idea_does_not_take_the_turn() -> None:
    """Anything short of complete is an attempt to answer, and keeps the question."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    before = dict(session.accepted)

    result = await session.say("maybe something about volume")

    assert GENERIC_MISS not in result.message.casefold()
    assert session.conversation.active_question is not None
    assert session.accepted == before


async def test_the_shown_question_is_the_stored_question_after_every_turn() -> None:
    session = _Session()
    for message in (ALERT_REQUEST, "qh", "1h", "??", "Candle open"):
        await session.say(message)
        session.assert_one_step()
