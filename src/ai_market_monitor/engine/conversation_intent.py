"""What the trader is actually asking for this turn, decided before anything else.

The product has always had exactly two destinations for a turn: change the inactive
strategy, or talk. Everything that was not a strategy edit fell into "talk", and "talk"
had one deterministic answer:

    No strategy state changed. Scanner checks a strategy on demand; Monitor keeps
    evaluating an explicitly approved strategy. Neither path places trades.

So a trader who selected Scanner and then asked *what coins are up at least 5% now?* —
a live, read-only market question with an obvious destination — was told the difference
between two product modes. When they replied ``??``, the same classification produced
the same sentence again.

Five destinations, not two
--------------------------

============================  ==================================================
intent                         where the turn goes
============================  ==================================================
``STRATEGY_EDIT``              the compact planner and the canonical mutation path
``CONTINUOUS_MONITOR``         the same path, in monitor mode
``ON_DEMAND_SCAN``             the authenticated read-only scan, no mutation
``PRODUCT_EXPLANATION``        an answer about how the product works
``MARKET_QUESTION``            a general market question, answered without scanning
``CONFUSION_SIGNAL``           recovery: never repeat, return to the real goal
``SOCIAL``                     a greeting or acknowledgement
============================  ==================================================

This module only *classifies* and reports which trader-controlled values are still
missing. It never scans, never mutates, never invents a value, and never decides that
something is unsupported — those remain exactly where they were.

Vocabulary is borrowed, never redeclared: direction from ``engine/price_movement``,
comparisons from ``engine/comparators``, timeframes and symbols from
``engine/turn_fragments``. A phrase one reader understands must not be invisible here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ai_market_monitor.engine.comparators import find_comparator_for_value
from ai_market_monitor.engine.price_movement import movement_direction
from ai_market_monitor.engine.turn_fragments import extract_symbols, extract_timeframes

__all__ = [
    "ConversationIntent",
    "IntentReading",
    "ScanSlots",
    "classify_turn",
    "is_confusion_signal",
    "scan_window_answer",
    "scan_window_is_explicit",
    "selected_mode_word",
]


class ConversationIntent(StrEnum):
    STRATEGY_EDIT = "STRATEGY_EDIT"
    CONTINUOUS_MONITOR = "CONTINUOUS_MONITOR"
    ON_DEMAND_SCAN = "ON_DEMAND_SCAN"
    PRODUCT_EXPLANATION = "PRODUCT_EXPLANATION"
    MARKET_QUESTION = "MARKET_QUESTION"
    CONFUSION_SIGNAL = "CONFUSION_SIGNAL"
    SOCIAL = "SOCIAL"


# --------------------------------------------------------------------------------
# Confusion
# --------------------------------------------------------------------------------

#: A turn that says only this is the trader telling us the last answer missed. It is
#: never a market instruction, so it must never be planned as one.
_CONFUSION_EXACT: Final[frozenset[str]] = frozenset(
    {
        "?", "??", "???", "????",
        "what", "what?", "what??", "huh", "huh?", "eh", "eh?",
        "wat", "wut", "sorry?", "come again", "come again?",
        "ايه", "ايه؟", "إيه", "إيه؟", "ايه ده", "ايه ده؟", "مش فاهم", "مش فاهمة",
        "لم أفهم", "لا أفهم", "ما فهمت",
        "quoi", "quoi ?", "hein", "hein ?", "comment ?",
        "qué", "qué?", "¿qué?", "cómo?", "¿cómo?",
    }
)

#: Longer wordings of the same signal.
_CONFUSION_PHRASES: Final[tuple[str, ...]] = (
    "i don't understand",
    "i dont understand",
    "i do not understand",
    "that didn't answer",
    "that didnt answer",
    "that did not answer",
    "you didn't answer",
    "you didnt answer",
    "not what i asked",
    "that's not what i asked",
    "thats not what i asked",
    "you're not answering",
    "youre not answering",
    "makes no sense",
    "doesn't make sense",
    "مش فاهم",
    "مش ده اللي سألت",
    "ده مش جواب",
    "لم تجب",
    "ما جاوبتش",
    "je ne comprends pas",
    "ce n'est pas ma question",
    "vous n'avez pas répondu",
    "no entiendo",
    "eso no responde",
    "no has respondido",
)


def is_confusion_signal(message: str) -> bool:
    """True when the turn's only content is "that did not answer me"."""

    collapsed = " ".join((message or "").split()).casefold().strip()
    if not collapsed:
        return False
    if collapsed.strip(" .!") in _CONFUSION_EXACT:
        return True
    # Only punctuation and question marks is the same signal in any language.
    if collapsed and not re.search(r"[^\W_]", collapsed) and "?" in collapsed:
        return True
    return any(phrase in collapsed for phrase in _CONFUSION_PHRASES)


