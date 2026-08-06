"""One reader for every answer to an open question.

The defect this module removes
------------------------------

An open question used to be answered in several different places.  The Scanner scope
question had its own reader, the alert workflow had ``_supported_answer_value``, the
window question had a regular expression in the router, and each of them understood a
different, smaller set of words than the others.  A trader who typed ``all`` instead of
clicking *All eligible spot assets*, or ``1 minute`` instead of ``1h``, hit whichever
reader happened to be first and got a generic reply that said nothing was set up — the
open question was still open, the answer was simply thrown away by a reader that had
never been taught that word.

Worse, an answer the reader refused **fell through** to normal intent classification.
The pending workflow was then invisible, so the turn was answered as if the trader had
started a new conversation.

So: exactly one resolver, one normalisation, one alias vocabulary, one typed result.
Buttons and typed text enter here and leave as the same canonical value.

What this module will not do
----------------------------

* It never guesses.  Two plausible readings return ``AMBIGUOUS`` and the caller asks.
* It never silently autocorrects.  One clear near-miss returns ``CONFIRM_CANDIDATE``
  and the caller asks ``Did you mean 1h?`` — the value is not stored until confirmed.
* It never invents a value the domain does not contain, and never widens a domain to
  make an answer fit.
* An answer it cannot read keeps the question open.  Refusing is a result, not a
  failure, and it is always distinguishable from "the trader changed the subject".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ai_market_monitor.engine.comparators import detect_comparator
from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    option_label_map,
    scope_labels,
)
from ai_market_monitor.engine.price_movement import movement_direction
from ai_market_monitor.engine.turn_fragments import extract_symbols
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.timeframes import (
    COMMON_TIMEFRAMES,
    ORDERED_TIMEFRAMES,
    normalize_timeframe_alias,
)

__all__ = [
    "AFFIRMATIVE_ANSWERS",
    "NEGATIVE_ANSWERS",
    "RESUME_REQUESTS",
    "AnswerDomain",
    "AnswerOutcome",
    "AnswerResolution",
    "AnswerStage",
    "ConfirmationReply",
    "canonical_values",
    "display_options",
    "domain_for_field",
    "is_resume_request",
    "labels_for",
    "normalize_answer_text",
    "resolve_active_answer",
    "resolve_confirmation",
]


class AnswerOutcome(StrEnum):
    """What one message did to the open question. Exactly one of these, always."""

    #: The message names one canonical value with no doubt. Store it and move on.
    RESOLVED = "RESOLVED"
    #: One near-miss, and only one. Ask "did you mean X?" before storing anything.
    CONFIRM_CANDIDATE = "CONFIRM_CANDIDATE"
    #: Several readings are equally good. Ask, listing them. Never pick one.
    AMBIGUOUS = "AMBIGUOUS"
    #: A real answer this platform cannot do. Say why; keep the question open.
    UNSUPPORTED = "UNSUPPORTED"
    #: The trader asked to stop. Drop the question, keep nothing.
    CANCELLED = "CANCELLED"
    #: Not an answer at all — a different, complete request. Hand back to routing.
    NEW_REQUEST = "NEW_REQUEST"


class AnswerDomain(StrEnum):
    """The kind of value one question is waiting for."""

    TIMEFRAME = "timeframe"
    REFERENCE_POINT = "reference_point"
    COMPARATOR = "comparator"
    MOVEMENT_DIRECTION = "movement_direction"
    PERCENT = "percent"
    SYMBOL_SCOPE = "symbol_scope"
    UNIVERSE_MODE = "universe_mode"
    SCAN_WINDOW = "scan_window"
    #: Anything whose values arrive as a bounded ``allowed_options`` list.
    ENUMERATED = "enumerated"
    #: Free text with no bounded domain — a name, a list of symbols.
    FREE_TEXT = "free_text"


class AnswerStage(StrEnum):
    """Which of the two questions a message was read against.

    A near miss turns one question into two. ``qh`` does not answer *which period?*, so
    the assistant asks a second, narrower question — *did you mean 1h?* — and the next
    message belongs to **that** one. Reading it against the period question again is how
    a plain ``yes`` used to be thrown away: it is not a period, so it looked like
    nonsense, and the trader was shown the same list a third time.
    """

    #: Waiting for a value from the question's own domain.
    ANSWER = "answer"
    #: Waiting for yes or no about one exact proposed value.
    CONFIRMATION = "confirmation"


class ConfirmationReply(StrEnum):
    """What one message said about a yes/no question."""

    AFFIRMATIVE = "AFFIRMATIVE"
    NEGATIVE = "NEGATIVE"
    #: Neither. Never guessed either way — a wrong guess stores a value nobody chose.
    UNCLEAR = "UNCLEAR"


@dataclass(frozen=True, slots=True)
class AnswerResolution:
    """One typed outcome, and the evidence for it."""

    outcome: AnswerOutcome
    canonical_value: str | float | None = None
    #: Populated for ``AMBIGUOUS``; the caller lists these back to the trader.
    candidates: tuple[str, ...] = field(default_factory=tuple)
    #: Operator-facing, never rendered raw to a beginner.
    reason: str = ""
    #: Which question this message was read against.
    stage: AnswerStage = AnswerStage.ANSWER
    #: The near miss the caller must still hold, or ``None`` to drop it. This is the
    #: whole confirmation sub-state: one field, written by the resolver, stored by the
    #: caller. Nothing else may set it, so a proposal can never outlive its question.
    proposed_value: str | None = None
    #: True when the message *did* look like an answer and the trouble was choosing
    #: between readings. False when nothing in it answers the question at all.
    #:
    #: The two need different words. "I found two things this could mean" invites the
    #: trader to pick; "I could not read that" invites them to try again. Both used to
    #: come back as the same shrug, so a trader one keystroke from success was told the
    #: same thing as one who typed nonsense.
    plausible_readings: bool = False

    @property
    def stores_a_value(self) -> bool:
        return self.outcome is AnswerOutcome.RESOLVED

    @property
    def keeps_the_question(self) -> bool:
        """Everything except a stored value or a cancellation leaves it open."""

        return self.outcome in {
            AnswerOutcome.CONFIRM_CANDIDATE,
            AnswerOutcome.AMBIGUOUS,
            AnswerOutcome.UNSUPPORTED,
        }

    @property
    def rejected_candidate(self) -> bool:
        """True when the trader said "no, that is not what I meant"."""

        return (
            self.stage is AnswerStage.CONFIRMATION
            and self.outcome is AnswerOutcome.AMBIGUOUS
            and self.proposed_value is None
        )


# --------------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------------

#: Arabic letter shapes and vowel marks that carry no meaning for an answer word.
_ARABIC_FOLD: Final[dict[int, str]] = str.maketrans(
    {
        "ـ": "",
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ى": "ي",
        "ة": "ه",
        **{chr(mark): "" for mark in range(0x064B, 0x0653)},
    }
)

#: Every dash, quote and separator a keyboard or an autocorrect can produce.
_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[-–—_/\\.,;:!?¿¡'\"“”‘’«»()\[\]{}]+")

#: Filler that changes nothing about which option was chosen.
_FILLER: Final[frozenset[str]] = frozenset(
    {
        "the", "a", "an", "please", "use", "lets", "let", "go", "with", "i", "want",
        "choose", "select", "option", "one",
        "s", "el", "la", "los", "las", "por", "favor", "usa",
        "le", "les", "des", "du", "de", "utilise", "svp",
        "пожалуйста", "используй", "давай",
        "من", "فضلك", "لو", "سمحت", "استخدم", "يا", "ال",
    }
)


def normalize_answer_text(value: str) -> str:
    """One spelling for one answer, whatever the keyboard produced.

    Case, spacing, punctuation, hyphens, Unicode look-alikes, Arabic letter shapes and
    polite filler are all removed.  Two messages that mean the same choice come out of
    here identical, so the alias tables stay small and readable instead of listing a
    row per punctuation variant.
    """

    text = unicodedata.normalize("NFKC", value or "")
    text = _SEPARATORS.sub(" ", text).casefold().translate(_ARABIC_FOLD)
    words = [word for word in text.split() if word]
    kept = [word for word in words if word not in _FILLER]
    return " ".join(kept or words)


def _singular_forms(value: str) -> set[str]:
    """``coins`` and ``coin`` are the same answer; so are ``horas`` and ``hora``."""

    forms = {value}
    for word in (value,):
        parts = word.split()
        stripped = " ".join(
            part[:-1] if len(part) > 3 and part.endswith("s") else part for part in parts
        )
        forms.add(stripped)
    return forms


def _variants(value: str) -> set[str]:
    normalized = normalize_answer_text(value)
    return {item for item in _singular_forms(normalized) if item}


# --------------------------------------------------------------------------------
# Typo tolerance
# --------------------------------------------------------------------------------


#: Which keys sit next to which on the layouts this product's traders type on. A
#: mistyped key is nearly always the one beside the intended one, so this is what
#: separates "obviously meant 1h" from "could have meant any of five timeframes".
_KEYBOARD_ROWS: Final[tuple[str, ...]] = (
    "1234567890",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "йцукенгшщзхъ",
    "фывапролджэ",
    "ячсмитьбю",
    "ضصثقفغعهخحجد",
    "شسيبلاتنمكط",
    "ئءؤرلاىةوزظ",
)


def _neighbour_map() -> dict[str, frozenset[str]]:
    neighbours: dict[str, set[str]] = {}

    def link(left: str, right: str) -> None:
        neighbours.setdefault(left, set()).add(right)
        neighbours.setdefault(right, set()).add(left)

    for row_index, row in enumerate(_KEYBOARD_ROWS):
        for column, key in enumerate(row):
            if column + 1 < len(row):
                link(key, row[column + 1])
            if row_index + 1 < len(_KEYBOARD_ROWS):
                below = _KEYBOARD_ROWS[row_index + 1]
                for offset in (0, 1):
                    if column - offset < len(below) and column - offset >= 0:
                        link(key, below[column - offset])
    return {key: frozenset(values) for key, values in neighbours.items()}


_NEIGHBOURS: Final[dict[str, frozenset[str]]] = _neighbour_map()


def _edit_kind(left: str, right: str) -> str | None:
    """How far apart two spellings are, when that distance is at most one edit.

    Returns ``"exact"``, ``"neighbour"`` (a substitution of adjacent keys),
    ``"edit"`` (any other single edit) or ``None``.  One edit is the whole budget: an
    insert, a delete, a substitution, or two neighbouring characters swapped.  Two
    edits is guessing, and this module does not guess.
    """

    if left == right:
        return "exact"
    length_left, length_right = len(left), len(right)
    if abs(length_left - length_right) > 1:
        return None
    if length_left == length_right:
        differences = [index for index in range(length_left) if left[index] != right[index]]
        if len(differences) == 1:
            position = differences[0]
            adjacent = right[position] in _NEIGHBOURS.get(left[position], frozenset())
            return "neighbour" if adjacent else "edit"
        if len(differences) == 2:
            first, second = differences
            swapped = (
                second == first + 1
                and left[first] == right[second]
                and left[second] == right[first]
            )
            return "edit" if swapped else None
        return None
    shorter, longer = (left, right) if length_left < length_right else (right, left)
    for index in range(len(longer)):
        if shorter == longer[:index] + longer[index + 1 :]:
            return "edit"
    return None


def _within_one_edit(left: str, right: str) -> bool:
    return _edit_kind(left, right) is not None


# --------------------------------------------------------------------------------
# Domains
# --------------------------------------------------------------------------------

#: Which canonical draft field is waiting on which kind of value. The field names are
#: the ones the pending workflow already stores, so there is no second naming scheme.
_DOMAIN_BY_FIELD: Final[dict[str, AnswerDomain]] = {
    "universe": AnswerDomain.SYMBOL_SCOPE,
    "trigger_timeframe": AnswerDomain.TIMEFRAME,
    "reference_timeframe": AnswerDomain.TIMEFRAME,
    "measurement_window": AnswerDomain.TIMEFRAME,
    "reference_point": AnswerDomain.REFERENCE_POINT,
    "comparator": AnswerDomain.COMPARATOR,
    "operator": AnswerDomain.COMPARATOR,
    "movement_direction": AnswerDomain.MOVEMENT_DIRECTION,
    "movement_kind": AnswerDomain.MOVEMENT_DIRECTION,
    "threshold": AnswerDomain.PERCENT,
    "movement_size": AnswerDomain.PERCENT,
    "symbols": AnswerDomain.SYMBOL_SCOPE,
    "symbol_scope": AnswerDomain.SYMBOL_SCOPE,
    "sharia_policy.universe_mode": AnswerDomain.UNIVERSE_MODE,
    "universe_mode": AnswerDomain.UNIVERSE_MODE,
    "scan_window": AnswerDomain.SCAN_WINDOW,
}


#: Domains whose values are not a fixed list. A number and a symbol list are bounded
#: by the compiler and by governed screening, not by an enumeration here, so a real
#: reading of one is accepted rather than refused for "not being in the options".
_OPEN_ENDED: Final[frozenset[AnswerDomain]] = frozenset(
    {AnswerDomain.PERCENT, AnswerDomain.SYMBOL_SCOPE, AnswerDomain.FREE_TEXT}
)


def domain_for_field(field_name: str | None) -> AnswerDomain:
    """The kind of answer a field is waiting for. Unknown fields stay free text."""

    return _DOMAIN_BY_FIELD.get(str(field_name or ""), AnswerDomain.FREE_TEXT)


#: "Yes." Every wording of it, in every language the product answers in.
#:
#: This is the *only* list of these words. There were three: this module's scan-window
#: aliases, ``conversation_intent._WINDOW_24H`` and the social-turn vocabulary, and each
#: knew a different subset — ``تمام`` agreed to a scan window but not to a spelling
#: correction, ``correct`` agreed to neither. Every caller now imports this one.
AFFIRMATIVE_ANSWERS: Final[frozenset[str]] = frozenset(
    {
        "yes", "yeah", "yep", "yup", "ya", "yeh", "aye", "ok", "okay", "k",
        "sure", "correct", "right", "exactly", "true", "confirm", "confirmed",
        "use it", "that one", "thats it", "that is it", "go ahead", "do it",
        "please do", "of course", "definitely", "indeed", "affirmative",
        "sounds good", "that works", "fine", "good", "perfect", "agreed",
        "نعم", "أيوه", "ايوه", "اه", "آه", "تمام", "ماشي", "حاضر", "موافق",
        "صح", "بالظبط", "أكيد", "اكيد", "ايوا", "أيوا", "زي ما قلت", "هو ده",
        "oui", "ouais", "d'accord", "exactement", "c'est ça", "bien sûr",
        "parfait", "vas y", "allez y",
        "sí", "si", "claro", "vale", "exacto", "correcto", "por supuesto",
        "de acuerdo", "eso es", "perfecto",
        "да", "ага", "хорошо", "ладно", "конечно", "верно", "точно",
        "именно", "согласен", "давай",
    }
)

#: "No." The mirror of the list above, and read by the same resolver.
NEGATIVE_ANSWERS: Final[frozenset[str]] = frozenset(
    {
        "no", "nope", "nah", "naw", "not that", "not this", "not it",
        "wrong", "incorrect", "thats wrong", "that is wrong", "negative",
        "not what i meant", "not what i said", "something else", "another one",
        "different", "neither", "false",
        "لا", "لأ", "مش ده", "مش دي", "مش كده", "غلط", "خطأ", "مش صح",
        "مش اللي قصدته", "حاجة تانية", "لا مش ده",
        "non", "pas ça", "pas celui là", "faux", "autre chose",
        "ce n'est pas ça",
        "eso no", "incorrecto", "falso", "otra cosa", "no es eso",
        "нет", "не то", "неверно", "неправильно", "другое", "не это",
    }
)


#: The shortest word a spelling guess may be made about. Below this, one edit is the
#: whole word: ``x`` sits one edit from ``k``, and ``no`` from ``ok``. A confirmation
#: *stores* a value the moment it reads a yes, so it cannot afford that — unlike the
#: option matching further down, which only ever asks.
_MINIMUM_TOLERANT_LENGTH: Final[int] = 3


def _confirmation_forms(words: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """The forms of one yes/no vocabulary that may be matched exactly, and loosely.

    Every wording can be matched exactly, in both its filler-stripped and its plain
    collapsed spelling — ``yes please`` and ``yes`` are the same answer.

    Only a *single word* long enough to be worth guessing about may be matched loosely.
    Filler-stripping turns ``that one`` into ``that`` and ``use it`` into ``it``, and a
    one-edit guess against those stems read ``what`` as a yes.
    """

    exact: set[str] = set()
    tolerant: set[str] = set()
    for word in words:
        stripped = normalize_answer_text(word)
        plain = " ".join(
            unicodedata.normalize("NFKC", word).casefold().translate(_ARABIC_FOLD).split()
        )
        exact.update(item for item in (stripped, plain) if item)
        if (
            len(word.split()) == 1
            and len(stripped) >= _MINIMUM_TOLERANT_LENGTH
            and " " not in stripped
        ):
            tolerant.add(stripped)
    return frozenset(exact), frozenset(tolerant)


_AFFIRMATIVE_EXACT, _AFFIRMATIVE_TOLERANT = _confirmation_forms(AFFIRMATIVE_ANSWERS)
_NEGATIVE_EXACT, _NEGATIVE_TOLERANT = _confirmation_forms(NEGATIVE_ANSWERS)


def resolve_confirmation(message: str) -> ConfirmationReply:
    """Read one message as yes, no, or neither. The single boolean resolver.

    Tolerant in exactly the way the rest of this module is — the same normalisation, at
    most one typing mistake — and stricter in one way, on purpose. A yes here *stores* a
    value, so a guess is only made about a single word of three letters or more.
    ``yse`` is yes. ``x`` is not, and neither is ``what``: both sit one edit from
    something, and neither is a trader agreeing to anything.
    """

    normalized = normalize_answer_text(message)
    if not normalized:
        return ConfirmationReply.UNCLEAR
    if normalized in _AFFIRMATIVE_EXACT:
        # An exact match wins outright. Some words sit one edit from a word in the
        # other list, and a spelling guess must never flip yes into no.
        return ConfirmationReply.AFFIRMATIVE
    if normalized in _NEGATIVE_EXACT:
        return ConfirmationReply.NEGATIVE
    if " " in normalized or len(normalized) < _MINIMUM_TOLERANT_LENGTH:
        # A whole sentence is not a misspelled "yes", and a one- or two-letter answer
        # carries no spare information to correct against.
        return ConfirmationReply.UNCLEAR
    near_yes = any(_within_one_edit(normalized, word) for word in _AFFIRMATIVE_TOLERANT)
    near_no = any(_within_one_edit(normalized, word) for word in _NEGATIVE_TOLERANT)
    if near_yes and not near_no:
        return ConfirmationReply.AFFIRMATIVE
    if near_no and not near_yes:
        return ConfirmationReply.NEGATIVE
    return ConfirmationReply.UNCLEAR


#: What each canonical value is called, in every language the product answers in. The
#: labels a button shows come from here too, so a typed answer and a click cannot
#: disagree about what the option means.
_ALIASES: Final[dict[AnswerDomain, dict[str, tuple[str, ...]]]] = {
    AnswerDomain.UNIVERSE_MODE: {
        "eligible_market": (
            "all", "all coins", "all eligible coins", "all eligible spot assets",
            "all screened coins", "all screened assets", "everything",
            "the whole market", "whole market", "entire market", "any coin",
            "all markets", "all assets", "all of them", "eligible market",
            "كل العملات", "كل العملات المفحوصة", "كل الأصول المؤهلة", "السوق كله",
            "الكل", "أي عملة", "كل حاجة",
            "toutes les cryptos", "tous les actifs éligibles", "tout le marché",
            "toutes", "tout",
            "todas las monedas", "todos los activos elegibles", "todo el mercado",
            "todas", "todo",
            "все монеты", "все подходящие активы", "весь рынок", "все",
        ),
        "approved_watchlist": (
            "my favorites", "my favourites", "favorites", "favourites",
            "my list", "watchlist", "my watchlist", "saved list",
            "المفضلة", "قائمتي", "المفضلات",
            "mes favoris", "favoris", "ma liste",
            "mis favoritos", "favoritos", "mi lista",
            "избранное", "мой список",
        ),
        "explicit_assets": (
            "specific eligible assets", "specific assets", "specific coins",
            "certain coins", "named coins", "these coins", "pick coins",
            "just some coins", "explicit assets", "some coins",
            "عملات محددة", "أصول محددة", "عملات معينة",
            "actifs spécifiques", "cryptos précises", "certaines cryptos",
            "activos específicos", "monedas concretas", "ciertas monedas",
            "конкретные монеты", "определённые активы",
        ),
    },
    AnswerDomain.REFERENCE_POINT: {
        "candle_open": (
            "candle open", "open", "opening", "the open", "candle opening",
            "from the open", "open of the candle", "candles open",
            "افتتاح الشمعة", "الافتتاح", "افتتاح",
            "ouverture de la bougie", "ouverture", "l'ouverture",
            "apertura de la vela", "apertura",
            "открытие свечи", "открытие",
        ),
        "previous_close": (
            "previous close", "prior close", "last close", "previous candle close",
            "close", "closing", "previous candles close", "the previous close",
            "إغلاق الشمعة السابقة", "الإغلاق السابق", "الإغلاق", "إغلاق",
            "clôture précédente", "clôture de la bougie précédente", "clôture",
            "cierre anterior", "cierre de la vela anterior", "cierre",
            "предыдущее закрытие", "закрытие предыдущей свечи", "закрытие",
        ),
    },
    AnswerDomain.COMPARATOR: {
        Comparator.GREATER_THAN_OR_EQUAL.value: (
            "reaches", "at the threshold", "or beyond", "or more", "at least",
            "when it reaches", "reaching", "equal or more", "gte",
            "عند الحد", "أو أكثر", "على الأقل", "لما توصل",
            "au seuil", "ou au-delà", "au moins", "atteint",
            "en el umbral", "o más", "al menos", "alcanza",
            "на пороге", "или выше", "не менее", "достигает",
        ),
        Comparator.GREATER_THAN.value: (
            "passes", "only beyond", "above", "more than", "strictly above",
            "after it passes", "exceeds", "gt",
            "تجاوز", "فوق", "أكثر من", "بعد التجاوز",
            "au-delà", "dépasse", "plus que", "strictement au-dessus",
            "por encima", "supera", "más que",
            "только выше", "превышает", "больше чем",
        ),
    },
    AnswerDomain.MOVEMENT_DIRECTION: {
        "up": (
            "a rise", "rise", "up", "rises", "increase", "gain", "higher", "rising",
            "صعود", "طلوع", "فوق", "ارتفاع",
            "une hausse", "hausse", "monte",
            "una subida", "subida", "sube",
            "рост", "вверх", "повышение",
        ),
        "down": (
            "a fall", "fall", "down", "falls", "decrease", "drop", "lower", "falling",
            "هبوط", "نزول", "تحت", "انخفاض",
            "une baisse", "baisse", "chute",
            "una bajada", "bajada", "baja",
            "падение", "вниз", "снижение",
        ),
        "neutral": (
            "both", "either", "any direction", "up or down",
            "الاتنين", "الاثنين", "كلاهما",
            "les deux", "ambas", "ambos", "оба", "любое",
        ),
    },
    AnswerDomain.SYMBOL_SCOPE: {
        "all_screened": (
            "all", "all coins", "all screened coins", "every coin", "everything",
            "the whole market", "any coin", "all of them",
            "كل العملات", "كل العملات المفحوصة", "الكل", "أي عملة",
            "toutes les cryptos", "toutes", "tout le marché",
            "todas las monedas", "todas", "todo el mercado",
            "все монеты", "все", "весь рынок",
        ),
    },
    AnswerDomain.SCAN_WINDOW: {
        # The provider exposes one window, so agreeing to the question *is* choosing it.
        # The agreement words come from the shared list rather than a private copy —
        # that copy is what made ``correct`` an answer here and nowhere else.
        "24h": (
            "24h", "24 hours", "24 hour", "last 24 hours", "past 24 hours",
            "rolling 24 hours", "a day", "one day", "today",
            "24 ساعة", "اخر 24 ساعة", "يوم",
            "24 heures", "dernières 24 heures", "un jour",
            "24 horas", "últimas 24 horas", "un día",
            "24 часа", "последние 24 часа", "сутки",
            *sorted(AFFIRMATIVE_ANSWERS),
        ),
    },
}

#: Answers that end the question instead of answering it.
_CANCEL: Final[frozenset[str]] = frozenset(
    {
        "cancel", "stop", "forget it", "never mind", "nevermind", "quit", "abort",
        "drop it", "leave it", "start over", "reset", "no thanks", "not now",
        "إلغاء", "الغاء", "بطل", "سيبك", "مش عايز", "خلاص",
        "annuler", "arrête", "laisse tomber", "oublie",
        "cancelar", "para", "olvídalo", "déjalo",
        "отмена", "отменить", "стоп", "забудь",
    }
)

#: Asking to pick up a question that was put down. A paused platform requirement is
#: still required, so there has to be a way back to it that a beginner would guess.
RESUME_REQUESTS: Final[frozenset[str]] = frozenset(
    {
        "continue", "resume", "carry on", "go on", "keep going", "finish it",
        "finish setup", "lets continue", "back to it", "where were we",
        "كمل", "أكمل", "نكمل", "استمر", "رجعنا", "نرجع",
        "continuer", "reprendre", "on continue",
        "continuar", "seguir", "sigamos", "retomar",
        "продолжить", "продолжим", "дальше",
    }
)


def is_resume_request(message: str) -> bool:
    """Whether this turn asks to pick a paused question back up.

    Whole-message only. "Continue" on its own is a request to resume; the same word
    inside a sentence that also states a rule is part of that rule, and reading it as a
    resume would throw the rule away.
    """

    normalized = normalize_answer_text(message)
    if not normalized:
        return False
    return normalized in {normalize_answer_text(word) for word in RESUME_REQUESTS}


def _canonical_values(domain: AnswerDomain, allowed: Sequence[str]) -> tuple[str, ...]:
    """Which values may be answered, with the caller's governed list winning.

    A domain proposes; the capability or runtime contract the caller passes in
    disposes.  This is why timeframes are not listed anywhere in this module: they
    come from the canonical registry, narrowed only by an explicit restriction.
    """

    if allowed:
        return tuple(dict.fromkeys(str(item) for item in allowed))
    if domain is AnswerDomain.TIMEFRAME:
        return tuple(ORDERED_TIMEFRAMES)
    return tuple(_ALIASES.get(domain, {}))


def canonical_values(domain: AnswerDomain, allowed: Sequence[str] = ()) -> tuple[str, ...]:
    """Every value this question can actually execute."""

    return _canonical_values(domain, allowed)


#: The shortlist each question puts on screen. Never a restriction on what may be
#: *answered* — a trader may still type any canonical value — only on what is shown,
#: because twelve buttons is not a question a beginner can read. Each entry is proved
#: to be a subset of its domain's canonical values by an invariant test.
_OFFERED: Final[dict[AnswerDomain, tuple[str, ...]]] = {
    AnswerDomain.TIMEFRAME: COMMON_TIMEFRAMES,
    AnswerDomain.REFERENCE_POINT: ("candle_open", "previous_close"),
    AnswerDomain.COMPARATOR: (
        Comparator.GREATER_THAN_OR_EQUAL.value,
        Comparator.GREATER_THAN.value,
    ),
    AnswerDomain.MOVEMENT_DIRECTION: ("up", "down", "neutral"),
    AnswerDomain.UNIVERSE_MODE: (
        "eligible_market",
        "approved_watchlist",
        "explicit_assets",
    ),
    AnswerDomain.SCAN_WINDOW: ("24h",),
}


def display_options(domain: AnswerDomain, allowed: Sequence[str] = ()) -> tuple[str, ...]:
    """The values a caller may show as buttons — never more than it can execute.

    Anything the governed ``allowed`` list excludes is dropped from the shortlist, so a
    displayed option is always executable.  That is the whole point: a button the
    backend cannot honour is a promise the product then breaks.
    """

    executable = set(_canonical_values(domain, allowed))
    shortlist = tuple(item for item in _OFFERED.get(domain, ()) if item in executable)
    return shortlist or tuple(sorted(executable))[:6]


#: Where each domain's button wording lives. The words stay in the language module;
#: this only says which catalogue belongs to which domain, so a value and its label
#: can never be paired by list position again.
_LABEL_FIELD: Final[dict[AnswerDomain, str]] = {
    AnswerDomain.REFERENCE_POINT: "reference_point",
    AnswerDomain.COMPARATOR: "comparator",
    AnswerDomain.TIMEFRAME: "trigger_timeframe",
}


def labels_for(
    domain: AnswerDomain,
    values: Sequence[str],
    language: ConversationLanguage,
) -> dict[str, str]:
    """Canonical value → the words to show for it, in ``language``.

    A value with no translated wording shows its own canonical form rather than being
    dropped: a missing translation must never remove a choice the trader has.
    """

    if domain is AnswerDomain.UNIVERSE_MODE:
        catalogue = scope_labels(language)
    else:
        catalogue = option_label_map(_LABEL_FIELD.get(domain, ""), language)
    return {value: catalogue.get(value, value) for value in values}


def _alias_index(
    domain: AnswerDomain,
    canonical: Iterable[str],
    labels: Mapping[str, str],
) -> dict[str, set[str]]:
    """Every written form → the canonical values it could mean."""

    index: dict[str, set[str]] = {}

    def record(written: str, value: str) -> None:
        for variant in _variants(written):
            index.setdefault(variant, set()).add(value)

    for value in canonical:
        record(value, value)
        for alias in _ALIASES.get(domain, {}).get(value, ()):
            record(alias, value)
        # A displayed label always answers its own option, even when no alias lists
        # it. Typing what the button says can never be a worse answer than clicking.
        label = labels.get(value)
        if label:
            record(label, value)
    return index


def _domain_reader(domain: AnswerDomain, text: str) -> str | float | None:
    """Ask the module that already owns this concept, rather than re-reading it."""

    if domain is AnswerDomain.TIMEFRAME:
        return normalize_timeframe_alias(text)
    if domain is AnswerDomain.COMPARATOR:
        comparator = detect_comparator(text)
        return comparator.value if comparator is not None else None
    if domain is AnswerDomain.MOVEMENT_DIRECTION:
        direction = movement_direction(text)
        return str(direction) if direction is not None else None
    if domain is AnswerDomain.PERCENT:
        # Exactly one number in the whole answer, or nothing. "between 3 and 9 percent"
        # names a range this reader cannot resolve, and taking the number that happens
        # to carry the percent sign is how a stated value becomes an invented one.
        numbers = re.findall(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)(?![\w.])", text)
        if len(numbers) != 1:
            return None
        return abs(float(numbers[0]))
    if domain is AnswerDomain.SYMBOL_SCOPE:
        symbols = extract_symbols(text)
        return ", ".join(symbols) if symbols else None
    return None


def resolve_active_answer(
    message: str,
    *,
    domain: AnswerDomain,
    allowed_options: Sequence[str] = (),
    offered_values: Sequence[str] = (),
    display_labels: Mapping[str, str] | None = None,
    looks_like_new_request: bool = False,
    proposed_value: str | None = None,
) -> AnswerResolution:
    """Read one message as an answer to the one question that is open.

    ``allowed_options`` is the governed list of canonical values.  When it is empty the
    domain's own list is used, and for timeframes that means the canonical registry —
    so a capability that really can run on one-minute candles can be answered with
    ``1 minute`` without anybody editing a second list.

    ``offered_values`` is the shorter list actually shown as buttons this turn.  It
    never narrows what may be *answered*; it only breaks a tie between near misses,
    because a trader looking at three buttons who mistypes almost certainly meant one
    of the three.

    ``proposed_value`` is a near miss already put to the trader as *did you mean X?*.
    While it is set the question on screen is a yes/no, and that is what this message is
    read against first.  The value is still never stored without a yes.

    The result is always typed.  A caller that receives anything other than
    ``RESOLVED`` must leave the question, the accepted values and the workflow exactly
    as they were, and must store ``proposed_value`` from the result — including when it
    comes back ``None``, which is how a rejected or superseded proposal is cleared.
    """

    if proposed_value is not None:
        return _resolve_confirmation_turn(
            message,
            domain=domain,
            allowed_options=allowed_options,
            offered_values=offered_values,
            display_labels=display_labels,
            looks_like_new_request=looks_like_new_request,
            proposed_value=proposed_value,
        )
    return _resolve_domain_answer(
        message,
        domain=domain,
        allowed_options=allowed_options,
        offered_values=offered_values,
        display_labels=display_labels,
        looks_like_new_request=looks_like_new_request,
    )


def _resolve_confirmation_turn(
    message: str,
    *,
    domain: AnswerDomain,
    allowed_options: Sequence[str],
    offered_values: Sequence[str],
    display_labels: Mapping[str, str] | None,
    looks_like_new_request: bool,
    proposed_value: str,
) -> AnswerResolution:
    """Read one message against an open *did you mean X?*.

    Three readings, in this order, and the order is the whole design:

    1. a stop word ends the question, as it does anywhere else;
    2. yes or no decides the proposal;
    3. anything else is tried as a fresh answer to the original question, because a
       trader who typed ``qh`` and then ``4h`` has corrected themselves and should not
       have to say "no" first.

    A message none of those can read keeps the proposal and asks for yes or no again.
    It is never handed to the planner: a reply to our own yes/no question is not a new
    request, however oddly it is worded.
    """

    raw = " ".join((message or "").split())
    normalized = normalize_answer_text(raw)
    if normalized and normalized in {normalize_answer_text(word) for word in _CANCEL}:
        return AnswerResolution(
            AnswerOutcome.CANCELLED,
            reason="explicit cancellation",
            stage=AnswerStage.CONFIRMATION,
        )
    confirmation = resolve_confirmation(raw)
    if confirmation is ConfirmationReply.AFFIRMATIVE:
        return AnswerResolution(
            AnswerOutcome.RESOLVED,
            canonical_value=proposed_value,
            reason="the trader confirmed the proposed value",
            stage=AnswerStage.CONFIRMATION,
        )
    if confirmation is ConfirmationReply.NEGATIVE:
        # Rejected, not restarted. The proposal goes; every value already accepted, the
        # workflow and this same question all stay exactly as they were.
        return AnswerResolution(
            AnswerOutcome.AMBIGUOUS,
            candidates=tuple(offered_values) or tuple(allowed_options),
            reason="the trader rejected the proposed value",
            stage=AnswerStage.CONFIRMATION,
        )
    fresh = _resolve_domain_answer(
        raw,
        domain=domain,
        allowed_options=allowed_options,
        offered_values=offered_values,
        display_labels=display_labels,
        looks_like_new_request=looks_like_new_request,
    )
    if fresh.outcome in {
        AnswerOutcome.RESOLVED,
        AnswerOutcome.CONFIRM_CANDIDATE,
        AnswerOutcome.CANCELLED,
        AnswerOutcome.NEW_REQUEST,
    }:
        # A real reading supersedes the proposal, so the old one is dropped rather than
        # left behind to be confirmed by a later, unrelated "yes".
        return fresh
    return AnswerResolution(
        AnswerOutcome.CONFIRM_CANDIDATE,
        canonical_value=proposed_value,
        reason="neither yes nor no, and no other reading; ask the same yes/no again",
        stage=AnswerStage.CONFIRMATION,
        proposed_value=proposed_value,
    )


def _resolve_domain_answer(
    message: str,
    *,
    domain: AnswerDomain,
    allowed_options: Sequence[str] = (),
    offered_values: Sequence[str] = (),
    display_labels: Mapping[str, str] | None = None,
    looks_like_new_request: bool = False,
) -> AnswerResolution:
    """Read one message as a value from ``domain``. No confirmation state involved."""

    raw = " ".join((message or "").split())
    if not raw:
        return AnswerResolution(AnswerOutcome.AMBIGUOUS, reason="empty message")

    normalized = normalize_answer_text(raw)
    if normalized in {normalize_answer_text(word) for word in _CANCEL}:
        return AnswerResolution(AnswerOutcome.CANCELLED, reason="explicit cancellation")

    canonical = _canonical_values(domain, allowed_options)
    index = _alias_index(domain, canonical, display_labels or {})

    exact = index.get(normalized)
    if exact and len(exact) == 1:
        return AnswerResolution(AnswerOutcome.RESOLVED, canonical_value=next(iter(exact)))
    if exact and len(exact) > 1:
        return AnswerResolution(
            AnswerOutcome.AMBIGUOUS,
            candidates=tuple(sorted(exact)),
            reason="one written form, several canonical meanings",
            plausible_readings=True,
        )

    read = _domain_reader(domain, raw)
    if read is not None:
        if domain in _OPEN_ENDED or not canonical or str(read) in canonical:
            return AnswerResolution(AnswerOutcome.RESOLVED, canonical_value=read)
        # A real, well-formed value this question is not allowed to take. That is a
        # boundary, not a misunderstanding, and it deserves an explanation.
        return AnswerResolution(
            AnswerOutcome.UNSUPPORTED,
            candidates=tuple(canonical),
            reason=f"{read} is outside the governed options for this question",
        )

    if domain is AnswerDomain.FREE_TEXT:
        return AnswerResolution(AnswerOutcome.RESOLVED, canonical_value=raw)

    near: dict[str, set[str]] = {"neighbour": set(), "edit": set()}
    for written, values in index.items():
        kind = _edit_kind(normalized, written)
        if kind in near:
            near[kind].update(values)
    shown = {str(item) for item in offered_values}
    # Two things narrow a near miss, in this order: a key struck beside the intended
    # one, and an option that was actually on screen. Only when one answer is left
    # standing is a confirmation offered; otherwise the options are simply repeated.
    tiers: tuple[tuple[str, set[str]], ...] = (
        ("neighbour", near["neighbour"] & shown),
        ("neighbour", near["neighbour"]),
        ("edit", near["edit"] & shown),
        ("edit", near["edit"]),
    )
    for label, matches in tiers:
        if len(matches) == 1:
            candidate = next(iter(matches))
            return AnswerResolution(
                AnswerOutcome.CONFIRM_CANDIDATE,
                canonical_value=candidate,
                reason=f"one {label} match; confirm before storing",
                proposed_value=candidate,
            )
    for label, matches in tiers:
        if matches:
            return AnswerResolution(
                AnswerOutcome.AMBIGUOUS,
                candidates=tuple(sorted(matches)),
                reason=f"several {label} matches",
                plausible_readings=True,
            )

    if looks_like_new_request:
        return AnswerResolution(
            AnswerOutcome.NEW_REQUEST, reason="a complete request, not an answer"
        )
    return AnswerResolution(
        AnswerOutcome.AMBIGUOUS,
        candidates=tuple(canonical),
        reason="no reading of this message answers the open question",
    )
