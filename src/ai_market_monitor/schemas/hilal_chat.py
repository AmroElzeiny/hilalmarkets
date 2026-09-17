"""What Hilal is allowed to say back, and what a person is allowed to send.

The reply is a structured object rather than free text, for two reasons that are both
rules in `docs/dashboard-test-hilal-chat-rules.md`:

* **J2 — never show code.** The model writes into a ``reply`` field, and that field is
  checked here for the shapes that leak machinery into a customer's face: fenced code,
  a JSON object, an internal key. A model told "do not show JSON" in a prompt will
  still do it occasionally; a validator will not.
* **B2/B3 — it refuses strategy and financial advice.** ``mode`` makes the refusal an
  explicit, countable decision rather than something to be inferred later by reading
  the words. Nothing downstream has to guess whether an answer was a refusal.
"""

import re
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: What kind of turn this was.
#:
#: ``REFUSAL`` covers the two things Hilal must never do — decide what somebody should
#: trade, and judge or author a strategy for them. ``OUT_OF_SCOPE`` is different and
#: must stay different: it is an ordinary question about something outside Hilal
#: Markets, and answering it with a safety refusal would tell a person they had asked
#: for something dangerous when they had only asked about the weather.
#:
#: ``GUIDE`` is help using the product itself — where a button is, what a card means,
#: what the board is still missing. It is deliberately its own mode rather than a kind
#: of ``ANSWER``: showing somebody how to work the canvas they are already looking at
#: is a different promise from telling them a fact off a record, and only ``GUIDE``
#: carries the reminder that this help is new and can be wrong.
HilalChatMode = Literal[
    "GREETING",
    "ANSWER",
    "GUIDE",
    "CLARIFY",
    "REFUSAL",
    "OUT_OF_SCOPE",
]

#: Every declared mode, as a set, derived from the type above.
#:
#: Written out by hand once, in the service, where it decided whether a stored answer
#: could be redrawn. Two lists of the same modes is the duplication this codebase keeps
#: paying for: adding ``GUIDE`` to one and not the other would have quietly turned every
#: stored guidance answer into a plain one on reload.
HILAL_CHAT_MODES: frozenset[str] = frozenset(get_args(HilalChatMode))

#: Why an answer was reported. Kept short and closed: a free-text reason nobody reads is
#: worse than four buttons somebody does.
HilalChatReportReason = Literal[
    "wrong",
    "confusing",
    "not_allowed",
    "other",
]

#: Fenced code, an HTML tag, or a bare JSON object. Any of them means machinery reached
#: the person, which rule J2 forbids outright.
_CODE_SHAPES = (
    re.compile(r"```"),
    re.compile(r"<[a-zA-Z/][^>]*>"),
    re.compile(r"\{\s*\"[^\"]+\"\s*:"),
)

#: Words that only exist inside this codebase. If one reaches a reply, the model is
#: reading its evidence out loud instead of explaining it.
_INTERNAL_WORDS = re.compile(
    r"\b("
    r"canonical_asset|methodology_id|assessment_id|user_id|conversation_id"
    r"|supported_comparators|default_comparator|mechanic_key|capability_key"
    r"|json|null|undefined|true_false"
    r")\b",
    re.IGNORECASE,
)