# --------------------------------------------------------------------------------
# Live-scan / explanation / instruction vocabulary
# --------------------------------------------------------------------------------

#: "right now" — the word that turns a market question into a request to look at the
#: market *this moment*, which is what an on-demand scan is.
_NOW_MARKERS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:now|right now|currently|at the moment|today|so far today|"
    r"at present|as we speak)\b"
    r"|(?:دلوقتي|الان|الآن|النهارده|النهاردة|حاليا|حالياً|اليوم)"
    r"|\b(?:delwa2ti|dilwa2ti|elnaharda|enaharda)\b"
    r"|\b(?:maintenant|aujourd'hui|actuellement)\b"
    r"|\b(?:ahora|hoy|actualmente)\b"
    r"|(?:сейчас|сегодня|в данный момент|на данный момент)",
    re.IGNORECASE,
)

#: Asking *the market* something: which coins, what is moving, show me.
_MARKET_QUERY: Final[re.Pattern[str]] = re.compile(
    r"\b(?:what|which|any|show|list|find|search|scan|screen|give)\b"
    r"|(?:ايه|إيه|انهي|أنهي|ما هي|ماهي|اعرض|هات|دور|افحص|فين|ما ال|أي |اي |كم )"
    r"|\b(?:eh|anhi|hat|dawar|warini)\b"
    r"|\b(?:quel|quels|quelle|quelles|montre|affiche|cherche|trouve)\b"
    r"|\b(?:qué|cuál|cuáles|muestra|busca|encuentra|dame)\b"
    r"|(?:какие|какая|какой|каких|что|покажи|найди|список|сколько)",
    re.IGNORECASE,
)

#: The thing being asked about is a market instrument, not the product.
_MARKET_SUBJECT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:coin|coins|crypto|cryptos|token|tokens|pair|pairs|asset|assets|"
    r"market|markets|symbol|symbols|altcoin|altcoins)\b"
    r"|(?:عملة|عملات|العملات|سوق|السوق|رمز|رموز)"
    r"|\b(?:3omla|3omlat|coinat)\b"
    r"|\b(?:pièce|pièces|crypto|cryptos|marché|actif|actifs)\b"
    r"|\b(?:moneda|monedas|cripto|criptos|mercado|activo|activos)\b"
    r"|(?:монета|монеты|монет|монетах|криптовалюта|криптовалюты|рынок|рынке|"
    r"актив|активы|пара|пары|токен|токены)",
    re.IGNORECASE,
)

#: The thing being asked about is the product itself.
_PRODUCT_SUBJECT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:scanner|monitor|monitoring|hilalmarkets|halal|sharia|screening|"
    r"watchlist|subscription|plan|price|billing|approval|alerts?)\b"
    r"|(?:سكانر|مراقبة|حلال|شريعة|فحص|اشتراك)"
    r"|\b(?:scanner|moniteur|surveillance|abonnement)\b"
    r"|\b(?:escáner|monitor|suscripción)\b",
    re.IGNORECASE,
)

#: Asking *how something works* rather than what the market is doing.
_EXPLANATION_QUERY: Final[re.Pattern[str]] = re.compile(
    r"\b(?:what is|what's|whats|what are|how does|how do|how can|explain|"
    r"difference between|tell me about|meaning of|what does)\b"
    r"|(?:يعني ايه|يعني إيه|ازاي|إزاي|ما معنى|اشرح|الفرق بين)"
    r"|\b(?:ya3ni eh|ezay|ezzay|esharh)\b"
    r"|\b(?:qu'est-ce que|comment ça marche|explique|différence entre)\b"
    r"|\b(?:qué es|cómo funciona|explica|diferencia entre)\b",
    re.IGNORECASE,
)

#: Asking the product to keep watching, rather than to look once.
_CONTINUOUS_MARKERS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:keep|continuously|continuous|always|whenever|every time|each time|"
    r"ongoing|persistent|monitor|notify me when|tell me when|alert me when|"
    r"let me know when)\b"
    r"|(?:كل ما|كلما|دايما|دائما|طول الوقت|راقب|نبهني لما|قوللي لما)"
    r"|\b(?:kol ma|dayman|ra2eb|nabahni)\b"
    r"|\b(?:surveille|préviens-moi quand|à chaque fois)\b"
    r"|\b(?:vigila|avísame cuando|cada vez que)\b",
    re.IGNORECASE,
)

