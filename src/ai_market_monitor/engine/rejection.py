"""One rejection vocabulary, and one rule for what a rejection governs.

Before this module the word "not" was understood in exactly one place: the symbol
reader in :mod:`ai_market_monitor.engine.turn_fragments`, which kept its own
``_EXCLUSION_MARKERS`` tuple. Every other reader — timeframe, direction, exchange,
market type, comparator, threshold — had never heard of it. So ``don't use 15m``
set the timeframe to 15m and ``not short`` produced a short strategy: the readers
saw a value, and nothing told them the trader had refused it.

That is the duplicate-parser failure this repository keeps producing, in its worst
form: not two parsers that disagree, but one parser and several readers that never
learned the word at all. The fix is the usual one. One vocabulary lives here, every
reader imports it, and no caller writes its own list.

The resolution rule
-------------------

A rejection governs the value **nearest to its right**, and only when nothing but
filler separates the two. This mirrors the rule already used for operators and
numbers (see ``engine/numeric_clause.py``): the word that governs a value is the
one beside it, inside the clause that owns it.

Why "nothing but filler" and not "to the end of the clause": scanning to a clause
boundary over-refuses, and over-refusal is its own defect. ``don't alert me when
RSI is below 30`` rejects nothing — ``below`` is not filler, so the chain from
``don't`` to ``30`` is broken and the threshold lands, which is what the trader
meant. ``don't use 15m`` rejects 15m, because ``use`` is filler and the chain
holds. Both directions are covered by tests: refusing too little loses the
trader's meaning, refusing too much invents a refusal they never made.

What a caller does with a rejection depends on the field:

* the universe readers turn a rejected symbol into an **exclusion** — the trader
  named a destination for it
* every scalar reader simply **does not write** the value

Neither ever writes the refused value, which is the only invariant this module
exists to hold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Wording that refuses whatever follows it.
#:
#: Kept as one list, in four languages, because a trader who writes ``مش 15m``
#: has refused the timeframe exactly as plainly as one who writes ``not 15m``.
#: The Arabic, Egyptian-Arabic and Arabizi entries are not a translation
#: courtesy: without them the guard is switched off for most of the audience
#: this product is built for.
REJECTION_TERMS: tuple[str, ...] = (
    # English — plain negation
    "not",
    "no",
    "never",
    "none of",
    "dont",
    "don't",
    "do not",
    "doesnt",
    "doesn't",
    "does not",
    "didnt",
    "didn't",
    "did not",
    "wont",
    "won't",
    "will not",
    "cant",
    "can't",
    "cannot",
    "can not",
    "isnt",
    "isn't",
    "is not",
    "arent",
    "aren't",
    "are not",
    "no longer",
    "not anymore",
    "never include",
    "not include",
    # English — subtracting a value from a set
    "but not",
    "anything but",
    "everything but",
    "except",
    "except for",
    "other than",
    "apart from",
    "aside from",
    "besides",
    "minus",
    "without",
    "exclude",
    "excluding",
    "excluded",
    "omit",
    "omitting",
    "omitted",
    "ignore",
    "ignoring",
    "skip",
    "skipping",
    "drop",
    "dropping",
    "remove",
    "removing",
    "leave out",
    "keep out",
    "get rid of",
    "rid of",
    "stop using",
    "avoid",
    "forget",
    "cancel",
    # English — replacing one value with another. The refused value is the one
    # these introduce, which is why `use 1h instead of 15m` must keep 1h.
    "instead of",
    "rather than",
    "in place of",
    # Modern Standard Arabic
    "لا",  # la
    "ما",  # ma
    "ليس",  # laysa
    "بدون",  # bidoon - without
    "من غير",  # min gheir - without
    "غير",  # gheir - other than
    "ما عدا",  # ma 3ada - except
    "ماعدا",
    "عدا",
    "باستثناء",  # bistithnaa - except
    "استبعاد",  # istib3ad - exclusion
    "استبعد",
    "نستبعد",
    "ممنوع",  # mamnoo3 - forbidden
    "مرفوض",  # marfood - rejected
    "إلغاء",  # ilghaa - cancel
    "الغاء",
    "بدل من",  # badal min - instead of
    "بدلا من",
    "بدلاً من",
    # Egyptian Arabic
    "مش",  # mesh
    "مو",  # mu
    "بلاش",  # balash - don't
    "مفيش",  # mafeesh - there is no
    "مافيش",
    "مش عايز",  # mesh 3ayez - I don't want
    "مش عايزه",
    "مش عايزة",
    "مش عاوز",
    "مش عاوزه",
    "مش عاوزة",
    # Arabizi / Franco-Arabic. Digits are letters here, never thresholds.
    "la",
    "laa",
    "mesh",
    "msh",
    "mish",
    "mush",
    "mosh",
    "balash",
    "balaash",
    "mafeesh",
    "mafish",
    "mfeesh",
    "msh 3ayez",
    "mesh 3ayez",
    "mish 3ayez",
    "msh 3awez",
    "mesh 3awez",
    "msh 3ayza",
    "mesh 3ayza",
    "ma3ada",
    "ma 3ada",
    "men gheir",
    "min gheir",
    "men gher",
    "bedoon",
    "bidoon",
    "badal men",
    "badal min",
)

#: The subset of :data:`REJECTION_TERMS` strong enough to mean "keep this symbol
#: out of the universe" wherever it sits in the clause, rather than only when it
#: sits directly in front of the symbol.
#:
#: A universe rule is read across a whole clause, so the wording has to be
#: unambiguous over that distance. ``exclude BTCUSDT and ETHUSDT`` must keep both
#: out even though ``exclude`` touches only the first. Bare ``not``, ``no``,
#: ``la`` and ``ما`` are deliberately absent: read across a clause they would
#: exclude a symbol from ``watch BTCUSDT when RSI is not above 30 and ETHUSDT``,
#: which the trader never asked for. Those are handled by the adjacency rule in
#: :func:`rejects_following`, so nothing is lost — only the reckless reading is.
#:
#: A test asserts this stays a subset of :data:`REJECTION_TERMS`. Two lists that
#: drift apart is the failure this module was built to end.
UNIVERSE_EXCLUSION_TERMS: tuple[str, ...] = (
    "exclude",
    "excluding",
    "excluded",
    "never include",
    "not include",
    "never",
    # Bare ``no`` is **not** here, and must not be added. Read across a whole
    # clause it excludes the wrong symbol: ``Also yes/no: ETHUSDT only`` contains
    # the word ``no``, and treating that as a universe rule threw ETHUSDT out of
    # a watchlist the trader had just confirmed. ``no BTCUSDT`` still excludes,
    # through the adjacency rule in :func:`rejects_following`, which is the only
    # reading of ``no`` that is safe at any distance: none.
    "without",
    "omit",
    "omitting",
    "omitted",
    "ignore",
    "ignoring",
    "skip",
    "skipping",
    "drop",
    "dropping",
    "leave out",
    "keep out",
    "remove",
    "removing",
    "get rid of",
    "rid of",
    "stop using",
    "avoid",
    "but not",
    "anything but",
    "everything but",
    "except for",
    "except",
    "apart from",
    "other than",
    "aside from",
    "minus",
    "استبعاد",
    "نستبعد",
    "استبعد",
    "ما عدا",
    "ماعدا",
    "عدا",
    "باستثناء",
    "ممنوع",
    "مرفوض",
    "مش عايز",
    "مش عايزه",
    "مش عايزة",
    "مش عاوز",
    "بدون",
    "من غير",
    "مفيش",
    "مافيش",
    "بلاش",
    "balash",
    "balaash",
    "mafeesh",
    "mafish",
    "msh 3ayez",
    "mesh 3ayez",
    "mish 3ayez",
    "msh 3awez",
    "mesh 3awez",
    "ma3ada",
    "ma 3ada",
    "men gheir",
    "min gheir",
    "bedoon",
    "bidoon",
)

#: Wording that follows a symbol and excludes it (``BTCUSDT never included``).
#: Narrower than the prefix vocabulary on purpose: a trailing bare ``no`` is
#: ambiguous in a way a leading one is not.
TRAILING_EXCLUSION_TERMS: tuple[str, ...] = (
    "never included",
    "never include",
    "not included",
    "is excluded",
    "are excluded",
    "excluded",
    "must be excluded",
    "should be excluded",
    "must not appear",
    "must not be present",
    "must not be included",
    "removed",
    "omitted",
    "ignored",
    "left out",
    "ممنوع",
    "مستبعد",
    "مش عايز",
    "مش عايزه",
    "مش عايزة",
)

#: Words that may sit between a rejection and the value it refuses without
#: breaking the link. Deliberately closed and deliberately small.
#:
#: ``just`` and ``only`` are **not** filler and must never become filler:
#: ``not just BTCUSDT`` widens a universe, it does not exclude BTCUSDT.
#: Comparator words (``below``, ``above``, ``under``) are not filler either,
#: which is what keeps ``don't alert me below 30`` from refusing the 30.
REJECTION_FILLER_TERMS: tuple[str, ...] = (
    # articles, prepositions, pronouns
    "a",
    "an",
    "the",
    "on",
    "in",
    "at",
    "to",
    "of",
    "for",
    "with",
    "by",
    "from",
    "into",
    "it",
    "that",
    "this",
    "these",
    "those",
    "me",
    "my",
    "us",
    "we",
    "i",
    "you",
    "any",
    "anymore",
    "longer",
    "again",
    "ever",
    "please",
    "really",
    # light verbs a trader puts between the refusal and the value
    "use",
    "using",
    "used",
    "set",
    "setting",
    "make",
    "making",
    "made",
    "put",
    "run",
    "running",
    "go",
    "going",
    "do",
    "does",
    "did",
    "be",
    "been",
    "being",
    "is",
    "are",
    "was",
    "were",
    "want",
    "wants",
    "wanted",
    "need",
    "needs",
    "needed",
    "like",
    "prefer",
    "watch",
    "watching",
    "monitor",
    "monitoring",
    "alert",
    "alerts",
    "trade",
    "trading",
    "include",
    "included",
    "including",
    "add",
    "adding",
    "keep",
    "keeping",
    "work",
    "working",
    "have",
    "has",
    "had",
    "get",
    "getting",
    # Arabic filler
    "على",  # 3ala - on
    "في",  # fi - in
    "من",  # min - from
    "مع",  # ma3 - with
    "عايز",  # 3ayez - want
    "عاوز",
    "عايزة",
    "استخدم",  # use
    "نستخدم",
    "تستخدم",
    "الفريم",  # el frame
    "فريم",
    # Arabizi filler
    "3ala",
    "fi",
    "men",
    "ma3",
    "3ayez",
    "3awez",
    "3ayza",
    "esta5dem",
    "nesta5dem",
    "frame",
    "el",
)

#: A letter, for the purpose of deciding where a word starts and ends. Arabic
#: script is included so ``مش`` is matched as a whole word rather than as a
#: fragment of a longer Arabic word.
_LETTER = r"0-9A-Za-z؀-ۿ"
_LEFT_EDGE = rf"(?<![{_LETTER}])"
_RIGHT_EDGE = rf"(?![{_LETTER}])"


def _term_pattern(term: str) -> str:
    """A whitespace-tolerant pattern for one vocabulary entry."""
    return r"\s+".join(re.escape(token) for token in term.split())


def _alternation(terms: tuple[str, ...]) -> str:
    # Longest first, so ``but not`` wins over ``not`` and ``no longer`` over ``no``
    # at the same starting position.
    ordered = sorted(set(terms), key=lambda term: (-len(term), term))
    return "|".join(_term_pattern(term) for term in ordered)


#: The rejection vocabulary as a regex alternation, exported so callers share the
#: exact wording instead of hand-writing a subset that drifts from it.
REJECTION_ALTERNATION: str = _alternation(REJECTION_TERMS)

#: The filler vocabulary as a regex alternation, exported for the same reason.
REJECTION_FILLER_ALTERNATION: str = _alternation(REJECTION_FILLER_TERMS)

_MARKER_RE = re.compile(
    rf"{_LEFT_EDGE}(?:{REJECTION_ALTERNATION}){_RIGHT_EDGE}",
    re.IGNORECASE,
)

#: Every filler entry is a single word, so membership is a set lookup rather than
#: a regex with a nested quantifier. That matters: the gap can be a whole sentence
#: when a marker appears early, and a nested-quantifier gap pattern backtracks
#: badly on exactly that input.
_FILLER_SET: frozenset[str] = frozenset(term.casefold() for term in REJECTION_FILLER_TERMS)

#: One token is a run of letters (with apostrophes), or a single other character.
#: Punctuation therefore becomes its own token and can never be filler, which is
#: what makes ``don't worry, use 15m`` keep 15m.
_GAP_TOKEN_RE = re.compile(rf"[{_LETTER}']+|[^\s{_LETTER}']")
_IS_LETTER_RE = re.compile(rf"[{_LETTER}']")


def _gap_is_only_filler(gap: str) -> bool:
    """True when nothing between a rejection and a value breaks the link."""
    return all(token.casefold() in _FILLER_SET for token in _GAP_TOKEN_RE.findall(gap))

#: Curly apostrophes are the same character to a reader and a different one to a
#: regex. Replacing one code point with one code point keeps every offset valid.
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "´": "'"})


def normalize_for_rejection(text: str) -> str:
    """Fold apostrophe spellings without moving any character position."""
    return (text or "").translate(_APOSTROPHES)


@dataclass(frozen=True, slots=True)
class Rejection:
    """One rejection marker, and where it sits in the text."""

    marker: str
    start: int
    end: int


def rejection_markers(text: str) -> tuple[Rejection, ...]:
    """Every rejection marker in ``text``, in order."""
    normalized = normalize_for_rejection(text)
    return tuple(
        Rejection(marker=match.group(0), start=match.start(), end=match.end())
        for match in _MARKER_RE.finditer(normalized)
    )


def rejects_following(prefix: str) -> bool:
    """True when ``prefix`` ends in a rejection governing whatever comes next.

    This is the primitive every reader calls. ``prefix`` is the text to the left
    of a candidate value; the value itself is not needed, because a rejection
    refuses whatever it is placed in front of.
    """
    return governing_rejection_of(prefix) is not None


#: How far back a rejection can reach. A governing rejection must be separated from
#: its value by filler alone, and filler words are short, so anything beyond this is
#: not reachable in any sentence a person writes — the longest marker plus eight
#: filler words is under 140 characters.
#:
#: The window is not a nicety. Without it this scans the whole prefix for every
#: candidate value, which is quadratic in the length of the turn: reading one long
#: message went from milliseconds to 183ms, and the suites that read thousands of
#: them stopped finishing.
_GOVERNING_WINDOW = 200


def governing_rejection_of(prefix: str) -> Rejection | None:
    """The rejection governing the value that follows ``prefix``, if any."""
    normalized = normalize_for_rejection(prefix)
    offset = 0
    if len(normalized) > _GOVERNING_WINDOW:
        offset = len(normalized) - _GOVERNING_WINDOW
        # Never start inside a word. A marker cut in half is not a marker, and a
        # word cut in half could look like one.
        while offset < len(normalized) and _IS_LETTER_RE.match(normalized[offset]):
            offset += 1
    window = normalized[offset:]
    governing: Rejection | None = None
    for match in _MARKER_RE.finditer(window):
        if _gap_is_only_filler(window[match.end() :]):
            # Nearest wins: a later marker replaces an earlier one.
            governing = Rejection(
                marker=match.group(0),
                start=offset + match.start(),
                end=offset + match.end(),
            )
    return governing


def is_rejected(text: str, start: int, end: int) -> bool:
    """True when the value occupying ``text[start:end]`` was refused."""
    del end  # A rejection is decided entirely by what precedes the value.
    return rejects_following(text[:start])


def rejected_matches(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[re.Match[str], ...]:
    """Every match of ``pattern`` in ``text`` that a rejection governs."""
    return tuple(match for match in pattern.finditer(text) if is_rejected(text, *match.span()))


#: The universe vocabularies as regex alternations, exported for the same reason
#: as :data:`REJECTION_ALTERNATION`.
UNIVERSE_EXCLUSION_ALTERNATION: str = _alternation(UNIVERSE_EXCLUSION_TERMS)
TRAILING_EXCLUSION_ALTERNATION: str = _alternation(TRAILING_EXCLUSION_TERMS)

_UNIVERSE_EXCLUSION_RE = re.compile(
    rf"{_LEFT_EDGE}(?:{UNIVERSE_EXCLUSION_ALTERNATION}){_RIGHT_EDGE}",
    re.IGNORECASE,
)
_TRAILING_EXCLUSION_RE = re.compile(
    rf"^\W*(?:fully\s+|hard\s+)?(?:{TRAILING_EXCLUSION_ALTERNATION})"
    rf"{_RIGHT_EDGE}(?!\s*:)",
    re.IGNORECASE,
)


def mentions_universe_exclusion(text: str) -> bool:
    """True when ``text`` carries wording that keeps a named symbol out.

    Read across a whole clause, not only beside the symbol, because
    ``exclude BTCUSDT and ETHUSDT`` excludes both.
    """
    return _UNIVERSE_EXCLUSION_RE.search(normalize_for_rejection(text)) is not None


def opens_with_trailing_exclusion(suffix: str) -> bool:
    """True when the wording *after* a symbol excludes that symbol."""
    return _TRAILING_EXCLUSION_RE.match(normalize_for_rejection(suffix)) is not None