class HilalChatReply(BaseModel):
    """One answer from Hilal, as the model is required to shape it."""

    model_config = ConfigDict(extra="forbid")

    mode: HilalChatMode
    #: The words a person reads. Short on purpose — rule J1.
    reply: str = Field(min_length=1, max_length=1200)
    #: The language the person wrote in, as the model read it. Plain name, not a code,
    #: because it is shown to a person if it is shown at all.
    language: str = Field(min_length=1, max_length=40)
    #: Up to three short things they might ask next. Offered, never automatic.
    suggestions: list[str] = Field(default_factory=list, max_length=3)
    #: Which supplied evidence rows the answer rests on. Empty is legitimate for a
    #: greeting or a refusal; it is not legitimate for a claim about a coin, and the
    #: service checks that separately.
    grounded_in: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("reply")
    @classmethod
    def reply_is_written_for_a_person(cls, value: str) -> str:
        """No code, no JSON, no internal field names. Ever.

        Raising rather than stripping is deliberate. A reply with the code cut out of
        it is a reply with a hole in it, and the sentence around the hole was written
        to lead into it. Refusing sends the turn down the retry path, and a second
        failure surfaces as an honest "I could not answer that" instead of a mangled
        one.
        """

        for shape in _CODE_SHAPES:
            if shape.search(value):
                raise ValueError("the answer contained code or markup")
        found = _INTERNAL_WORDS.search(value)
        if found:
            raise ValueError(f"the answer used the internal word {found.group(0)!r}")
        return value.strip()

    @field_validator("suggestions")
    @classmethod
    def suggestions_are_short_questions(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            if not text:
                continue
            if len(text) > 80:
                raise ValueError("a follow-up suggestion is too long to be a button")
            cleaned.append(text)
        return cleaned


#: Length caps for what a page tells Hilal about itself, and what the canvas tells
#: Hilal about the person's own fields. The schema is the one owner: the browser
#: truncates to these same numbers before sending, and
#: ``tests/unit/test_invariant_hilal_page_context.py`` fails if the two drift apart.
HILAL_PAGE_HEADING_MAX = 120
HILAL_PAGE_SUMMARY_MAX = 500
HILAL_PAGE_POINTS_MAX = 8
HILAL_PAGE_POINT_MAX = 160
HILAL_CARD_INPUTS_MAX = 6
HILAL_CARD_INPUT_LABEL_MAX = 80
HILAL_CARD_INPUT_VALUE_MAX = 120

#: Every dashboard page that shows Hilal, by the name it publishes under. Closed on
#: purpose: a name that is not here is refused, so a page describing itself in a new
#: way cannot silently slip past.
HILAL_PAGE_NAMES: tuple[str, ...] = (
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
)


def _clip_text(value: str | None, limit: int) -> str | None:
    """Shorten an over-long diagnostic, never refuse it.

    A page description that is too long is a diagnostic that grew, not a person who
    did something wrong. Raising here would turn the description into the failure of
    the whole turn — the class of bug this codebase names in its compiler rules.
    """

    if value is None:
        return None
    return value[:limit] if len(value) > limit else value


def _clip_raw_text(value: object, limit: int) -> object:
    """Truncate raw input before validation, so length caps never raise.

    Runs ``mode="before"``: pydantic checks ``max_length`` after these validators,
    so an ``after`` validator would never see an over-long value — the cap would
    have already refused it. Non-strings pass through untouched, so mistyped values
    are still refused as mistyped rather than silently reshaped.
    """

    if value is None or not isinstance(value, str):
        return value
    return value[:limit] if len(value) > limit else value


def _clip_list(value: list[str], *, count: int, each: int) -> list[str]:
    kept = value[:count] if len(value) > count else value
    return [item[:each] if len(item) > each else item for item in kept]


def _clip_raw_list(value: object, *, count: int, each: int) -> object:
    """Truncate a raw list before validation: keep the first ``count`` strings,
    each cut to ``each`` characters. Non-lists pass through for normal validation."""

    if not isinstance(value, list):
        return value
    kept = value[:count] if len(value) > count else value
    return [item[:each] if isinstance(item, str) and len(item) > each else item for item in kept]


class HilalChatCardInput(BaseModel):
    """One field on the person's own card, exactly as they left it."""

    model_config = ConfigDict(extra="forbid")

    #: The field's own name, as the form prints it.
    label: str = Field(min_length=1, max_length=HILAL_CARD_INPUT_LABEL_MAX)
    #: Whether the person has put anything in it.
    filled: bool = False
    #: What they typed, untouched and unjudged. ``None`` when it is still empty.
    value: str | None = Field(default=None, max_length=HILAL_CARD_INPUT_VALUE_MAX)

    @field_validator("label", mode="before")
    @classmethod
    def _truncate_label(cls, value: object) -> object:
        return _clip_raw_text(value, HILAL_CARD_INPUT_LABEL_MAX)

    @field_validator("value", mode="before")
    @classmethod
    def _truncate_value(cls, value: object) -> object:
        return _clip_raw_text(value, HILAL_CARD_INPUT_VALUE_MAX)


class HilalChatBoardCard(BaseModel):
    """One card as it sits on the person's own canvas, right now."""

    model_config = ConfigDict(extra="forbid")

    #: The card's own name, as the page prints it.
    label: str = Field(min_length=1, max_length=80)
    #: The plain sentence the card currently reads as, if it has enough set to read.
    reads: str | None = Field(default=None, max_length=160)
    #: Whether this one has to be true, or only adds to the score.
    required: bool = True
    #: The group it sits in, in the words the page uses ("all of these").
    inside: str | None = Field(default=None, max_length=48)
    #: Set aside means on the board but not part of the monitor.
    set_aside: bool = False
    #: The fields the person has not filled in yet, named as the form names them.
    needs: list[str] = Field(default_factory=list, max_length=6)
    #: Every field on this card, filled or not, with the value as the person typed
    #: it. ``needs`` names what is missing; this shows what is already there, so
    #: Hilal can repeat the person's own values back as theirs — and never recommend
    #: one.
    inputs: list[HilalChatCardInput] = Field(
        default_factory=list, max_length=HILAL_CARD_INPUTS_MAX
    )

    @field_validator("label", mode="before")
    @classmethod
    def _truncate_card_label(cls, value: object) -> object:
        return _clip_raw_text(value, 80)

    @field_validator("reads", mode="before")
    @classmethod
    def _truncate_card_reads(cls, value: object) -> object:
        return _clip_raw_text(value, 160)

    @field_validator("inside", mode="before")
    @classmethod
    def _truncate_card_inside(cls, value: object) -> object:
        return _clip_raw_text(value, 48)

    @field_validator("needs", mode="before")
    @classmethod
    def _truncate_card_needs(cls, value: object) -> object:
        return _clip_raw_list(value, count=6, each=80)

    @field_validator("inputs", mode="before")
    @classmethod
    def _truncate_card_inputs(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return value[:HILAL_CARD_INPUTS_MAX] if len(value) > HILAL_CARD_INPUTS_MAX else value


class HilalChatPageContext(BaseModel):
    """One dashboard page, in its own words.

    Published by the page itself through ``publish(name, describe)`` in
    ``static/hm-page-context.js`` — the heading it prints, a short summary of what
    it shows, and the names of its parts. Read, never interpreted: Hilal uses it as
    background for "what is this page" questions, never as a source of facts about
    coins or standards.
    """

    model_config = ConfigDict(extra="forbid")

    #: The page's own heading, as printed.
    heading: str | None = Field(default=None, max_length=HILAL_PAGE_HEADING_MAX)
    #: What the page shows, in its own words.
    summary: str | None = Field(default=None, max_length=HILAL_PAGE_SUMMARY_MAX)
    #: The names of its parts, in its own words.
    points: list[str] = Field(default_factory=list, max_length=HILAL_PAGE_POINTS_MAX)

    @field_validator("heading", mode="before")
    @classmethod
    def _truncate_heading(cls, value: object) -> object:
        return _clip_raw_text(value, HILAL_PAGE_HEADING_MAX)

    @field_validator("summary", mode="before")
    @classmethod
    def _truncate_summary(cls, value: object) -> object:
        return _clip_raw_text(value, HILAL_PAGE_SUMMARY_MAX)

    @field_validator("points", mode="before")
    @classmethod
    def _truncate_points(cls, value: object) -> object:
        return _clip_raw_list(value, count=HILAL_PAGE_POINTS_MAX, each=HILAL_PAGE_POINT_MAX)


class HilalChatBoardCheck(BaseModel):
    """One line from the page's own checklist, copied rather than re-derived."""

    model_config = ConfigDict(extra="forbid")

    tone: Literal["pass", "warn", "stop"]
    text: str = Field(min_length=1, max_length=240)

    @field_validator("text", mode="before")
    @classmethod
    def _truncate_check_text(cls, value: object) -> object:
        return _clip_raw_text(value, 240)


class HilalChatBoard(BaseModel):
    """The monitor the person is drawing, as their own page describes it.

    Every field here is produced by the canvas's own readout — the same sentence, the
    same checks and the same card wording the person can already see. Nothing is
    re-derived from the draft, because a second opinion about what a card means is
    exactly how two parts of this product end up disagreeing in front of a customer.
    """

    model_config = ConfigDict(extra="forbid")

    #: The whole monitor as the page's one-sentence readout.
    sentence: str | None = Field(default=None, max_length=600)
    #: How far along the page says it is.
    ready_percent: int = Field(default=0, ge=0, le=100)
    cards: list[HilalChatBoardCard] = Field(default_factory=list, max_length=32)
    checks: list[HilalChatBoardCheck] = Field(default_factory=list, max_length=32)
    #: What the monitor is pointed at, in the page's words.
    watching: str | None = Field(default=None, max_length=120)
    #: The ways to be told that are chosen, in the page's words.
    ways_to_be_told: list[str] = Field(default_factory=list, max_length=8)
    #: The names of the controls actually on screen, so guidance can name a real one.
    controls: list[str] = Field(default_factory=list, max_length=24)
    #: How the board is worked, in the words the page's own help prints. Guidance may
    #: repeat these and nothing else — a gesture the page does not document is one
    #: Hilal must not describe.
    how_to: list[str] = Field(default_factory=list, max_length=24)

    @field_validator("sentence", mode="before")
    @classmethod
    def _truncate_sentence(cls, value: object) -> object:
        return _clip_raw_text(value, 600)

    @field_validator("watching", mode="before")
    @classmethod
    def _truncate_watching(cls, value: object) -> object:
        return _clip_raw_text(value, 120)

    @field_validator("ways_to_be_told", mode="before")
    @classmethod
    def _truncate_ways(cls, value: object) -> object:
        return _clip_raw_list(value, count=8, each=60)

    @field_validator("controls", mode="before")
    @classmethod
    def _truncate_controls(cls, value: object) -> object:
        return _clip_raw_list(value, count=24, each=60)

    @field_validator("how_to", mode="before")
    @classmethod
    def _truncate_how_to(cls, value: object) -> object:
        return _clip_raw_list(value, count=24, each=160)

    @field_validator("cards", "checks", mode="before")
    @classmethod
    def _truncate_board_lists(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return value[:32] if len(value) > 32 else value


class HilalChatView(BaseModel):
    """What the person can see while they are asking.

    One owner. ``page`` and ``subject`` used to travel as two loose fields beside the
    message, which meant "what is on screen" had no single shape and nothing could be
    added to it without adding another loose field.
    """

    model_config = ConfigDict(extra="forbid")

    #: Which dashboard page.
    page: str | None = Field(default=None, max_length=120)
    #: Which part of that page is in front of them, by its own heading.
    section: str | None = Field(default=None, max_length=120)
    #: The coin or Passport open on the page, if it has one.
    subject: str | None = Field(default=None, max_length=64)
    #: The canvas, when they are on it.
    board: HilalChatBoard | None = None
    #: One page's own words, when they are on it. Each field below matches one name
    #: in ``HILAL_PAGE_NAMES`` — the closed list both sides publish and accept.
    screened_market: HilalChatPageContext | None = None
    opportunities: HilalChatPageContext | None = None
    watch_plans: HilalChatPageContext | None = None
    passport: HilalChatPageContext | None = None
    watchlist: HilalChatPageContext | None = None
    connections: HilalChatPageContext | None = None
    research: HilalChatPageContext | None = None
    settings: HilalChatPageContext | None = None
    support: HilalChatPageContext | None = None
    subscription: HilalChatPageContext | None = None
    report: HilalChatPageContext | None = None

    @field_validator("page", "section", mode="before")
    @classmethod
    def _truncate_where(cls, value: object) -> object:
        return _clip_raw_text(value, 120)

    @field_validator("subject", mode="before")
    @classmethod
    def _truncate_subject(cls, value: object) -> object:
        return _clip_raw_text(value, 64)


class HilalChatAsk(BaseModel):
    """One question from a person."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    #: What the person is looking at. Context for the answer; for facts about coins and
    #: standards it is never a source, and for their own draft it is the subject itself.
    view: HilalChatView | None = None
    #: The client's own id for this message, so a retry is not a second question.
    client_message_id: str | None = Field(default=None, max_length=120)


class HilalChatReport(BaseModel):
    """A person telling us an answer was wrong."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64)
    reason: HilalChatReportReason
    note: str | None = Field(default=None, max_length=2000)


class HilalChatRatingInput(BaseModel):
    """The stars and the comment asked for when the window is closed."""

    model_config = ConfigDict(extra="forbid")

    stars: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)