#: Asking the product to create something.
_CREATE_MARKERS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:create|make|set up|setup|build|add|configure|i want|i'd like|"
    r"give me)\b"
    r"|(?:اعمل|إعمل|انشئ|أنشئ|ضيف|أضف|عايز|عاوز|أريد|جهز)"
    r"|\b(?:e3mel|a3mel|3ayez|3awez|dayef)\b"
    r"|\b(?:crée|créer|ajoute|configure|je veux|je voudrais)\b"
    r"|\b(?:crea|crear|añade|configura|quiero|quisiera)\b",
    re.IGNORECASE,
)

#: A greeting or bare acknowledgement.
_SOCIAL_EXACT: Final[frozenset[str]] = frozenset(
    {
        "hi", "hello", "hey", "yo", "thanks", "thank you", "ok", "okay", "k",
        "cool", "great", "nice", "got it", "sure", "yes", "no", "bye",
        "سلام", "السلام عليكم", "اهلا", "أهلا", "شكرا", "تمام", "ماشي", "اوك",
        "salut", "bonjour", "merci", "d'accord",
        "hola", "gracias", "vale",
    }
)

#: The one-word mode choices the product's own start buttons send.
_MODE_WORDS: Final[dict[str, str]] = {
    "scanner": "scanner",
    "scan": "scanner",
    "monitor": "monitor",
    "monitoring": "monitor",
    "سكانر": "scanner",
    "فحص": "scanner",
    "مراقبة": "monitor",
    "escáner": "scanner",
    "escaner": "scanner",
    "moniteur": "monitor",
    "сканер": "scanner",
    "монитор": "monitor",
}


def selected_mode_word(message: str) -> str | None:
    """``scanner``/``monitor`` when the whole turn is that one word, else ``None``.

    Shared so a typed "Scanner" and the Scanner button reach the same governed mode
    change. They used to be read in two places: the button set ``draft.mode`` through an
    authorized patch, and the typed word set a note nothing else looked at, so the next
    market question did not know Scanner had been chosen.
    """

    collapsed = " ".join((message or "").split()).casefold().strip(" .!?،؟")
    return _MODE_WORDS.get(collapsed)


#: Answers that mean "yes, the rolling 24-hour window". The backend supports no other
#: window, so this is the whole accepted vocabulary for that question — including a bare
#: yes, because a one-option question is answered that way more often than not.
_WINDOW_24H: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"24\s*h(?:ours?|rs?)?|1\s*d|one\s+day|last\s+day|last\s+24\s*h(?:ours?)?|"
    r"rolling\s+24\s*h(?:ours?)?|today|"
    r"yes|yeah|yep|yup|ok|okay|sure|please|do it|go ahead|"
    r"24\s*heures?|une\s+journ[ée]e|derni[èe]res?\s+24\s*heures?|oui|d.accord|"
    r"24\s*horas?|un\s+d[ií]a|[úu]ltimas?\s+24\s*horas?|s[ií]|claro|vale|"
    r"24\s*(?:часа|часов)|сутки|последние\s+24\s*часа|да|хорошо|конечно|"
    r"نعم|أيوه|ايوه|اه|آه|تمام|ماشي|حاضر|24\s*ساعة|24\s*ساعه|يوم\s*كامل|آخر\s*24\s*ساعة"
    r")$",
    re.IGNORECASE,
)

#: The same window stated inside a longer sentence, for the first turn rather than a
#: bare answer to the question.
_WINDOW_24H_INLINE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:24\s*h(?:ours?|rs?)?|1d|one\s+day|last\s+day|rolling\s+24)\b"
    r"|24\s*(?:ساعة|ساعه)|\bيوم\s*كامل\b"
    r"|\b(?:24\s*heures?|une\s+journ[ée]e)\b"
    r"|\b(?:24\s*horas?|un\s+d[ií]a)\b"
    r"|24\s*(?:часа|часов)|\bсутки\b",
    re.IGNORECASE,
)


