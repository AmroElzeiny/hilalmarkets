"""A request the platform *can* express, missing values only the trader can supply.

``create me an alert to alert me when a coin increases 5%`` is a supported alert. The
platform measures percentage moves; that is a core primitive. What the sentence does
not say is which coins, and over what period. Those are the trader's choices, and the
correct product behaviour is to ask for one of them.

What happened instead
---------------------

The planner was instructed that a rule missing a required value must be preserved in
``unsupported_intents``. The compiler turned that into ``add_unsupported``, the draft
recorded an unsupported requirement, and the reply rendered it — as a composer claim,
again as ``deterministic_claim_text`` ("Not expressible exactly: ..."), again as
``deterministic_summary`` ("I could not express this exactly: ..."), and once more as
an appended clarification. One missing timeframe produced a paragraph telling a
beginner their ordinary request could not be expressed.

``unsupported`` has to mean one thing: the registered capability system genuinely
cannot represent this market behaviour. A supported mechanic waiting on a trader's
choice is a different state with a different answer — a question.

This module decides which of the two a request is, and names the missing choices in
the order a person would ask them. It never fills a value, never picks a timeframe,
never widens a threshold, and never decides a mechanic is supported that the registry
does not offer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    localized,
)
from ai_market_monitor.engine.price_movement import movement_direction
from ai_market_monitor.engine.turn_fragments import extract_symbols, extract_timeframes
from ai_market_monitor.schemas.setup_authorization import ClarificationContract

__all__ = [
    "MissingChoice",
    "RequestCompleteness",
    "SupportedMechanic",
    "assess_request",
    "clarification_for_choice",
]


class RequestCompleteness(StrEnum):
    """What kind of "not yet buildable" a request is."""

    #: Everything needed is present.
    COMPLETE = "COMPLETE"
    #: A registered capability covers this; the trader has not yet said everything.
    SUPPORTED_INCOMPLETE = "SUPPORTED_INCOMPLETE"
    #: No registered capability covers this market behaviour at all.
    UNSUPPORTED = "UNSUPPORTED"


class MissingChoice(StrEnum):
    """A trader-controlled value the request has not supplied.

    Ordered the way a person asks: what to watch, then what counts as a move, then
    how big, then over what period.
    """

    SYMBOL_SCOPE = "symbol_scope"
    MOVEMENT_KIND = "movement_kind"
    MOVEMENT_SIZE = "movement_size"
    MEASUREMENT_WINDOW = "measurement_window"


#: The order questions are asked in. One per turn, always the first still missing.
_ASK_ORDER: Final[tuple[MissingChoice, ...]] = (
    MissingChoice.SYMBOL_SCOPE,
    MissingChoice.MOVEMENT_KIND,
    MissingChoice.MOVEMENT_SIZE,
    MissingChoice.MEASUREMENT_WINDOW,
)

#: The catalogue key for each question, so the wording lives in one place and exists
#: in every language the product answers in.
_QUESTION_KEY: Final[dict[MissingChoice, str]] = {
    MissingChoice.SYMBOL_SCOPE: "ask.symbol_scope",
    MissingChoice.MOVEMENT_KIND: "ask.movement_kind",
    MissingChoice.MOVEMENT_SIZE: "ask.movement_size",
    MissingChoice.MEASUREMENT_WINDOW: "ask.measurement_window",
}

#: What a usable answer looks like, for the clarification contract. Plain descriptions,
#: never a JSON Schema shown to a beginner.
_ANSWER_SHAPE: Final[dict[MissingChoice, str]] = {
    MissingChoice.SYMBOL_SCOPE: "all screened coins, or one named coin",
    MissingChoice.MOVEMENT_KIND: "a rise, a fall, or both",
    MissingChoice.MOVEMENT_SIZE: "a percentage, for example 5%",
    MissingChoice.MEASUREMENT_WINDOW: "1 hour, 4 hours, 24 hours, or since the daily open",
}

#: The offered choices, so the UI can show buttons and the answer stays bounded. A
#: button a trader cannot read is as bad as a question they cannot read, so these are
#: localized with the question rather than left in English beside it.
_OPTIONS: Final[dict[MissingChoice, dict[ConversationLanguage, tuple[str, ...]]]] = {
    MissingChoice.SYMBOL_SCOPE: {
        ConversationLanguage.ENGLISH: ("All screened coins", "One specific coin"),
        ConversationLanguage.ARABIC: ("كل العملات المفحوصة", "عملة واحدة بعينها"),
        ConversationLanguage.FRENCH: ("Toutes les cryptos filtrées", "Une crypto précise"),
        ConversationLanguage.SPANISH: ("Todas las monedas filtradas", "Una moneda concreta"),
    },
    MissingChoice.MOVEMENT_KIND: {
        ConversationLanguage.ENGLISH: ("A rise", "A fall", "Both"),
        ConversationLanguage.ARABIC: ("صعود", "هبوط", "الاتنين"),
        ConversationLanguage.FRENCH: ("Une hausse", "Une baisse", "Les deux"),
        ConversationLanguage.SPANISH: ("Una subida", "Una bajada", "Ambas"),
    },
    MissingChoice.MOVEMENT_SIZE: {},
    MissingChoice.MEASUREMENT_WINDOW: {
        ConversationLanguage.ENGLISH: (
            "1 hour", "4 hours", "24 hours", "Since the daily open",
        ),
        ConversationLanguage.ARABIC: ("ساعة", "4 ساعات", "24 ساعة", "من افتتاح اليوم"),
        ConversationLanguage.FRENCH: (
            "1 heure", "4 heures", "24 heures", "Depuis l'ouverture du jour",
        ),
        ConversationLanguage.SPANISH: (
            "1 hora", "4 horas", "24 horas", "Desde la apertura del día",
        ),
    },
}


def _options_for(choice: MissingChoice, language: ConversationLanguage) -> list[str]:
    entry = _OPTIONS.get(choice, {})
    return list(entry.get(language) or entry.get(ConversationLanguage.ENGLISH) or ())


class SupportedMechanic(StrEnum):
    """The market behaviour a request is about, when the platform covers it."""

    PERCENTAGE_MOVE = "percentage_move"
    INDICATOR_LEVEL = "indicator_level"
    MOVING_AVERAGE_CROSS = "moving_average_cross"
    CANDLE_PATTERN = "candle_pattern"
    VOLUME = "volume"
    BREAKOUT = "breakout"
    SWEEP = "sweep"


#: How each supported mechanic is recognised in a trader's own words. These are
#: recognition patterns for *classification only* — they never choose a capability key
#: and never build a rule. The registry still decides what compiles.
_MECHANIC_PATTERNS: Final[tuple[tuple[SupportedMechanic, re.Pattern[str]], ...]] = (
    (
        SupportedMechanic.PERCENTAGE_MOVE,
        re.compile(
            r"\d+(?:\.\d+)?\s*%"
            r"|\b(?:percent|percentage|pct)\b"
            r"|(?:بالمئة|بالمية|في المية|نسبة)",
            re.IGNORECASE,
        ),
    ),
    (
        SupportedMechanic.INDICATOR_LEVEL,
        re.compile(
            r"\b(?:rsi|macd|stochastic|stoch|adx|atr|cci|mfi|obv|bollinger)\b",
            re.IGNORECASE,
        ),
    ),
    (
        SupportedMechanic.MOVING_AVERAGE_CROSS,
        re.compile(
            r"\b(?:ema|sma|wma|vwap|moving average)\b"
            r"|(?:متوسط متحرك)",
            re.IGNORECASE,
        ),
    ),
    (
        SupportedMechanic.CANDLE_PATTERN,
        re.compile(
            r"\b(?:candle|candles|candlestick|engulfing|doji|hammer|pin bar|wick)\b"
            r"|(?:شمعة|شموع)",
            re.IGNORECASE,
        ),
    ),
    (
        SupportedMechanic.VOLUME,
        re.compile(r"\b(?:volume|turnover)\b|(?:حجم التداول|الحجم)", re.IGNORECASE),
    ),
    (
        SupportedMechanic.BREAKOUT,
        re.compile(
            r"\b(?:breakout|break out|breaks? (?:above|below|out)|breakdown)\b"
            r"|(?:اختراق|كسر)",
            re.IGNORECASE,
        ),
    ),
    (
        SupportedMechanic.SWEEP,
        re.compile(r"\b(?:sweep|sweeps|liquidity grab|stop hunt|reclaim)\b", re.IGNORECASE),
    ),
)

#: Wording that names a *move* without a number. "a coin increases" is a percentage
#: move whose size has not been given, not an unsupported mechanic.
_BARE_MOVE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:increase|increases|rise|rises|rising|gain|gains|up|jump|jumps|pump|"
    r"drop|drops|fall|falls|falling|decline|declines|down|dump|crash)\b"
    r"|(?:يزيد|يطلع|يرتفع|صاعد|ينزل|يهبط|هابط|طلع|نزل)"
    r"|\b(?:yetla3|yenzel|tal3|nazel)\b"
    r"|\b(?:monte|hausse|baisse|chute)\b"
    r"|\b(?:sube|subida|baja|bajada|cae)\b",
    re.IGNORECASE,
)

#: The whole screened universe, said in words.
_ALL_COINS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:all|every|any|whole market|screened|each)\s+(?:screened\s+)?"
    r"(?:coin|coins|crypto|cryptos|token|tokens|asset|assets|pair|pairs|market)"
    r"|\b(?:all screened|everything|whole market)\b"
    r"|(?:كل العملات|كل العملات المفحوصة|السوق كله|أي عملة)"
    r"|\b(?:toutes les cryptos|tout le marché)\b"
    r"|\b(?:todas las monedas|todo el mercado)\b",
    re.IGNORECASE,
)

#: Words that mean "measured over the whole day" without naming a timeframe token.
_NAMED_WINDOW: Final[re.Pattern[str]] = re.compile(
    r"\b(?:since (?:today'?s? |the )?(?:daily )?open|daily open|today'?s? open|"
    r"since midnight|24\s*h(?:ours?|rs?)?|intraday)\b"
    r"|(?:من افتتاح|من الافتتاح|24 ساعة)"
    r"|\b(?:depuis l'ouverture)\b|\b(?:desde la apertura)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RequestAssessment:
    """Whether a request is buildable, and what it is still waiting on."""

    completeness: RequestCompleteness
    mechanic: SupportedMechanic | None = None
    missing: tuple[MissingChoice, ...] = field(default_factory=tuple)
    #: Values the trader already supplied. Never asked for again.
    supplied: dict[str, str] = field(default_factory=dict)

    @property
    def next_question(self) -> MissingChoice | None:
        """The one choice to ask about this turn."""

        for choice in _ASK_ORDER:
            if choice in self.missing:
                return choice
        return None

    @property
    def is_supported_incomplete(self) -> bool:
        return self.completeness is RequestCompleteness.SUPPORTED_INCOMPLETE

    def to_dict(self) -> dict[str, object]:
        return {
            "request_completeness": self.completeness.value,
            "request_mechanic": self.mechanic.value if self.mechanic else None,
            "request_missing_choices": [item.value for item in self.missing],
            "request_supplied_values": dict(self.supplied),
            "clarification_target": (
                self.next_question.value if self.next_question else None
            ),
        }


def _recognised_mechanic(text: str) -> SupportedMechanic | None:
    for mechanic, pattern in _MECHANIC_PATTERNS:
        if pattern.search(text):
            return mechanic
    if _BARE_MOVE.search(text):
        # A move with no size stated is still a percentage move. Calling it
        # unsupported because the number is missing is the defect this module exists
        # to remove.
        return SupportedMechanic.PERCENTAGE_MOVE
    return None


def assess_request(
    message: str,
    *,
    offered_capability_keys: Sequence[str] | None = None,
    known_symbols: Sequence[str] = (),
    known_window: str | None = None,
) -> RequestAssessment:
    """Classify one strategy request, and name what it is still waiting on.

    ``offered_capability_keys`` is the governed shortlist for this turn. A mechanic
    this module recognises but the registry does not offer is **not** reported as
    supported: the registry remains the authority on what can be built, exactly as
    before. Recognition only decides whether the honest answer is a question or a
    boundary.

    ``known_symbols`` and ``known_window`` carry what earlier turns already settled, so
    a value the trader has supplied is never asked for twice.
    """

    text = " ".join((message or "").split())
    if not text:
        return RequestAssessment(RequestCompleteness.UNSUPPORTED)

    mechanic = _recognised_mechanic(text)
    if mechanic is None:
        return RequestAssessment(RequestCompleteness.UNSUPPORTED)
    # A percentage move is a core primitive and needs no capability key. Every other
    # mechanic must actually be on the governed shortlist this turn was given: the
    # registry stays the authority on what can be built, and recognising a word here
    # never promotes a mechanic the server does not offer.
    if (
        mechanic is not SupportedMechanic.PERCENTAGE_MOVE
        and offered_capability_keys is not None
        and not offered_capability_keys
    ):
        return RequestAssessment(RequestCompleteness.UNSUPPORTED, mechanic=mechanic)

    supplied: dict[str, str] = {}
    missing: list[MissingChoice] = []

    symbols = tuple(extract_symbols(text)) or tuple(known_symbols)
    if symbols:
        supplied["symbols"] = ", ".join(symbols)
    elif _ALL_COINS.search(text):
        supplied["symbols"] = "all screened coins"
    else:
        missing.append(MissingChoice.SYMBOL_SCOPE)

    direction = movement_direction(text)
    if direction is not None:
        supplied["direction"] = direction
    elif mechanic is SupportedMechanic.PERCENTAGE_MOVE:
        # Only a percentage move needs a side stated separately. ``RSI above 70`` and
        # ``a bullish engulfing candle`` already carry their direction in the
        # comparison or the pattern, and asking again would make the assistant look
        # like it had not read the sentence.
        missing.append(MissingChoice.MOVEMENT_KIND)

    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if percent is not None:
        supplied["threshold_percent"] = percent.group(1)
    elif mechanic is SupportedMechanic.PERCENTAGE_MOVE:
        missing.append(MissingChoice.MOVEMENT_SIZE)

    window = known_window
    timeframes = extract_timeframes(text)
    if timeframes:
        window = timeframes[0]
    elif _NAMED_WINDOW.search(text):
        window = "named"
    if window:
        supplied["window"] = window
    else:
        missing.append(MissingChoice.MEASUREMENT_WINDOW)

    if not missing:
        return RequestAssessment(
            RequestCompleteness.COMPLETE, mechanic=mechanic, supplied=supplied
        )
    return RequestAssessment(
        RequestCompleteness.SUPPORTED_INCOMPLETE,
        mechanic=mechanic,
        missing=tuple(missing),
        supplied=supplied,
    )


def clarification_for_choice(
    choice: MissingChoice,
    *,
    language: ConversationLanguage,
    source_turn_id: str,
    threshold_percent: str | None = None,
) -> ClarificationContract:
    """One typed question, in the conversation's language, in plain words.

    No field name reaches the trader. "trigger timeframe" is a name from our schema;
    "over what period should the 5% rise be measured" is the same question asked the
    way the person who typed the sentence would ask it.
    """

    threshold = f"{threshold_percent}%" if threshold_percent else "the"
    question = localized(_QUESTION_KEY[choice], language, threshold=threshold)
    digest = hashlib.sha256(f"{source_turn_id}:{choice.value}".encode()).hexdigest()[:20]
    return ClarificationContract(
        question_id=f"clarification_{digest}",
        question=question,
        # The reason is operator-facing evidence, never shown as a second sentence.
        reason=f"supported request waiting on {choice.value}",
        target_type="condition_creation",
        target_field=None,
        expected_answer_schema=json.dumps(
            {"description": _ANSWER_SHAPE[choice]}, sort_keys=True, separators=(",", ":")
        ),
        mutating=True,
        allowed_options=_options_for(choice, language),
    )