def scan_window_answer(message: str) -> str | None:
    """``"24h"`` when this turn answers the window question, else ``None``.

    Deliberately total and deliberately narrow. The provider exposes one rolling
    percentage window, so "24h" is the only value this can ever return — a different
    period is not silently rounded to it, it simply is not recognised here.
    """

    collapsed = " ".join((message or "").split()).strip(" .!،؟")
    if not collapsed:
        return None
    if _WINDOW_24H.match(collapsed) or _WINDOW_24H_INLINE.search(collapsed):
        return "24h"
    return None


def scan_window_is_explicit(message: str) -> bool:
    """Whether the text itself names the 24-hour window, rather than merely agreeing.

    Grounding uses this: "yes" authorises the window the server offered, but it is not
    the trader *writing* 24 hours, and provenance must not claim it was.
    """

    return bool(_WINDOW_24H_INLINE.search(" ".join((message or "").split())))


@dataclass(frozen=True, slots=True)
class ScanSlots:
    """The trader-controlled values a live scan needs, and which are still missing.

    Nothing here is ever defaulted. A missing window is asked about; it is not filled
    with "24h because that is usual", because a scan answered over the wrong period is
    a wrong answer delivered confidently.
    """

    threshold_percent: float | None = None
    direction: str | None = None
    #: The measurement period, when the trader named one.
    window: str | None = None
    #: Explicit symbols the trader named, if any.
    symbols: tuple[str, ...] = field(default_factory=tuple)
    #: True when the trader asked for the whole screened universe.
    screened_universe: bool = False

    @property
    def missing(self) -> tuple[str, ...]:
        absent: list[str] = []
        if self.threshold_percent is None:
            absent.append("threshold")
        if self.direction is None:
            absent.append("direction")
        if self.window is None:
            absent.append("window")
        return tuple(absent)

    @property
    def ready(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, object]:
        return {
            "scan_threshold_percent": self.threshold_percent,
            "scan_direction": self.direction,
            "scan_window": self.window,
            "scan_symbols": list(self.symbols),
            "scan_screened_universe": self.screened_universe,
            "scan_missing_slots": list(self.missing),
        }


@dataclass(frozen=True, slots=True)
class IntentReading:
    """The routing decision for one turn, and the evidence behind it."""

    intent: ConversationIntent
    #: ``scanner`` | ``monitor`` | ``None`` — a mode the turn itself selected.
    selected_mode: str | None = None
    slots: ScanSlots = field(default_factory=ScanSlots)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_read_only(self) -> bool:
        """True when this turn must not change any persisted strategy."""

        return self.intent in {
            ConversationIntent.ON_DEMAND_SCAN,
            ConversationIntent.PRODUCT_EXPLANATION,
            ConversationIntent.MARKET_QUESTION,
            ConversationIntent.CONFUSION_SIGNAL,
            ConversationIntent.SOCIAL,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "conversation_intent": self.intent.value,
            "conversation_selected_mode": self.selected_mode,
            "conversation_intent_reasons": list(self.reasons),
            "conversation_read_only": self.is_read_only,
            **self.slots.to_dict(),
        }


_PERCENT_RE: Final[re.Pattern[str]] = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*%")

#: Periods a trader names in words rather than as a timeframe token.
_WINDOW_PHRASES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (
        re.compile(
            r"\b(?:since (?:today'?s? |the )?(?:daily )?open|today'?s? open|"
            r"since the daily open|daily open|since midnight)\b"
            r"|(?:من افتتاح|من الافتتاح)"
            r"|\b(?:depuis l'ouverture)\b|\b(?:desde la apertura)\b",
            re.IGNORECASE,
        ),
        "daily_open",
    ),
    (
        re.compile(
            r"\b(?:24\s*h(?:ours?|rs?)?|twenty[- ]four hours|last day|past day|"
            r"one day|1\s*day)\b"
            r"|(?:24 ساعة|٢٤ ساعة|يوم)"
            r"|\b(?:24\s*heures)\b|\b(?:24\s*horas)\b",
            re.IGNORECASE,
        ),
        "24h",
    ),
)


def _read_scan_slots(message: str) -> ScanSlots:
    """Read only what the trader actually stated. Absent stays absent."""

    threshold: float | None = None
    match = _PERCENT_RE.search(message or "")
    if match is not None:
        try:
            threshold = abs(float(match.group(1)))
        except ValueError:  # pragma: no cover - the pattern only matches numbers
            threshold = None

    direction = movement_direction(message or "")

    window: str | None = None
    for pattern, name in _WINDOW_PHRASES:
        if pattern.search(message or ""):
            window = name
            break
    if window is None:
        timeframes = extract_timeframes(message or "")
        if len(timeframes) == 1:
            window = timeframes[0]

    symbols = tuple(extract_symbols(message or ""))
    screened = bool(
        re.search(
            r"\b(?:all|every|screened|halal|whole market|any coin|all coins)\b"
            r"|(?:كل العملات|كل|المفحوصة|حلال)"
            r"|\b(?:toutes|filtrées)\b|\b(?:todas|filtradas)\b",
            message or "",
            re.IGNORECASE,
        )
    )
    return ScanSlots(
        threshold_percent=threshold,
        direction=direction,
        window=window,
        symbols=symbols,
        screened_universe=screened and not symbols,
    )


def _comparator_is_stated(message: str, threshold: float | None) -> bool:
    """Whether the trader stated how to compare the number they gave."""

    if threshold is None:
        return False
    return find_comparator_for_value(message or "", 0, 0) is not None or any(
        find_comparator_for_value(message, match.start(), match.end()) is not None
        for match in re.finditer(re.escape(f"{threshold:g}"), message or "")
    )


def classify_turn(
    message: str,
    *,
    active_mode: str | None = None,
    previous_intent: ConversationIntent | None = None,
    has_open_question: bool = False,
    pending_scan: bool = False,
) -> IntentReading:
    """Decide where this turn goes.

    ``active_mode`` and ``previous_intent`` are the conversation's memory. They are
    what keeps a trader who selected Scanner and then asked a market question from
    being handed a product explanation: the destination they already chose stands
    unless this turn changes it.

    ``pending_scan`` says a live scan is already half-collected and waiting on one
    answer. It has to win over every reading below, because the answer to "which
    period?" is a bare period — and a bare period is also what a strategy edit looks
    like. "24 hours" was being compiled as a rule while the scan it belonged to sat
    forgotten.
    """

    text = " ".join((message or "").split())
    collapsed = text.casefold().strip(" .!")
    reasons: list[str] = []

    if not text:
        return IntentReading(ConversationIntent.SOCIAL, selected_mode=active_mode)

    if is_confusion_signal(text):
        return IntentReading(
            ConversationIntent.CONFUSION_SIGNAL,
            selected_mode=active_mode,
            reasons=("confusion_marker",),
        )

    if pending_scan:
        # A scan is waiting on one answer, so a bare "24 hours" belongs to it rather
        # than being read as a strategy value. Two things still take the conversation
        # away: choosing the other mode, and asking outright for something to be built.
        # Without that second escape a trader could not leave a scan they had lost
        # interest in — every message came back as the same question.
        chosen = _MODE_WORDS.get(collapsed)
        if chosen == "monitor":
            return IntentReading(
                ConversationIntent.CONTINUOUS_MONITOR,
                selected_mode=chosen,
                reasons=("mode_selection", "abandons_pending_scan"),
            )
        slots = _read_scan_slots(text)
        window = scan_window_answer(text)
        if window is None and (
            _CREATE_MARKERS.search(text) or _CONTINUOUS_MARKERS.search(text)
        ):
            return IntentReading(
                (
                    ConversationIntent.CONTINUOUS_MONITOR
                    if _CONTINUOUS_MARKERS.search(text) and active_mode != "scanner"
                    else ConversationIntent.STRATEGY_EDIT
                ),
                selected_mode=active_mode,
                slots=slots,
                reasons=("abandons_pending_scan", "asks_for_something_built"),
            )
        return IntentReading(
            ConversationIntent.ON_DEMAND_SCAN,
            selected_mode=active_mode or "scanner",
            slots=(
                slots
                if window is None
                else ScanSlots(
                    threshold_percent=slots.threshold_percent,
                    direction=slots.direction,
                    window=window,
                    symbols=slots.symbols,
                    screened_universe=slots.screened_universe,
                )
            ),
            reasons=(
                "pending_scan_open",
                "answers_scan_window" if window is not None else "scan_answer_unclear",
            ),
        )

    mode = _MODE_WORDS.get(collapsed)
    if mode is not None:
        # The product's own one-word mode button. It selects a destination and asks
        # for nothing, so it is not an instruction and not a question.
        return IntentReading(
            ConversationIntent.SOCIAL,
            selected_mode=mode,
            reasons=("mode_selection",),
        )

    if collapsed in _SOCIAL_EXACT:
        return IntentReading(
            ConversationIntent.SOCIAL,
            selected_mode=active_mode,
            reasons=("social_only",),
        )

    slots = _read_scan_slots(text)
    asks_market = bool(_MARKET_QUERY.search(text)) and bool(_MARKET_SUBJECT.search(text))
    asks_explanation = bool(_EXPLANATION_QUERY.search(text))
    about_product = bool(_PRODUCT_SUBJECT.search(text))
    says_now = bool(_NOW_MARKERS.search(text))
    wants_continuous = bool(_CONTINUOUS_MARKERS.search(text))
    wants_created = bool(_CREATE_MARKERS.search(text))

    states_values = bool(
        slots.threshold_percent is not None or slots.symbols or slots.direction
    )

    # An instruction that happens to contain a product word is still an instruction.
    # "Monitor BTC/USDT when the 15m candle rises 5% — also what does the timeframe
    # change?" names a market, a size and a period; reading it as "explain Monitor"
    # because the word appears would refuse to build the rule the trader just gave.
    if states_values and (wants_continuous or wants_created) and not says_now:
        return IntentReading(
            (
                ConversationIntent.CONTINUOUS_MONITOR
                if wants_continuous and active_mode != "scanner"
                else ConversationIntent.STRATEGY_EDIT
            ),
            selected_mode=active_mode,
            slots=slots,
            reasons=("states_market_values", "instruction_wins_over_product_word"),
        )

    # An explanation request is about the *product*, and asks how something works.
    # "what coins are up 5% now" contains neither, and used to land here anyway.
    if asks_explanation and about_product and not asks_market:
        return IntentReading(
            ConversationIntent.PRODUCT_EXPLANATION,
            selected_mode=active_mode,
            slots=slots,
            reasons=("explanation_query", "product_subject"),
        )

    if asks_market:
        reasons.append("market_query")
        # "now" makes it a live look at the market. So does having already chosen
        # Scanner: that is the destination the trader selected, and a market question
        # inside it is a scan request unless they say otherwise.
        if says_now:
            reasons.append("now_marker")
        if active_mode == "scanner":
            reasons.append("scanner_mode_active")
        if previous_intent is ConversationIntent.ON_DEMAND_SCAN:
            reasons.append("continuing_scan")
        if says_now or active_mode == "scanner" or previous_intent is (
            ConversationIntent.ON_DEMAND_SCAN
        ):
            if wants_continuous and not says_now:
                return IntentReading(
                    ConversationIntent.CONTINUOUS_MONITOR,
                    selected_mode=active_mode,
                    slots=slots,
                    reasons=(*reasons, "continuous_marker"),
                )
            return IntentReading(
                ConversationIntent.ON_DEMAND_SCAN,
                selected_mode=active_mode or "scanner",
                slots=slots,
                reasons=tuple(reasons),
            )
        return IntentReading(
            ConversationIntent.MARKET_QUESTION,
            selected_mode=active_mode,
            slots=slots,
            reasons=tuple(reasons),
        )

    if asks_explanation and about_product:
        return IntentReading(
            ConversationIntent.PRODUCT_EXPLANATION,
            selected_mode=active_mode,
            slots=slots,
            reasons=("explanation_query",),
        )

    if wants_continuous or (wants_created and not says_now):
        intent = (
            ConversationIntent.CONTINUOUS_MONITOR
            if wants_continuous and active_mode != "scanner"
            else ConversationIntent.STRATEGY_EDIT
        )
        return IntentReading(
            intent,
            selected_mode=active_mode,
            slots=slots,
            reasons=("create_marker" if wants_created else "continuous_marker",),
        )

    if has_open_question:
        # An answer to the question we just asked belongs to whatever we were doing.
        return IntentReading(
            previous_intent or ConversationIntent.STRATEGY_EDIT,
            selected_mode=active_mode,
            slots=slots,
            reasons=("answers_open_question",),
        )

    # A turn that states a market value with no question and no product subject is an
    # instruction. This is the ordinary "BTCUSDT 15m bullish 2%" case.
    if slots.threshold_percent is not None or slots.symbols or slots.direction:
        return IntentReading(
            ConversationIntent.STRATEGY_EDIT,
            selected_mode=active_mode,
            slots=slots,
            reasons=("states_market_values",),
        )

    if about_product:
        return IntentReading(
            ConversationIntent.PRODUCT_EXPLANATION,
            selected_mode=active_mode,
            slots=slots,
            reasons=("product_subject",),
        )
    return IntentReading(
        ConversationIntent.STRATEGY_EDIT,
        selected_mode=active_mode,
        slots=slots,
        reasons=("default_instruction",),
    )
