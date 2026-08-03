"""Deterministic classification of user-authored turn fragments.

Capability resolution answers one question: *which registered market mechanic does
this wording refer to?* That question is only meaningful for text that actually
describes a trading condition. Symbols, exclusions, timeframes, directions,
operators, thresholds, approval instructions, and ordinary conversation carry no
mechanic at all, so routing them into the resolver produces "unknown capability"
clarifications for terms the platform already understands perfectly well
(``What do you mean by 'BTCUSDT' in this setup?``).

Every classification here is deterministic and runs before any model call.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.engine.comparators import OPERATOR_TERMS, detect_comparator
from ai_market_monitor.engine.price_movement import PHRASAL_PARTICLE_GUARD
from ai_market_monitor.engine.prompt_aliases import normalized_phrases
from ai_market_monitor.engine.text_normalization import repair_utf8_mojibake
from ai_market_monitor.engine.timeframes import (
    WORD_TIMEFRAME_ALIASES,
    normalize_timeframe_alias,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection

FragmentKind = Literal[
    "symbol",
    "exclusion",
    "timeframe",
    "direction",
    "operator",
    "threshold",
    "approval",
    "approval_policy",
    "product_policy",
    "conversation_control",
    "decision_request",
    "reversion",
    "conversational",
    "trading_condition",
]
FragmentCategory = Literal[
    "SYMBOL",
    "INCLUDE",
    "EXCLUDE",
    "TIMEFRAME",
    "DIRECTION",
    "OPERATOR",
    "THRESHOLD",
    "FORMULA",
    "APPROVAL",
    "APPROVAL_INSTRUCTION",
    "PRODUCT_POLICY",
    "CONVERSATION_CONTROL",
    "DECISION_REQUEST",
    "REVERSION",
    "CONVERSATIONAL_TEXT",
    "TRADING_MECHANIC",
]

#: Fragment kinds that carry no market mechanic and must never reach the
#: capability resolver. Everything else is a genuine trading condition.
NON_MECHANIC_KINDS: frozenset[str] = frozenset(
    {
        "symbol",
        "exclusion",
        "timeframe",
        "direction",
        "operator",
        "threshold",
        "approval",
        "approval_policy",
        "product_policy",
        "conversation_control",
        "decision_request",
        "reversion",
        "conversational",
    }
)

#: A separator proves the intent, so any quote asset is safe in that form.
_SEPARATED_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "USD", "BTC", "ETH", "EUR", "TRY")
#: Without a separator the quote is only a suffix, and English words end in these
#: letters too: ``ENTRY``/``COUNTRY``/``REGISTRY`` end in TRY, ``AMATEUR`` in EUR,
#: ``TEETH`` in ETH. Restricting the concatenated form to dollar quotes keeps
#: ordinary trading prose out of the symbol parser.
_CONCATENATED_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "USD")

#: ``BTCUSDT``, ``BTC/USDT``, ``BTC-USDT`` and ``BTC_USDT`` all name the same market.
_SYMBOL_RE = re.compile(
    r"\b(?P<base>[A-Z][A-Z0-9]{1,9})[/\-_](?P<quote>" + "|".join(_SEPARATED_QUOTES) + r")\b"
    r"|"
    r"\b(?P<base2>[A-Z][A-Z0-9]{2,9})(?P<quote2>" + "|".join(_CONCATENATED_QUOTES) + r")\b"
)
#: Unit words, including the Arabic and Arabizi forms traders actually type. A
#: HilalMarkets user writing `فريم 15 دقيقة` or `3ala 15 de2i2a` is naming the same
#: timeframe as `15m`; reading only the English form dropped the timeframe entirely.
_TIMEFRAME_UNITS = (
    r"m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks|"
    r"دقيقة|دقائق|دقيقه|د|ساعة|ساعات|ساعه|س|يوم|أيام|ايام|ي|أسبوع|اسبوع|أسابيع|اسابيع|"
    r"de2i2a|da2i2a|da2aye2|de2aye2|sa3a|sa3at|yom|ayam|esboo3|osboo3"
)
_TIMEFRAME_RE = re.compile(
    r"(?<![a-z0-9؀-ۿ])(\d{1,2})\s*" rf"({_TIMEFRAME_UNITS})(?![a-z0-9؀-ۿ])",
    re.IGNORECASE,
)

#: Arabic and Arabizi unit words mapped to the English stem `_canonical_timeframe`
#: already understands, so there is one conversion rule rather than two.
_UNIT_ALIASES: dict[str, str] = {
    "دقيقة": "m",
    "دقائق": "m",
    "دقيقه": "m",
    "د": "m",
    "de2i2a": "m",
    "da2i2a": "m",
    "da2aye2": "m",
    "de2aye2": "m",
    "ساعة": "h",
    "ساعات": "h",
    "ساعه": "h",
    "س": "h",
    "sa3a": "h",
    "sa3at": "h",
    "يوم": "d",
    "أيام": "d",
    "ايام": "d",
    "ي": "d",
    "yom": "d",
    "ayam": "d",
    "أسبوع": "w",
    "اسبوع": "w",
    "أسابيع": "w",
    "اسابيع": "w",
    "esboo3": "w",
    "osboo3": "w",
}
_PERCENT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*%")
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

_EXCLUSION_MARKERS = (
    "exclude",
    "excluding",
    "excluded",
    "never include",
    "not include",
    "no ",
    "without",
    "omit",
    "ignore",
    "skip",
    "drop",
    "leave out",
    "remove",
    # The subtracting words. `BTC on 15m but not LTC` states an exclusion just as
    # plainly as `exclude LTC`, and without them LTC was added to the watchlist the
    # trader had just told us to keep it out of. A bare `not` stays out on purpose:
    # `not below 30` is a comparison, not a universe rule.
    "but not",
    "except for",
    "except",
    "apart from",
    "other than",
    "aside from",
    "minus",
    "استبعاد",
    "نستبعد",
    "ما عدا",
    "ماعدا",
    "عدا",
    "باستثناء",
    "ممنوع",
    "مش عايز",
    "مش عايزه",
    "مش عايزة",
)
_INCLUSION_ONLY_MARKERS = (
    "only",
    "just",
    "solely",
    "exclusively",
    "nothing but",
    "فقط",
    "بس",
)

#: Exclusion wording that follows the symbol (``BTCUSDT never included``). Kept
#: narrower than the prefix markers because a trailing bare ``no`` is ambiguous.
_TRAILING_EXCLUSION_MARKERS = (
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

#: Approval must be recognised semantically but deterministically. Activation still
#: requires the server-side authorisation and hash checks; this only detects intent.
_APPROVAL_PATTERNS = (
    r"^\W*approve\W*$",
    r"^\W*approved\b",
    r"\bi\s+approve\b",
    r"^\W*(?:the\s+)?(?:draft|rules|version|setup)\s+(?:is|are)\s+approved\b",
    r"\bgo\s+ahead\b",
    r"^\W*go\W*$",
    r"\byes[,\s]+(?:approve|proceed|go|finalize|finalise|activate)\b",
    r"\bi\s+confirm\b.{0,32}\bi\s+approv",
    r"^\W*confirm(?:ed)?\W+(?:and\W+)?approv",
    r"\bproceed\b.*\bapprov",
    r"\bsign\s*off\b",
    r"\blooks\s+good[,\s]*(?:approve|go|proceed|ship)\b",
    r"^\W*(?:ok|okay)[,\s]+(?:approve|proceed|finalize|finalise)\W*$",
    # Arabizi / Franco-Arabic. Digits are letters here, not thresholds.
    r"^\W*(?:ana\s+)?(?:mowafe2|mowa2e2|mwafe2|awafe2|e3temed|e3tamed|mo3tamed)\W*$",
    r"^\W*(?:tamam|aywa|aiwa|ah)[,\s]+"
    r"(?:mowafe2|mowa2e2|mwafe2|awafe2|e3temed|e3tamed)\W*$",
    r"^\W*(?:موافق|أوافق|اوافق|تمت\s+الموافقة|تمت\s+الموافقه|اعتمد|معتمد)\W*$",
    r"\b(?:نعم|ايوه|أيوه|تمام)\b.{0,24}\b"
    r"(?:أوافق|اوافق|موافق|اعتمد|اعتماد|تمت\s+الموافقة)\b",
    r"^\W*(?:أوافق|اوافق|موافق|موافقة|اعتمد|اعتماد)\W*$",
    r"\b(?:نعم|ايوه|أيوه|تمام)\b.{0,16}\b(?:أوافق|اوافق|وافق|اعتمد)\b",
)
_APPROVAL_NEGATIONS = (
    # Describing *when* approval will be given is not giving it. "You only confirm
    # after I say I approve" states the gate; treating it as the grant would let the
    # assistant approve on the user's behalf.
    r"\b(?:after|once|when|only\s+(?:after|when|once))\s+i\s+(?:say|type|write|give|send)\b",
    r"\bno\s+auto-?(?:approv\w*|greenlight|green-?light)\b",
    r"\bauto-?(?:approval|greenlight|green-?light)\b",
    r"\bapprov\w*\s+(?:must|has\s+to|should)\s+(?:stay|remain|be)\b",
    r"\bapprov\w*\s+(?:must|has\s+to|should)\s+"
    r"(?:apply|bind|attach|refer|match)\b",
    # A future or conditional promise is not the present authenticated action.
    r"\bi(?:'ll|\s+will|\s+would|\s+can|\s+may|\s+might|\s+am\s+going\s+to)\s+"
    r"(?:(?:review|check)\s*(?:and|/)\s*)?approv",
    r"\b(?:do\s+not|don'?t|never|without|before|until|unless|not)\b[^.]{0,40}\bapprov",
    r"\bapprov\w*\b[^.]{0,30}\?",
    r"\bwait\s+for\b[^.]{0,30}\bapprov",
    r"\bneed(?:s)?\s+(?:my|your|explicit)\s+approv",
    r"\brequires?\s+(?:my|your|explicit)\s+approv",
    r"\bask\s+for\b[^.]{0,20}\bapprov",
    r"\b(?:please\s+)?(?:reply|type|write|say|ask)\b[^.]{0,40}\b"
    r"(?:i\s+approve|approved)\b",
    r"\b(?:do\s+not|don'?t|will\s+not|won'?t|not)\s+"
    r"(?:proceed|go\s+ahead|approve)\b",
    r"\bwithout\b[^.]{0,32}\b(?:approval|go-?ahead|greenlight)\b",
    r"\b(?:لا|مش|ما)\b.{0,24}\b"
    r"(?:توافق|توافقش|تعتمد|تعتمدش|موافقة|اعتماد)\b",
    r"\b(?:لا|مش|ما)\b.{0,16}\b(?:توافق|توافقش|تعتمد|تعتمدش|موافقة|اعتماد)\b",
)

_DIRECTION_TERMS: dict[str, StrategyDirection] = {
    "long": StrategyDirection.LONG,
    "longs": StrategyDirection.LONG,
    "buy": StrategyDirection.LONG,
    "bullish": StrategyDirection.LONG,
    "bull": StrategyDirection.LONG,
    "upside": StrategyDirection.LONG,
    "short": StrategyDirection.SHORT,
    "shorts": StrategyDirection.SHORT,
    "sell": StrategyDirection.SHORT,
    "bearish": StrategyDirection.SHORT,
    "bear": StrategyDirection.SHORT,
    "downside": StrategyDirection.SHORT,
    "both directions": StrategyDirection.BOTH,
    "either direction": StrategyDirection.BOTH,
    "any direction": StrategyDirection.BOTH,
    "long and short": StrategyDirection.BOTH,
    "both long and short": StrategyDirection.BOTH,
    "\u0635\u0627\u0639\u062f": StrategyDirection.LONG,
    "\u0635\u0627\u0639\u062f\u0629": StrategyDirection.LONG,
    "\u0635\u0639\u0648\u062f": StrategyDirection.LONG,
    "\u0635\u0639\u0648\u062f\u064a": StrategyDirection.LONG,
    "\u0647\u0627\u0628\u0637": StrategyDirection.SHORT,
    "\u0647\u0627\u0628\u0637\u0629": StrategyDirection.SHORT,
    "\u0647\u0628\u0648\u0637": StrategyDirection.SHORT,
    "\u0647\u0628\u0648\u0637\u064a": StrategyDirection.SHORT,
}

#: The comparison vocabulary lives in one module so every reader shares the same
#: mapping and the same longest-first ordering.
_OPERATOR_TERMS = OPERATOR_TERMS

#: A sweep is a wick through a level that closes back on the original side. It is a
#: distinct mechanic from a cross, so it is recorded separately for the compiler.
_SWEEP_TERMS = ("sweep", "sweeps", "swept", "sweeping", "liquidity grab", "stop hunt")

_CONVERSATIONAL_RE = re.compile(
    r"^\W*(?:"
    # A greeting may carry a vocative (`hi there`, `hello team`) and stays a greeting.
    r"(?:hi|hey|hello|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+(?:there|all|team|folks|again|everyone))?|"
    r"hi|hey|hello|thanks|thank\s+you|thx|ty|ok|okay|k|sure|cool|great|nice|perfect|"
    r"got\s+it|understood|makes\s+sense|sounds\s+good|right|correct|exactly|yep|yeah|yes|"
    r"no|nope|please|sorry|never\s*mind|nvm|good|awesome|excellent|alright|fine|"
    r"that\s+(?:looks|sounds|is)\s+(?:right|good|correct|fine)|"
    r"what\s+do\s+you\s+mean|can\s+you\s+explain|i\s+see|hmm+|"
    r"one\s+(?:sec|second|moment)|hold\s+on|wait"
    r")\W*$",
    re.IGNORECASE,
)

_META_QUESTION_RE = re.compile(
    r"^\W*(?:"
    r"(?:so\s+)?(?:what|which|how|why|when|who|where)\b.*\?|"
    r"(?:can|could|would|will|do|does|did|is|are|should)\s+(?:you|it|that|this|we)\b.*\?"
    r")\W*$",
    re.IGNORECASE,
)

_META_DISCOURSE_RE = re.compile(
    r"\b(?:"
    r"last\s+(?:reply|message|answer)|"
    r"stop\s+(?:asking|looping|dodging|punting)|"
    r"don'?t\s+(?:dodge|overthink|keep\s+asking)|"
    r"you(?:'re|\s+are)\s+(?:looping|dodging)|"
    r"internal[-\s]+error\s+nonsense|"
    r"re[-\s]*sending\s+it\s+clean|"
    r"hold\s+up|come\s+on|bro\b|"
    r"reply\s+was\s+(?:blank|empty)|"
    r"confirm\s+(?:you|that)|"
    r"spell(?:ed)?\s+out\s+clearly"
    r")\b",
    re.IGNORECASE,
)

_MARKET_FACT_REQUEST_RE = re.compile(
    r"\b(?:show|tell)\s+me\s+(?:how|what)\s+(?:the\s+)?markets?\s+"
    r"(?:looks?|is\s+doing|are\s+doing)\b|"
    r"\b(?:show|give)\s+me\s+(?:the\s+)?(?:current\s+)?market\s+snapshot\b|"
    r"\bhow\s+(?:is|are)\s+(?:the\s+)?markets?\b",
    re.IGNORECASE,
)

#: Wording that talks *about* the approval gate rather than granting it. The grant
#: itself is detected by :func:`is_approval_instruction`, which deliberately refuses
#: these negated and interrogative forms. Both are approval traffic, so neither may
#: be handed to the capability resolver as an unconverted market mechanic.
_APPROVAL_POLICY_RE = re.compile(
    r"\b(?:approve|approved|approval|greenlight|green-light|sign\s*off|"
    r"finali[sz]e|finali[sz]ed|activate|activation|auto-?approv\w*|"
    r"موافقة|اعتماد|تفعيل)\b"
    r"|"
    # The gate stated without the word "approval": something is withheld until the
    # trader gives the word. `nothing runs until I say yes` is the approval rule, not
    # a market rule, and reporting it as an unconvertible mechanic gave the trader a
    # blocking finding for the very safeguard they were asking for.
    r"\b(?:nothing|none\s+of\s+it|no\s+\w+|don'?t|do\s+not|never)\b[^.!?]{0,60}?"
    r"\b(?:until|before|unless)\s+(?:i|we|you)\s+"
    r"(?:say|said|give|gave|confirm|confirmed|approve|approved|greenlight|ok|okay)\b",
    re.IGNORECASE,
)

#: Discourse openers that carry no meaning of their own. A fragment's role in the
#: conversation does not change because it starts with `And`, `Then`, `Now` or
#: `Alright` — but every ``^``-anchored dialogue pattern below stopped matching when
#: one was present, so `And confirm the rest in one line` was read as a market
#: instruction while `Confirm the rest in one line` was not.
#: `no`, `yes` and `just` are deliberately absent. They are not filler: `no` carries the
#: negation (`no alerts until I say so` becomes `alerts until I say so` without it) and
#: `just` narrows a universe. Stripping a word that changes meaning is how a safeguard
#: turns into its opposite.
_DISCOURSE_PREFIX_RE = re.compile(
    r"^(?:\W*\b(?:and|but|so|then|also|plus|now|next|first|finally|actually|"
    r"ok|okay|alright|well|please)\b\W*)+",
    re.IGNORECASE,
)

#: Governance and labelling policy. HilalMarkets never assigns a Sharia status from
#: chat, so these instructions are product policy the platform already enforces —
#: they are not market mechanics and can never be "converted into an executable
#: rule". Routing them into the resolver produced blocking findings that no answer
#: from the trader could ever clear.
_PRODUCT_POLICY_RE = re.compile(
    r"\b(?:sharia|shariah|shari'a|halal|haram|islamic|religious|ethical|"
    r"compliance\s+status|screening\s+status|"
    r"(?:extra\s+|any\s+)?(?:labels?|tags?|statuses|status)\b(?![\s-]*(?:quo|code))|"
    r"شرعي|شرعية|حلال|حرام)\b",
    re.IGNORECASE,
)

#: A Sharia word used as an adjective on a market noun — `halal coins`, `sharia-
#: compliant pairs`, `islamic assets`. That restricts *which* coins to watch; it does
#: not ask for a status to be assigned, which is what the labelling policy branch
#: exists to catch. The distinction matters because the policy branch consumes the
#: whole fragment, so a rule stated alongside the filter was being discarded.
#:
#: Sharia status itself is still never decided here: the platform's own screening owns
#: the universe, and this only stops one phrase from silencing a mechanic.
_POLICY_QUALIFIES_UNIVERSE_RE = re.compile(
    r"\b(?:halal|sharia|shariah|shari'a|islamic)(?:[-\s]*compliant)?[-\s]+"
    r"(?:coins?|pairs?|assets?|symbols?|tokens?|markets?|universe|watchlist|list|"
    r"عملات|أصول)\b"
    r"|\b(?:عملات|أصول)\s+(?:حلال|شرعية|إسلامية)\b",
    re.IGNORECASE,
)

#: Instructions addressed to the assistant about how to hold the conversation:
#: be precise, confirm, restate, stop asking. They describe the dialogue, not the
#: market, so they carry no mechanic to resolve.
_CONVERSATION_CONTROL_RE = re.compile(
    r"\b(?:"
    r"be\s+(?:precise|exact|specific|clear)|"
    r"(?:it|that|this)\s+must\s+be\s+measurable|"
    r"needs?\s+to\s+be\s+measurable|"
    # A scare-quoted verb (`don't "merge" it`) is the same instruction, so the
    # separator tolerates the quote characters chat clients insert.
    r"(?:don't|do\s+not|no|never)\s+[\"']?"
    r"(?:guess|guessing|assume|assuming|sneak|dodge|merge|merging)|"
    r"(?:don't|do\s+not|never)\s+(?:carry|transfer|reuse)\s+"
    r"(?:it|approval|the\s+approval)\s+over\b|"
    r"(?:i\s+)?(?:won't|will\s+not|am\s+not\s+going\s+to)\s+[\"']?(?:guess|assume|merge)|"
    r"pin(?:ned)?\s+(?:it\s+)?down|lock(?:ed)?\s+(?:it|these|those|them)?\s*in|"
    # A hypothetical about what one of *us* does next describes the conversation,
    # not the market: "if I change my mind", "if you later change any parameter".
    r"if\s+(?:i|you|we)\s+(?:later\s+|ever\s+|then\s+)?"
    r"(?:change|revert|update|modify|say|type|decide|want)|"
    r"quick\s+check|"
    # An imperative addressed to the assistant about *replying*. Restricted to verbs
    # that are unambiguously about the dialogue — `watch`, `alert`, `find` and `tell`
    # all start real requests, so they are deliberately absent. Applied after the
    # discourse opener is stripped, so `And confirm…` is read like `Confirm…`.
    r"^(?:reply|answer|respond|confirm|commit|restate|clarify|acknowledge|"
    r"paste|print|paraphrase|paraphrase)\b|"
    # `test it exactly as specified` — a pronoun object means the thing under test was
    # named earlier, so this fragment carries no rule of its own. `test the RSI below
    # 30 rule` states one and is guarded by `_states_measurable_condition`.
    r"^(?:test|check|verify|validate|re-?run)\s+(?:it|that|this|these|those)\b|"
    # Asking the assistant to confirm its own behaviour: `confirm you will query
    # OHLCV`, `timestamps you will pull`. About the assistant's actions, not the
    # market's.
    r"\byou\s+will\s+(?:query|pull|fetch|use|call|read|return|output|send|apply)\b|"
    # A hypothetical about one of *our* turns. `if` was already here; `when I reply
    # OK` is the same shape and was not.
    r"\b(?:if|when|once|after)\s+(?:i|you|we)\s+"
    r"(?:reply|replies|say|says|type|send|write|confirm|approve|answer)\b|"
    r"\b(?:i'?m|i\s+am)\s+not\s+doing\b|"
    # Assessing the assistant's previous turn is commentary, not a requirement.
    r"\byou(?:'re|\s+are)?\s+(?:still\s+)?(?:haven'?t|hadn'?t|didn'?t|never|keep|"
    r"said|asked|basically|dodg\w*|loop\w*)\b|"
    r"\b(?:still\s+)?not\s+(?:acceptable|resolved|good\s+enough|what\s+i\s+asked)\b|"
    r"\bgood\s+catch\b|"
    r"restate|spell\s+(?:it\s+)?out|sanity\s+check|just\s+to\s+be\s+safe|"
    r"answer\s+(?:those|that|these)|tell\s+me\s+(?:exactly|precisely)|"
    r"stop\s+(?:asking|looping)|"
    r"(?:i\s+need\s+you\s+to|you\s+need\s+to|please)\W*"
    r"(?:confirm|summari[sz]e|restate|explain|answer|show|state)\b|"
    r"(?:just\s+)?answer\W+(?:what|which|how|why)\b|"
    r"you\s+already\s+(?:have|know)\b[^.!?]{0,80}\b(?:answer|confirm|state)\b|"
    r"^\W*or\s+confirm\b|"
    r"if\s+(?:anything|something|the\s+(?:draft|spec|version|hash|text|rule\s*set))\s+"
    r"changes?\b|"
    r"(?:build|test)\s*/?\s*(?:test|build)?\s+the\s+exact\s+rule\s+set"
    r")\b|"
    # Questions addressed to the assistant about its explanation or representation
    # are dialogue acts. The market-facing verbs intentionally remain absent.
    r"^\W*(?:can|could|would|will)\s+you\s+"
    r"(?:confirm|explain|summari[sz]e|restate|show|print|display|return|provide)\b|"
    # Requests about what the product should display on a later turn are likewise
    # presentation instructions, not market conditions.
    r"^\W*(?:next|this)\s+time\W+(?:the\s+)?"
    r"(?:system|app|chat|assistant|you)\s+"
    r"(?:shows?|prints?|displays?|returns?|provides?|includes?)\b",
    re.IGNORECASE,
)

#: Verbs whose object is the assistant's own answer — how to read the trader's
#: words, what to produce, what to call things. A market noun inside such an
#: instruction does not make it a market mechanic: `don't interpret it as "Strong
#: Swing High"` is about the reading, not about swing highs. Verbs that also start
#: genuine requests (`show`, `list`, `find`, `watch`, `alert`) are deliberately
#: absent — `show me coins that dropped 5%` is a real scan.
_META_VERBS = (
    "interpret",
    "interpreting",
    "reinterpret",
    "read",
    "reading",
    "treat",
    "treating",
    "map",
    "mapping",
    "call",
    "calling",
    "name",
    "naming",
    "rename",
    "renaming",
    "label",
    "labelling",
    "labeling",
    "relabel",
    "relabelling",
    "relabeling",
    "word",
    "wording",
    "phrase",
    "phrasing",
    "restate",
    "rephrase",
    "spell",
    "write",
    "writing",
    "output",
    "outputting",
    "emit",
    "emitting",
    "invent",
    "inventing",
    "assume",
    "assuming",
    "guess",
    "guessing",
    "ask",
    "asking",
    "answer",
    "answering",
    "reply",
    "replying",
    "explain",
    "explaining",
)

#: Building verbs. These start real requests (`build me a monitor that…`), so they
#: only mark a meta-instruction when they are prohibited rather than asked for.
_BUILD_VERBS = ("build", "building", "create", "creating", "make", "making", "add", "adding")

#: An instruction aimed at the assistant's own reading or output rather than at the
#: market. This is a *class*, not a list of observed phrasings: a prohibition or an
#: obligation whose verb acts on the answer. Guarded by
#: :func:`_states_measurable_condition`, so "don't guess — RSI must be under 30"
#: keeps its rule.
_META_INSTRUCTION_RE = re.compile(
    # Prohibition aimed at the assistant: "don't interpret it as…", "never assume…",
    # "no need to build a new rule". Up to two words may sit between the negation and
    # the verb ("don't just merge", "do not ever rename").
    r"\b(?:don'?t|do\s+not|never|no\s+need\s+to|stop|avoid|please\s+don'?t)\s+"
    r"(?:\w+\s+){0,2}?"
    rf"(?:{'|'.join((*_META_VERBS, *_BUILD_VERBS))})\b"
    r"|"
    # Obligation aimed at the assistant: "you must map the operators exactly".
    # Only the presentation verbs, never the building ones.
    r"\b(?:you|i|we)\s+(?:must|should|need\s+to|have\s+to|will|shall)\s+"
    r"(?:\w+\s+){0,2}?"
    rf"(?:{'|'.join(_META_VERBS)})\b"
    r"|"
    # Saying what the turn is *not* about states no requirement of its own.
    r"\b(?:i'?m|i\s+am|we'?re|we\s+are)\s+not\s+(?:asking|talking|referring)\b"
    r"|"
    # A rule about how the trader's own typing should be handled is about input,
    # not about the market: "if you write stuff like 'ba3s' with typos".
    r"\bif\s+(?:i|you|we)\s+(?:write|type|send|paste|spell|misspell|use)\b",
    re.IGNORECASE,
)

#: A leading coordinating conjunction joins this clause to the previous one. It adds
#: no meaning of its own, so completeness is judged on what follows it: `and a 1h
#: trigger to confirm` is exactly as complete as `a 1h trigger to confirm`. This is
#: stripped, never used to reject — clauses beginning with `and` or `but` routinely
#: carry the whole requirement, and vetoing them dropped real rules.
_CONTINUATION_OPENER_RE = re.compile(
    r"^\W*(?:and|or|but|so|then|plus|also|otherwise)\b",
    re.IGNORECASE,
)

#: A blank the trader never filled in: `within next __ minutes`, `stop at [ ]%`. The
#: value is missing by construction, so there is nothing to compile and nothing the
#: resolver could ask about that the blank does not already ask.
_PLACEHOLDER_RE = re.compile(r"_{2,}|\[\s*\]|\{\s*\}|<\s*>")

#: A serialized field quoted back at the assistant: `"approval_required": true`,
#: `**Context:** 1h = weakening`. The trader is showing the artifact, not stating a
#: requirement. Guarded by `_states_measurable_condition`, so `Condition: RSI below
#: 30` — the same shape carrying a real rule — is untouched.
#: Deliberately narrow. `Label: value` is how traders write ordinary instructions —
#: `Trigger: 5m`, `Apply: add candle-close confirmation` — so only the two shapes that
#: are unmistakably a quoted artifact count: a JSON pair with a quoted key, and a
#: markdown field label carrying its own colon.
_FIELD_ECHO_RE = re.compile(
    r'^\W*"[a-z][a-z0-9_]*"\s*:\s*\S'
    r"|^\s*\*\*[^*]{1,40}:\s*\*\*"
    r"|^\s*\*\*[^*]{1,40}\*\*\s*:",
    re.IGNORECASE,
)

#: Brackets or quotes that never close prove the text was cut mid-structure. Only
#: applied to short fragments: the splitter routinely cuts long real clauses inside a
#: parenthesis, and rejecting those dropped rules the trader had fully stated.
_BRACKET_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"))


def _brackets_are_balanced(text: str) -> bool:
    for opener, closer in _BRACKET_PAIRS:
        if text.count(opener) != text.count(closer):
            return False
    return text.count('"') % 2 == 0


#: A request to act now — run the check, proceed, go ahead. It asks the assistant to
#: take the next step in the conversation; it states no requirement about the market,
#: so there is nothing in it for the capability resolver to recognise.
_ACTION_REQUEST_RE = re.compile(
    r"^\W*(?:go\s+ahead|proceed|continue|carry\s+on|"
    r"(?:run|execute|perform|do)\s+(?:it|that|this|the\b)|"
    r"let'?s\s+(?:go|do\s+it|proceed|run|lock|start|begin)|"
    r"(?:build|create|make|assemble|generate)\s+(?:it|this|that)\b)"
    r"|"
    # A request for the *artefact* rather than for a market condition: `build the
    # SOLUSDT-only watchlist`, `set up a scanner`. The mechanics come from the rest of
    # the conversation; this sentence only asks for the thing to be assembled. Guarded
    # by `_states_measurable_condition`, so `build me a monitor that fires when RSI is
    # under 30` keeps its rule.
    r"^\W*(?:build|create|make|set\s+up|assemble|generate|give\s+me)\s+"
    r"(?:me\s+)?(?:a\s+|an\s+|the\s+)?[\w-]{0,20}\s*"
    r"(?:watchlist|monitor|filter|scanner|screener|alert|setup|strategy|list|rule\s*set)\b"
    r"|"
    r"^\W*(?:(?:i(?:'m|\s+am)|we(?:'re|\s+are))\s+)?"
    r"(?:start(?:ing)?|begin(?:ning)?)\s+(?:a\s+|an\s+|the\s+|my\s+)?"
    r"(?:watchlist|monitor|scanner|screener|alert|setup|strategy|list)\b",
    re.IGNORECASE,
)


#: Typographic apostrophes and quotes reach the classifier verbatim from chat
#: clients. Matching them as their ASCII equivalents keeps `won’t` and `won't` on
#: the same code path instead of silently classifying one of them differently.
_SMART_PUNCTUATION = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _plain_quotes(text: str) -> str:
    return (
        text.translate(_SMART_PUNCTUATION)
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


#: A fragment that asks which of several readings to use. It records an open
#: decision, not an authored requirement, so compiling it would manufacture a
#: condition the trader never asked for.
_DECISION_REQUEST_RE = re.compile(
    r"\bvs\.?\b|\bversus\b|\bor\s+(?:is\s+it|do\s+you|should\s+(?:i|we|it))\b|"
    r"\bor\s+(?:include|exclude|use|choose|watch|monitor|keep|remove)\b[^?]{0,80}\?|"
    r"\b(?:do|would|should|will|can)\s+(?:you|we|i)\b[^?]{0,100}\bor\b[^?]{0,100}\?|"
    r"\b(?:should|does|is)\s+(?:the\s+)?"
    r"(?:trigger|filter|rule|condition|measurement)\b[^?]{0,100}\bor\b[^?]{0,100}\?|"
    r"(?:\u0623\u0648|\u0648\u0644\u0627)[^?\u061f]{0,100}[?\u061f]|"
    r"\bwhich\s+(?:one|of|do|should|would)\b|"
    # "confirm whether X" asks which reading applies; it asserts neither.
    r"\b(?:confirm|clarify|tell\s+me|say)\s+whether\b|\bwhether\s+(?:it'?s|it\s+is)\b|"
    r"\bwhat(?:'s|\s+is)\s+(?:the\s+)?(?:exact|precise)\b",
    re.IGNORECASE,
)

#: Approval identity can be split away from the word ``approval`` by punctuation
#: and conjunctions. Hashes, reviewed versions and conversation snapshots are
#: application lifecycle metadata, never market measurements.
_APPROVAL_BINDING_RE = re.compile(
    r"\b(?:"
    r"sha-?256|canonical\s+hash|schema\s+hash|conversation\s+snapshot|"
    r"(?:draft|reviewed|rule|strategy|schema|spec(?:ification|text)?|json)\s+"
    r"(?:version|hash|snapshot)|"
    r"(?:version|hash|snapshot)\s+(?:reference|identity|changes?|changed)|"
    r"exact\s+(?:reviewed\s+)?(?:version|hash|snapshot|spec(?:ification|text)?|json)|"
    r"(?:bind|bound|attach(?:ed)?|tie[ds]?)\b[^.!?]{0,60}\b"
    r"(?:version|hash|snapshot|spec(?:ification|text)?|json)|"
    r"(?:version|hash|snapshot|spec(?:ification|text)?|json)\b[^.!?]{0,60}\b"
    r"(?:bind|bound|attach(?:ed)?|tie[ds]?|carry|reuse|change[sd]?)|"
    r"carry[-\s]*over|re-?request(?:ed|ing)?"
    r")\b",
    re.IGNORECASE,
)

#: A rejected implementation example is not an authored threshold. This catches
#: grammar such as "do not invent another parameter" without knowing any evaluator
#: phrase or parameter name.
_NEGATED_METADATA_RE = re.compile(
    r"\b(?:don'?t|do\s+not|never|no|without)\b[^.!?]{0,48}\b"
    r"(?:introduce|invent|add|assume|use|create|make\s+up|made[-\s]+up)\b"
    r"[^.!?]{0,48}\b(?:parameter|assumption|field|setting|value|rule)\w*\b|"
    r"\b(?:no|without)\s+(?:extra|additional|invented|made[-\s]+up)\s+"
    r"(?:parameter|assumption|field|setting|value|rule)\w*\b|"
    r"\b(?:don'?t|do\s+not|never)\s+"
    r"(?:introduce|invent|add|assume|use|create|make\s+up)\s+"
    r"[a-z][a-z0-9_]{2,48}\s*=\s*[-+]?\d|"
    r"\bno\b[^.!?]{0,32}\b[a-z][a-z0-9_ ]{2,32}\s*=\s*\d",
    re.IGNORECASE,
)

# A correction to a named canonical field is configuration, not a new market
# mechanic. The current draft already owns the formula that gives the value its
# meaning, so requiring the trader to repeat that formula would needlessly route a
# deterministic patch through capability resolution or the model.
_CANONICAL_FIELD_ASSIGNMENT_RE = re.compile(
    r"^\W*(?:please\s+)?"
    r"(?:change|set|update|edit|adjust|replace|switch|make)\s+(?:only\s+)?"
    r"(?:the\s+)?(?:percentage\s+|numeric\s+)?"
    r"(?:threshold|operator|comparator|timeframe|direction|symbols?|"
    r"exchange|quote(?:\s+asset)?|market\s+type)\b",
    re.IGNORECASE,
)

# Dialogue intent is identified by grammatical role, not by a list of evaluator
# sentences. These forms discuss how the setup is represented or explained. They may
# still carry canonical configuration such as `ETHUSDT only`, but they never become
# executable mechanics or unsupported-capability blockers.
_DIALOGUE_STATEMENT_RE = re.compile(
    r"^\W*(?:"
    r"(?:(?:yeah|yes|no|okay|ok|alright|right|sure)\W*)?"
    r"(?:(?:a\s+)?(?:quick|brief|simple|clean)\s+)?"
    r"(?:setup|summary|recap|note|check|confirmation)?\W*$|"
    r"(?:yeah|yes|no|okay|ok|alright|right|sure)?\W*"
    r"let'?s\s+(?:not\s+)?(?:overcomplicate|complicate|simplify|clarify|map|"
    r"organize|organise|summari[sz]e|restate|keep)\b|"
    r"let'?s\s+avoid\s+(?:overcomplicating|complicating|mixing)\b|"
    r"(?:i|we|you)\s+(?:do\s+not|don'?t|won'?t|shouldn'?t|needn'?t|"
    r"want\s+to|need\s+to|should|can)\s+"
    r"(?:overcomplicate|complicate|simplify|clarify|map|restate|repeat|"
    r"explain|ask|mix)\b|"
    r"(?:it|that|this)\s+(?:just\s+)?"
    r"(?:means|ensures|explains|prevents|keeps|confirms|clarifies)\b|"
    r"you\s+already\s+(?:have|know)\s+(?:the\s+|this\s+|that\s+)?"
    r"(?:answer|requirement|rule|condition|context|timeframe|threshold|direction)\b|"
    r"(?:it'?s|it\s+is|that'?s|that\s+is)\s+(?:the\s+)?"
    r"(?:first|second|third|former|latter)\b|"
    r"(?:no|not|without)\s+(?:any\s+|more\s+)?"
    r"(?:questions?|heavy\s+formulas?|complex\s+formulas?|complicated\s+formulas?|"
    r"technical\s+formulas?|extra\s+complexity|overcomplication|"
    r"(?:direction(?:al)?|trend|bias)\s+(?:filter|bias))\b|"
    r"(?:keep|make)\s+(?:it|this|that|the\s+(?:answer|setup|rule|rules))\s+"
    r"(?:simple|clear|concise|brief|readable|measurable)\b|"
    r"(?:here(?:'s|\s+is)|below\s+is)\s+(?:the\s+)?(?:exact|final|current)\s+"
    r"(?:nested\s+)?(?:structure|expression|logic|setup|rule)\b|"
    r"(?:i|we)\s+(?:am\s+not|are\s+not|won'?t|will\s+not)\s+"
    r"(?:lock(?:ing)?|finali[sz](?:e|ing)|proceed(?:ing)?)\b|"
    r"so\W*(?:yes\s*/\s*no|give\s+me|tell\s+me|confirm)\b|"
    r"(?:no|not)\s+(?:concrete|exact|clear|actual)\s+"
    r"(?:gating\s+)?(?:logic|answer|definition|details?)\b|"
    r"(?:no|don'?t|do\s+not|never)\s+mix(?:ing)?\b|"
    r"keep\s+it\s+(?:strict|clean|simple)\b|"
    r"(?:nah\W+)?(?:that|this|it)(?:'s|\s+is|\s+was|\s+looks?|\s+sounds?)?\W*"
    r"not\s+(?:clean|clear|precise|specific|complete|good)\s+enough\b|"
    r"(?:(?:yeah|yep|yes|okay|ok|alright|right|sure)\W*)?"
    r"(?:tight|clean|brief|short|concise|final)\s+"
    r"(?:confirmation|confirm|summary|recap|answer)\s*(?:block)?\b|"
    r"(?:confirmation|summary|recap)\s+block(?:\W+\w+)?\b|"
    r"(?:in|as)\s+(?:one|a)\s+(?:tight|clean|brief|short|concise)\s+"
    r"(?:block|paragraph|line|answer)\b|"
    r"(?:nah|no|please)\s+stop\s+(?:stalling|looping|asking|dodging)\b|"
    r"(?:i\s+need\s+you\s+to\s+|please\s+)?stop\s+talking\b|"
    r"(?:you\s+can|can\s+you|please)\s+"
    r"(?:absolutely\s+)?(?:write|list|describe|explain|show|return|give)\b"
    r"[^.!?]{0,80}\b(?:rules?|criteria|conditions?|output|payload|json)\b|"
    r"(?:give|show|return|paste|display|output)\s+(?:me\s+)?"
    r"[^.!?]{0,80}\b(?:ui\s+output|backend\s+payload|payload|contract|json)\b|"
    r"(?:the\s+)?[\w-]+\s+(?:boolean\s+)?gate\W*(?:yes\s*/\s*no|if\s+yes)\b"
    r")",
    re.IGNORECASE,
)

# Generic requests about the conversation or the shape of the answer. These are
# grammatical classes, not a list of evaluator sentences.
_PROCESS_PREAMBLE_RE = re.compile(
    r"^\W*(?:before|after|while|when)\s+(?:we|you|i)\s+"
    r"(?:build|compile|finali[sz]e|continue|proceed|start|finish|approve)\b"
    r"|^\W*(?:for\s+now|at\s+this\s+point|in\s+this\s+reply)\b",
    re.IGNORECASE,
)
_GENERIC_SCOPE_CONTROL_RE = re.compile(
    r"^\W*(?:no|without|do\s+not|don'?t|never)\s+"
    r"(?:any\s+|more\s+|additional\s+|extra\s+|new\s+)?"
    r"(?:questions?|details?|complexity|explanations?|formulas?|indicators?|"
    r"filters?|conditions?|rules?|mechanics?|logic|assumptions?)\W*$",
    re.IGNORECASE,
)
# Requests aimed at the assistant's explanation or presentation are dialogue even
# when they quote operators, formulas, timeframes, or thresholds. Their technical
# nouns are the subject to explain, not new market rules. This grammatical gate runs
# before the measurable-condition shortcut so "tell me what >= means numerically"
# cannot become an executable condition or an unsupported capability.
_ANSWER_SHAPE_REQUEST_RE = re.compile(
    r"\b(?:tell|show|explain|define|map|summari[sz]e|restate|recap|confirm|"
    r"reply|respond|paste|display|describe|clarify)\b"
    r"[^.!?]{0,100}\b(?:meaning|definition|mapping|explanation|summary|recap|"
    r"answer|reply|output|logic|formula|math|operator|comparison|rules?)\b|"
    r"\bmake\s+sure\s+(?:that\s+)?you\b[^.!?]{0,100}\b"
    r"(?:map|explain|define|show|keep|preserve)\b|"
    r"\bwhat\s+(?:does|do)\b[^.!?]{0,80}\bmean\b",
    re.IGNORECASE,
)
_EXPLICIT_MECHANIC_INTENT_RE = re.compile(
    r"^\W*(?:monitor|watch|track|detect|find|scan|screen|require|check|"
    r"alert(?:\s+me)?|notify(?:\s+me)?)\b"
    r"|^\W*(?:when|whenever|if)\s+(?:the\s+)?"
    r"(?:price|market|candle|close|open|high|low|volume|rsi|ema|sma|vwap)\b"
    r"|^\W*(?:condition|rule|trigger|filter)\s*(?:is|:|=)\s*\S+",
    re.IGNORECASE,
)

#: Rolling back to an earlier value is a state operation, not a market mechanic.
#: The strategy-state patch log imports this so both readers share one vocabulary.
REVERSION_RE = re.compile(
    r"\b("
    r"go\s+back(?:\s+to)?|revert|undo|scratch\s+that|cancel\s+that|"
    r"as\s+(?:it\s+)?(?:was|before)|previous\s+(?:value|setting|one)|"
    r"restore\s+(?:the\s+)?(?:previous|original|old)|"
    r"never\s*mind(?:\s+that)?|forget\s+(?:that|the\s+last)|"
    r"roll(?:ing|s)?\s*back|rolled\s+back|"
    r"back\s+to\s+(?:the\s+)?(?:previous|original|old)"
    r")\b",
    re.IGNORECASE,
)

#: Vocabulary that makes a fragment about the market at all. Capability resolution
#: answers "which registered mechanic is this?", which is only a meaningful question
#: for text naming a market quantity or behaviour.
#:
#: The boundaries are lookarounds, not ``\b``. Underscore is a word character, so
#: ``\bclose\b`` does not match ``latest_5m_close`` — and traders write their formulas
#: exactly that way, so a real close-to-close rule read as naming nothing about the
#: market. Same boundary defect as ``"rsi" in text`` matching ``ve``**``rsi``**``on``:
#: an alphabetic lookaround is the form that holds for identifiers and for Arabic.
_MARKET_VOCABULARY_RE = re.compile(
    r"(?<![a-z])(?:rsi|mfi|macd|roc|ema|sma|hma|wma|vwap|atr|adx|bollinger|stochastic|"
    r"moving\s+average|price|close|closes|open|opens|high|low|candle|candles|bar|bars|"
    r"volume|liquidity|spread|depth|order\s*book|trend|breakout|breakdown|"
    r"support|resistance|swing|pullback|retest|reversal|momentum|divergence|"
    r"cross(?:es|ing|ed)?|sweep|swept|wick|body|doji|hammer|engulfing|"
    # Inflections count: a rule about `pumping` is as much about the market as one
    # about a `pump`, and a word-boundary match on the stem alone would miss it.
    r"pumps?|pumped|pumping|dumps?|dumped|dumping|fades?|faded|fading|"
    r"rally|rallies|sell-?off|gains?|drops?|falls?|rises?|"
    r"percent|percentage|moves?|market|markets|ath|all-?time\s+high|"
    # Market objects a trader names when stating what to watch. They carry no
    # measurement on their own, but wording that mentions them is *about* the
    # market, which is all this gate decides.
    r"chart|charts|level|levels|floor|ceiling|zone|zones|range|structure|signal|signals|"
    r"entry|exit|stop|target|position|trade|trades|setup|watchlist|pattern|patterns|"
    r"candidate|candidates|"
    r"coin|coins|token|tokens|asset|assets|pair|pairs|ticker|tickers|"
    r"rvol|cvd|liquidation\s+heatmap|whale\s+wallets?|fear\s+and\s+greed|"
    r"news\s+sentiment)(?![a-z])"
    # Bare `change` means "modify" far more often than it names a market quantity:
    # "if I change my mind" is not a trading instruction. Only its market senses
    # count as market vocabulary.
    r"|(?<![a-z])(?:percent|percentage|pct|price|close|%)\s*_?\s*change(?![a-z])"
    r"|%\s*change(?![a-z])",
    re.IGNORECASE,
)


#: Tokens that, next to a percentage, mark the fragment as explicit arithmetic the
#: formula compiler owns rather than a named mechanic the resolver must recognise.
#: Deliberately narrow — this is not the movement vocabulary, and widening it to that
#: pulled ordinary `bearish move of at least 1%` wording out of capability resolution.
#:
#: `up` and `down` carry the phrasal-verb guard from the movement module, because
#: `set up a scanner for coins that dropped 5%` was being read as a formula on the
#: strength of the `up` inside `set up`.
_FORMULA_TOKEN_RE = re.compile(
    rf"{PHRASAL_PARTICLE_GUARD}(?<![a-z])(?:up|down)(?![a-z])"
    r"|(?<![a-z])(?:gain(?:s|ed)?|increase[sd]?|decrease[sd]?|"
    r"drop(?:s|ped)?|rise[sn]?|fall(?:s|en)?|decline[sd]?|move(?:s|d|ment)?|"
    r"open|close|high|low|percent_move|percentage_change)(?![a-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ClassifiedFragment:
    """One user-authored fragment with its deterministic interpretation."""

    text: str
    kind: FragmentKind
    symbols: tuple[str, ...] = ()
    excluded_symbols: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    direction: StrategyDirection | None = None
    comparator: Comparator | None = None
    threshold: float | None = None
    threshold_is_percent: bool = False
    is_sweep: bool = False

    @property
    def enters_capability_resolution(self) -> bool:
        return self.category == "TRADING_MECHANIC"

    @property
    def contributes_strategy_state(self) -> bool:
        """Whether parsed values from this fragment may mutate the draft."""

        return self.kind not in {
            "approval",
            "approval_policy",
            "product_policy",
            "conversation_control",
            "decision_request",
            "reversion",
            "conversational",
        }

    @property
    def category(self) -> FragmentCategory:
        """Canonical pre-resolution category required by the setup compiler."""

        if self.kind == "approval":
            return "APPROVAL"
        if self.kind == "approval_policy":
            return "APPROVAL_INSTRUCTION"
        if self.kind == "product_policy":
            return "PRODUCT_POLICY"
        if self.kind == "conversation_control":
            return "CONVERSATION_CONTROL"
        if self.kind == "decision_request":
            return "DECISION_REQUEST"
        if self.kind == "reversion":
            return "REVERSION"
        if self.kind == "conversational":
            return "CONVERSATIONAL_TEXT"
        if self.excluded_symbols:
            return "EXCLUDE"
        if self.kind == "symbol":
            lowered = self.text.casefold()
            return "INCLUDE" if re.search(r"\b(?:include|add|watch|only)\b", lowered) else "SYMBOL"
        if self.kind == "exclusion":
            return "EXCLUDE"
        if self.kind == "timeframe":
            return "TIMEFRAME"
        if self.kind == "direction":
            return "DIRECTION"
        if self.kind == "operator":
            return "OPERATOR"
        if self.kind == "threshold":
            return "THRESHOLD"
        if (
            self.threshold is not None
            and _FORMULA_TOKEN_RE.search(self.text)
            and (
                self.threshold_is_percent
                or "%" in self.text
                or re.search(r"\b(?:percent|percentage|pct)\b", self.text, re.IGNORECASE)
            )
        ):
            return "FORMULA"
        return "TRADING_MECHANIC"


@dataclass(frozen=True, slots=True)
class TurnFragmentReport:
    """Deterministic reading of a whole turn, before any model call."""

    text: str
    fragments: tuple[ClassifiedFragment, ...] = field(default_factory=tuple)
    #: Set when the turn as a whole grants approval even though no single fragment
    #: does (``yes, proceed`` splits into two individually neutral fragments).
    turn_approval: bool = False

    @property
    def trading_conditions(self) -> tuple[ClassifiedFragment, ...]:
        return tuple(item for item in self.fragments if item.enters_capability_resolution)

    @property
    def symbols(self) -> tuple[str, ...]:
        return _unique(
            symbol
            for item in self.fragments
            if item.contributes_strategy_state
            for symbol in item.symbols
        )

    @property
    def excluded_symbols(self) -> tuple[str, ...]:
        return _unique(
            symbol
            for item in self.fragments
            if item.contributes_strategy_state
            for symbol in item.excluded_symbols
        )

    @property
    def timeframes(self) -> tuple[str, ...]:
        return _unique(
            timeframe
            for item in self.fragments
            if item.contributes_strategy_state
            for timeframe in item.timeframes
        )

    @property
    def direction(self) -> StrategyDirection | None:
        for item in reversed(self.fragments):
            if (
                item.contributes_strategy_state
                and item.direction is not None
                and item.threshold_is_percent
            ):
                return item.direction
        for item in reversed(self.fragments):
            if item.contributes_strategy_state and item.direction is not None:
                return item.direction
        return None

    @property
    def comparator(self) -> Comparator | None:
        for item in reversed(self.fragments):
            if (
                item.contributes_strategy_state
                and item.comparator is not None
                and item.threshold_is_percent
            ):
                return item.comparator
        for item in reversed(self.fragments):
            if (
                item.contributes_strategy_state
                and item.comparator is not None
                and item.threshold is not None
            ):
                return item.comparator
        for item in reversed(self.fragments):
            if item.contributes_strategy_state and item.comparator is not None:
                return item.comparator
        return None

    @property
    def threshold(self) -> float | None:
        for item in reversed(self.fragments):
            if (
                item.contributes_strategy_state
                and item.threshold is not None
                and item.threshold_is_percent
            ):
                return item.threshold
        for item in reversed(self.fragments):
            if item.contributes_strategy_state and item.threshold is not None:
                return item.threshold
        return None

    @property
    def is_sweep(self) -> bool:
        return any(item.is_sweep for item in self.fragments if item.contributes_strategy_state)

    @property
    def is_approval(self) -> bool:
        return self.turn_approval or any(item.kind == "approval" for item in self.fragments)

    @property
    def is_conversational_only(self) -> bool:
        return bool(self.fragments) and all(
            item.kind == "conversational" for item in self.fragments
        )


def normalize_symbol(base: str, quote: str) -> str:
    return f"{base.upper()}{quote.upper()}"


def split_symbol(symbol: str) -> tuple[str, str] | None:
    """Split a canonical ``BASEQUOTE`` symbol into ``(base, quote)``.

    Longest quote first, so ``BTCUSDT`` splits as ``BTC``/``USDT`` rather than
    ``BTCUSD``/``T`` or ``BTCUSDT``/``USD``.
    """
    upper = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    for quote in sorted(_SEPARATED_QUOTES, key=len, reverse=True):
        if upper.endswith(quote) and len(upper) > len(quote):
            return upper[: -len(quote)], quote
    return None


def to_pair(symbol: str) -> str:
    """Render a symbol in the platform's canonical ``BASE/QUOTE`` form.

    Accepts every spelling a trader might use and is idempotent, so an already
    correct ``BTC/USDT`` is returned unchanged instead of becoming
    ``BTCUSDT/USDT``.
    """
    parts = split_symbol(symbol)
    if parts is None:
        return symbol.upper()
    base, quote = parts
    if base.endswith(quote):
        return symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    return f"{base}/{quote}"


def extract_symbols(text: str) -> tuple[str, ...]:
    """Return every market symbol named in ``text``, in canonical ``BASEQUOTE`` form."""
    symbols: list[str] = []
    for match in _SYMBOL_RE.finditer(text.upper()):
        base = match.group("base") or match.group("base2")
        quote = match.group("quote") or match.group("quote2")
        if base.endswith(quote):
            # Reject malformed repeated quotes such as BTCUSDTUSDT. Treating the
            # first USDT as part of the base created executable phantom markets.
            continue
        symbols.append(normalize_symbol(base, quote))
    return _unique(symbols)


def extract_timeframes(text: str) -> tuple[str, ...]:
    """Return every supported timeframe named in ``text``, canonicalised (``60m`` -> ``1h``)."""
    return _unique(canonical for _start, _end, canonical in _timeframe_spans(text))


SemanticTimeframeRole = Literal["trigger", "context", "confirmation", "reference"]

_SEMANTIC_TIMEFRAME_ROLE_TERMS: dict[SemanticTimeframeRole, tuple[str, ...]] = {
    "trigger": (
        "trigger",
        "entry",
        "signal",
        "fire",
        "alert",
        "setup candle",
        "إشارة",
        "تفعيل",
        "trigger",
        "eshara",
    ),
    "context": (
        "context",
        "backdrop",
        "regime",
        "higher timeframe",
        "htf",
        "macro",
        "سياق",
        "خلفية",
        "seya2",
        "sya2",
    ),
    "confirmation": (
        "confirmation",
        "confirm",
        "confirmed by",
        "independently confirm",
        "تأكيد",
        "يأكد",
        "ta2keed",
        "confirmation",
    ),
    "reference": (
        "reference",
        "baseline",
        "measured from",
        "compare with",
        "relative to",
        "مرجع",
        "قياس",
        "marga3",
        "marja3",
    ),
}


def timeframe_role_is_explicit(
    text: str,
    timeframe: str,
    role: SemanticTimeframeRole,
) -> bool:
    """Verify one AI-proposed role without inferring it from order or relative size.

    A role marker must grammatically bind to the canonical timeframe in the same
    clause.  The sole trigger exception is an otherwise unambiguous one-timeframe
    standard rule (``on 15m``); no context/confirmation/reference role is invented.
    """

    collapsed = _collapse(text)
    mentions = _timeframe_spans(collapsed)
    wanted = [(start, end) for start, end, value in mentions if value == timeframe]
    if not wanted:
        return False
    role_terms = _SEMANTIC_TIMEFRAME_ROLE_TERMS[role]
    all_role_terms = tuple(
        term for terms in _SEMANTIC_TIMEFRAME_ROLE_TERMS.values() for term in terms
    )
    for clause in _split_fragments(collapsed):
        clause_mentions = _timeframe_spans(clause)
        if timeframe not in {value for _start, _end, value in clause_mentions}:
            continue
        lowered = clause.casefold()
        term_pattern = "|".join(
            sorted((re.escape(term) for term in role_terms), key=len, reverse=True)
        )
        timeframe_patterns = [
            re.escape(clause[start:end])
            for start, end, value in clause_mentions
            if value == timeframe
        ]
        for timeframe_pattern in timeframe_patterns:
            # Traders very commonly name the rendered market view between the
            # timeframe and its purpose: ``the 4h chart provides directional
            # context`` or ``the 5m candle supplies the trigger``.  Those words do
            # not change the role, but the previous grammar treated them as a gap and
            # rejected an otherwise exact, same-clause binding.  Keep the role and
            # timeframe in the same clause; this merely accepts neutral view nouns
            # and linking verbs between them.
            view_noun = r"(?:timeframe|chart|candle(?:s)?|interval|period)?"
            role_modifier = r"(?:directional\s+|market\s+|trend\s+)?"
            linking_verb = r"(?:is|as|for|used\s+as|provides?|supplies?|sets?|serves\s+as)?"
            if re.search(
                rf"(?:{term_pattern})(?:\s+timeframe)?\s*(?::|=|is|on|at|of|as)?\s*"
                rf"(?:the\s+)?{timeframe_pattern}(?!\w)",
                lowered,
            ) or re.search(
                rf"(?<!\w){timeframe_pattern}\s*{view_noun}\s*{linking_verb}\s*"
                rf"(?:the\s+)?{role_modifier}(?:{term_pattern})",
                lowered,
            ):
                return True
    if role != "trigger" or len(mentions) != 1:
        return False
    lowered = collapsed.casefold()
    if any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered)
        for term in all_role_terms
        if term not in _SEMANTIC_TIMEFRAME_ROLE_TERMS["trigger"]
    ):
        return False
    timeframe_token = r"(?:\d+\s*(?:m|h|d|w)|hourly|daily|weekly|four[- ]hour)"
    formula_token = (
        r"(?:open[- ]to[- ]close|close[- ]to[- ]close|"
        r"high[- ]to[- ]low|low[- ]to[- ]high|move)"
    )
    trigger_pattern = (
        rf"\b(?:on|at|using|use)\s+(?:the\s+)?{timeframe_token}\b"
        rf"|\bwhen\s+(?:the\s+)?{timeframe_token}\b"
        rf"|\b{timeframe_token}\s+candles?\b"
        rf"|\b{timeframe_token}\s+{formula_token}\b"
    )
    return bool(re.search(trigger_pattern, lowered))


#: Words that mark the timeframe a rule actually fires on.
_TRIGGER_ROLE_TERMS = (
    "trigger",
    "triggers",
    "triggered",
    "entry",
    "entries",
    "signal",
    "signals",
    "execution",
    "execute",
    "fire",
    "fires",
    "confirm",
    "confirmation",
    "confirms",
    "alert",
    "alerts",
    "trade",
    "setup candle",
)
#: Words that mark a timeframe used only to select or filter, never to fire.
_CONTEXT_ROLE_TERMS = (
    "context",
    "bias",
    "backdrop",
    "regime",
    "structure",
    "filter",
    "filters",
    "higher timeframe",
    "htf",
    "macro",
    "trend",
    "direction from",
    "selection",
    "screening",
    "background",
    "\u0633\u064a\u0627\u0642",
    "\u0641\u0644\u062a\u0631",
    "\u062a\u0635\u0641\u064a\u0629",
)


#: The two roles are each other's default: a timeframe a lone marker did not claim
#: takes the other one.
_OPPOSITE_ROLE = {"trigger": "context", "context": "trigger"}


@dataclass(frozen=True, slots=True)
class TimeframeRoles:
    """Which timeframe fires a rule, and which ones only give it context."""

    trigger: str | None = None
    context: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.trigger is not None or bool(self.context)


def extract_timeframe_roles(text: str) -> TimeframeRoles:
    """Separate the trigger timeframe from context timeframes.

    ``4h context and a 1h trigger`` must not collapse to whichever timeframe was
    written first. The trigger is the timeframe conditions evaluate on; context
    timeframes only select or filter and belong in supporting timeframes.
    """
    trigger: str | None = None
    context: list[str] = []

    # Roles are scoped to a clause. `bias from the daily, entry on the 4h` binds each
    # role to the timeframe beside it; measuring raw distance across the comma would
    # let `entry` capture `daily` because it happens to sit two characters away.
    for clause in _split_fragments(_collapse(text)):
        markers = _role_markers(clause)
        if not markers:
            continue
        spans = _timeframe_spans(clause)
        kinds = {role for _position, role in markers}
        # One marker word cannot give its role to every timeframe in the clause. It
        # claims the timeframe beside it; the others take the opposite role. This ran
        # for a lone `trigger` marker but not for a lone `context` one, so `using the
        # 4h as context when the 15m candle rises` made *both* timeframes context and
        # left the rule with no trigger at all.
        lone_role: str | None = next(iter(kinds)) if len(kinds) == 1 else None
        lone_span: tuple[int, int, str] | None = None
        if lone_role is not None and len(spans) > 1:
            marker_positions = [position for position, role in markers if role == lone_role]
            lone_span = min(
                spans,
                key=lambda span: min(
                    _distance(position, span[0], span[1]) for position in marker_positions
                ),
            )
        for start, end, canonical in spans:
            explicit_role = _explicit_timeframe_role(clause, start=start, end=end)
            if explicit_role is not None:
                role = explicit_role
            elif lone_span is not None and lone_role is not None:
                role = (
                    lone_role if (start, end, canonical) == lone_span else _OPPOSITE_ROLE[lone_role]
                )
            elif lone_role is not None:
                role = lone_role
            else:
                nearest = min(markers, key=lambda item: _distance(item[0], start, end))
                if _distance(nearest[0], start, end) > _ROLE_PROXIMITY_LIMIT:
                    continue
                role = nearest[1]
            if role == "trigger":
                trigger = trigger or canonical
            elif canonical not in context:
                context.append(canonical)

    return TimeframeRoles(
        trigger=trigger,
        context=tuple(tf for tf in context if tf != trigger),
    )


def _role_markers(text: str) -> list[tuple[int, str]]:
    lowered = text.casefold()
    markers: list[tuple[int, str]] = []
    for terms, role in ((_TRIGGER_ROLE_TERMS, "trigger"), (_CONTEXT_ROLE_TERMS, "context")):
        for term in terms:
            for hit in re.finditer(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered):
                markers.append((hit.start(), role))
    return markers


def _explicit_timeframe_role(
    text: str,
    *,
    start: int,
    end: int,
) -> str | None:
    """Read field-label and postfix role syntax before proximity heuristics."""

    prefix = text[max(0, start - 56) : start].casefold()
    suffix = text[end : min(len(text), end + 40)].casefold()
    # A postfix role owns this timeframe unless it is followed by a colon, which
    # starts the next labelled field (`Context: 15m - Trigger: 1m`).
    after = re.match(
        r"^[\W_]*(?:is\s+|as\s+|used\s+(?:only\s+)?as\s+)?"
        r"(?:[\u0600-\u06ff]+\s+){0,3}"
        r"(context|bias|filter|trigger|entry|signal|execution)\b",
        suffix,
    )
    starts_next_field = after is not None and re.match(
        r"^[\s_]*(?:timeframe\s*)?:",
        suffix[after.end() :],
    )
    if after and not starts_next_field:
        return "context" if after.group(1) in {"context", "bias", "filter"} else "trigger"
    before = re.search(
        r"(?:context|bias|filter|trigger|entry|signal|execution)"
        r"(?:[\s_]+timeframe)?(?:\s+[\u0600-\u06ff]+){0,3}"
        r"\s*[:=]?\s*[*_`]*\s*$",
        prefix,
    )
    if before:
        term = before.group(0)
        return "context" if re.search(r"context|bias|filter", term) else "trigger"
    return None


#: How far a role word may sit from a timeframe and still describe it.
_ROLE_PROXIMITY_LIMIT = 40

#: `daily`/`weekly` name timeframes without a digit, so the numeric pattern misses them.
_WORD_TIMEFRAMES = {
    **WORD_TIMEFRAME_ALIASES,
    "four-hour": WORD_TIMEFRAME_ALIASES["four hour"],
}


def _timeframe_spans(text: str) -> list[tuple[int, int, str]]:
    """Every timeframe mention as ``(start, end, canonical)``, in order."""
    spans: list[tuple[int, int, str]] = []
    for match in _TIMEFRAME_RE.finditer(text):
        canonical = _canonical_timeframe(match.group(1), match.group(2))
        if canonical is not None:
            spans.append((match.start(), match.end(), canonical))
    lowered = text.casefold()
    for word, canonical in _WORD_TIMEFRAMES.items():
        for hit in re.finditer(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            spans.append((hit.start(), hit.end(), canonical))
    return sorted(spans)


def _distance(position: int, start: int, end: int) -> int:
    if start <= position <= end:
        return 0
    return start - position if position < start else position - end


def detect_direction(text: str) -> StrategyDirection | None:
    lowered = f" {_collapse(text).casefold()} "
    for term, direction in _DIRECTION_TERMS.items():
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered):
            return direction
    return None


def detect_sweep(text: str) -> bool:
    lowered = _collapse(text).casefold()
    return any(
        re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered) for term in _SWEEP_TERMS
    )


def detect_threshold(text: str) -> tuple[float | None, bool]:
    """Return ``(value, is_percent)`` for the first numeric threshold in ``text``."""
    percent = _PERCENT_RE.search(text)
    if percent is not None:
        number = _NUMBER_RE.search(percent.group(0))
        if number is not None:
            return float(number.group(0)), True
    # A bare timeframe such as `15m` is not a threshold.
    without_timeframes = _TIMEFRAME_RE.sub(" ", text)
    for number in _NUMBER_RE.finditer(without_timeframes):
        # Ordered-list labels describe presentation, not thresholds.
        before_number = without_timeframes[: number.start()]
        after_number = without_timeframes[number.end() :]
        if not before_number.strip() and re.match(r"\s*[\).:-]\s+\S", after_number):
            continue
        # Formula scale factors and indexed operand names are not thresholds:
        # `((end-start)/start) * 100`, `C0`, `Cprev1`, and `open_1m`.
        previous = without_timeframes[number.start() - 1 : number.start()]
        following = without_timeframes[number.end() : number.end() + 1]
        prefix = without_timeframes[: number.start()].rstrip()
        if previous in {"*", "x", "X", "×"} or prefix.endswith(("*", "x", "X", "×")):
            continue
        if (previous and (previous.isalnum() or previous == "_")) or (
            following and (following.isalnum() or following == "_")
        ):
            continue
        return float(number.group(0)), False
    return None, False


def is_approval_instruction(text: str) -> bool:
    """Detect an explicit human approval, ignoring requests *for* approval gating.

    ``APPROVED`` approves. ``Do not activate until I say approved`` does not, and
    neither does ``does this need my approval?``.
    """
    collapsed = _collapse(text)
    lowered = _plain_quotes(collapsed).casefold()
    if not lowered:
        return False
    if any(re.search(pattern, lowered) for pattern in _APPROVAL_NEGATIONS):
        return False
    return any(re.search(pattern, lowered) for pattern in _APPROVAL_PATTERNS)


def is_conversational(text: str) -> bool:
    collapsed = _collapse(text)
    if not collapsed:
        return True
    if _CONVERSATIONAL_RE.match(collapsed):
        return True
    # A question that names no market mechanic is meta-conversation, not a condition.
    return bool(_META_QUESTION_RE.match(collapsed)) and not _has_residual_content(
        _residual_vocabulary(
            collapsed,
            symbols=extract_symbols(collapsed),
            timeframes=extract_timeframes(collapsed),
        )
    )


def classify_fragment(text: str) -> ClassifiedFragment:
    """Classify a single fragment. Only ``trading_condition`` reaches the resolver."""
    collapsed = _collapse(text)
    symbols = extract_symbols(collapsed)
    timeframes = extract_timeframes(collapsed)
    direction = detect_direction(collapsed)
    comparator = detect_comparator(collapsed)
    threshold, is_percent = detect_threshold(collapsed)
    sweep = detect_sweep(collapsed)
    # The direct extractor handles formatting and wider grammatical context (for
    # example `**NOT** LTCUSDT`) that the compact per-symbol reader intentionally
    # does not. Both are deterministic and source-bound.
    excluded = _unique(
        (
            *_excluded_symbols(collapsed, symbols),
            *extract_explicit_exclusions(collapsed),
        )
    )
    included = tuple(symbol for symbol in symbols if symbol not in set(excluded))

    def build(kind: FragmentKind, *, keeps_universe: bool = True) -> ClassifiedFragment:
        return ClassifiedFragment(
            text=collapsed,
            kind=kind,
            symbols=included if keeps_universe else (),
            excluded_symbols=excluded if keeps_universe else (),
            timeframes=timeframes,
            direction=direction,
            comparator=comparator,
            threshold=threshold,
            threshold_is_percent=is_percent,
            is_sweep=sweep,
        )

    if is_approval_instruction(collapsed):
        return build("approval")

    # Every pattern below reads `probe`, with typographic quotes normalised and the
    # leading discourse opener removed. A fragment's role does not change because it
    # begins with `And`, `Then` or `Alright`, but the `^`-anchored dialogue patterns
    # all stopped matching when one was present.
    probe = _DISCOURSE_PREFIX_RE.sub("", _plain_quotes(collapsed)).strip() or _plain_quotes(
        collapsed
    )
    if _NEGATED_METADATA_RE.search(probe):
        return build("conversation_control", keeps_universe=False)
    states_condition = _states_measurable_condition(
        probe, threshold=threshold, comparator=comparator
    )

    # Policy about labelling never edits the universe. "Don't attach any religious
    # status to LTCUSDT" names LTCUSDT to protect it, and reading the negation as an
    # exclusion would drop the one asset the trader asked for.
    #
    # The branch claims the whole fragment, so it must only fire when the fragment is
    # *about* the policy. `monitor every forming head & shoulders on halal coins` says
    # which coins to watch and states a mechanic in the same breath; classifying all
    # of it as policy threw the mechanic away, and the trader's pattern was never
    # resolved. A policy word qualifying a market noun restricts the universe — which
    # this platform already enforces — and leaves the rule intact.
    if _PRODUCT_POLICY_RE.search(probe) and not _POLICY_QUALIFIES_UNIVERSE_RE.search(probe):
        return build("product_policy", keeps_universe=False)

    # Approval wording that does not grant approval still belongs to the approval
    # lifecycle, not to capability resolution.
    if (
        _APPROVAL_POLICY_RE.search(probe) or _APPROVAL_BINDING_RE.search(probe)
    ) and not states_condition:
        return build("approval_policy", keeps_universe=False)
    if re.search(
        r"\b(?:reply|type|write|say)\b[^.!?]{0,40}\b"
        r"(?:confirm|approve|approved|go[-\s]?ahead)\b",
        probe,
        re.IGNORECASE,
    ):
        return build("approval_policy", keeps_universe=False)

    if REVERSION_RE.search(probe) and not states_condition:
        return build("reversion", keeps_universe=False)
    if (
        _MARKET_FACT_REQUEST_RE.search(collapsed)
        and not symbols
        and not timeframes
        and direction is None
        and comparator is None
        and threshold is None
        and not sweep
    ):
        return build("conversational")
    if (
        _META_DISCOURSE_RE.search(collapsed)
        and not symbols
        and not timeframes
        and direction is None
        and comparator is None
        and threshold is None
        and not sweep
        and not re.search(
            r"\b(?:rsi|ema|sma|vwap|volume|candle|price|swing|breakout|"
            r"support|resistance|liquidity|percent(?:age)?_?change)\b",
            collapsed,
            re.IGNORECASE,
        )
    ):
        return build("conversational")
    if is_conversational(collapsed):
        return build("conversational")

    if _ANSWER_SHAPE_REQUEST_RE.search(probe):
        return build("conversation_control", keeps_universe=False)

    # A question offering alternatives is not an authored rule even when its
    # examples contain percentages or operators.
    if ("?" in collapsed or "\u061f" in collapsed) and _DECISION_REQUEST_RE.search(probe):
        return build("decision_request", keeps_universe=False)

    if (
        threshold is not None
        and _FORMULA_TOKEN_RE.search(collapsed)
        and (
            is_percent
            or "%" in collapsed
            or re.search(r"\b(?:percent|percentage|pct)\b", collapsed, re.IGNORECASE)
        )
    ):
        # Core arithmetic is compiled directly. It is intentionally represented by
        # the trading-condition kind so ``category`` can expose FORMULA while
        # ``enters_capability_resolution`` remains false.
        return build("trading_condition")

    def by_carried_state(*, empty_kind: FragmentKind = "conversational") -> ClassifiedFragment:
        """Classify by whatever the fragment still carries.

        Text that is not a mechanic can still hold real configuration: `add SOLUSDT`
        names a symbol, `**Context:** 1h = …` names a timeframe. Falling straight to
        `conversational` discarded that, silently shrinking the watch list or losing
        the timeframe the trader stated. Only text carrying nothing is discarded.
        """
        if excluded:
            return build("exclusion")
        if symbols:
            return build("symbol")
        if timeframes:
            return build("timeframe")
        if direction is not None:
            return build("direction")
        if comparator is not None:
            return build("operator")
        if threshold is not None:
            return build("threshold")
        return build(empty_kind, keeps_universe=False)

    if _CANONICAL_FIELD_ASSIGNMENT_RE.search(probe):
        return by_carried_state()

    residual = _residual_vocabulary(collapsed, symbols=symbols, timeframes=timeframes)

    # A fragment that only names markets, timings, sides, comparisons or numbers is
    # configuration for the strategy, not a mechanic the resolver has to recognise.
    if not _has_residual_content(residual):
        return by_carried_state()

    # "Go ahead and run it" asks for the next step in the conversation, not for
    # anything to be monitored.
    if _ACTION_REQUEST_RE.search(probe) and not states_condition:
        return by_carried_state(empty_kind="conversation_control")

    if (
        _DIALOGUE_STATEMENT_RE.search(probe)
        or _DIALOGUE_STATEMENT_RE.search(_plain_quotes(collapsed))
        or _PROCESS_PREAMBLE_RE.search(probe)
        or _GENERIC_SCOPE_CONTROL_RE.search(probe)
    ) and not states_condition:
        return by_carried_state(empty_kind="conversation_control")

    # An open choice ("close-to-close vs high/low") records a decision the trader has
    # not made. Resolving it picks one reading for them, which is how an unrequested
    # condition enters a compiled strategy.
    if _DECISION_REQUEST_RE.search(probe):
        return build("decision_request", keeps_universe=False)

    # Instructions about how to answer are not instructions about the market.
    if (
        _CONVERSATION_CONTROL_RE.search(probe)
        or _CONVERSATION_CONTROL_RE.search(_plain_quotes(collapsed))
    ) and not states_condition:
        return build("conversation_control", keeps_universe=False)

    # An instruction aimed at the assistant's own reading or output stays a dialogue
    # instruction even when it quotes market words: `don't interpret it as "Strong
    # Swing High"` names a swing high but asks for nothing to be monitored.
    if _META_INSTRUCTION_RE.search(probe) and not states_condition:
        return build("conversation_control", keeps_universe=False)

    # An artifact quoted back, or a blank never filled in. Both are the shape of a form
    # rather than an instruction. Placed after the dialogue checks so a labelled note
    # (`quick check: if I change my mind`) keeps its more specific classification.
    if (_FIELD_ECHO_RE.search(probe) or _PLACEHOLDER_RE.search(probe)) and not states_condition:
        return by_carried_state()

    # A piece of a sentence is not an instruction. Splitting a long message chopped
    # `Computed move % = [(C0 - L1) / C0] x 100` into `Computed move % =`, `L1` and
    # `move %`, and each piece was then reported as its own unconverted instruction.
    if not _is_whole_instruction(collapsed, threshold=threshold):
        return by_carried_state()

    # Unrecognised wording stays a trading condition on purpose. `raid the weekly
    # floor` names no word on any list, yet it is exactly what the capability registry
    # exists to interpret — and what an approved alias later resolves. A positive
    # "must name known market vocabulary" gate was tried here and reverted: it turned
    # every not-yet-known mechanic into ordinary conversation, which is the silent
    # drop this module exists to prevent.
    #
    # What does *not* reach the resolver is decided above, by what the fragment is —
    # approval traffic, product policy, a reversion, an open decision, an instruction
    # about the answer, a request to act, or debris — never by whether its words
    # happen to appear in a vocabulary.
    if names_market_mechanic(probe) or _EXPLICIT_MECHANIC_INTENT_RE.search(probe):
        return build("trading_condition")
    return by_carried_state()


#: A fragment ending in one of these was cut before its object. It cannot be acted on
#: and cannot be clarified — the meaning is in the part that was cut away.
_DANGLING_TAIL_RE = re.compile(
    r"(?:[=+\-/*×÷:,(\[]|\b(?:with|of|and|or|for|to|by|from|than|is|are|the|a|an|"
    r"per|into|onto|using|via)\b)\s*$",
    re.IGNORECASE,
)


def _is_whole_instruction(text: str, *, threshold: float | None) -> bool:
    """Whether the fragment is complete enough to be an instruction at all.

    Short fragments are kept when they still state something measurable — `volume 2x`
    is a real requirement — so this removes sentence debris without silencing terse
    but complete wording.
    """
    if _DANGLING_TAIL_RE.search(text):
        return False
    # A leading `and`/`or`/`then` joins this clause to the previous one and carries no
    # meaning itself, so the clause is judged on what follows it.
    body = _CONTINUATION_OPENER_RE.sub("", text, count=1).strip()
    words = [word for word in re.findall(r"[^\W\d_]+", body, re.UNICODE) if len(word) > 1]
    # A short fragment with an unclosed bracket or quote is the tail of a parenthetical
    # (`not a big dump)`). Long ones are left alone: the splitter cuts real clauses
    # mid-parenthesis too, and rejecting those dropped rules the trader had stated.
    if len(words) < 6 and not _brackets_are_balanced(text):
        return False
    if len(words) >= 3:
        return True
    names_mechanic = names_market_mechanic(body)
    starts_mechanic_command = re.match(
        r"^\W*(?:monitor|watch|track|detect|find|scan|screen|use|apply|require|"
        r"check|alert(?:\s+me)?(?:\s+when)?|notify(?:\s+me)?(?:\s+when)?)\s+\S+",
        body,
        re.IGNORECASE,
    )
    if starts_mechanic_command:
        return True
    # A distinctive registered mechanic can be one word (`CVD`, `RVOL`, `VWAP`).
    # The registry vocabulary, rather than string length, establishes its meaning.
    if words and names_mechanic:
        return True
    return threshold is not None and names_mechanic


@lru_cache(maxsize=1)
def _registry_vocabulary_re() -> re.Pattern[str]:
    """Every word the capability registry itself uses to name a mechanic.

    A hand-written word list always drifts behind the registry. The platform can
    build ``head_and_shoulders_neckline_break`` and advertises the exact phrase as an
    alias, but ``shoulders`` and ``neckline`` were not on the list, so the gate read
    that request as saying nothing about the market and refused to resolve it.

    The registry is the one owner of what this product can monitor, so it is also the
    one owner of the words that name a mechanic. Adding a capability now extends this
    vocabulary automatically; there is no second list to keep in step.

    Whole phrases are matched, never their component words. Splitting the aliases into
    words made `previous` and `failed` market vocabulary because some capability's
    description happens to contain them, which let ordinary English through.

    This widens :func:`names_market_mechanic` for callers that ask "is this wording
    about the market?". It is deliberately **not** used to decide what reaches the
    resolver — see the closing comment in :func:`classify_fragment`.
    """
    phrases: set[str] = set()
    for capability in all_capabilities():
        for phrase in normalized_phrases(capability):
            cleaned = " ".join(part for part in re.split(r"[^a-z0-9]+", phrase.casefold()) if part)
            if not cleaned:
                continue
            if " " in cleaned:
                phrases.add(cleaned)
            elif len(cleaned) >= 4 and cleaned not in _STRUCTURAL_FILLER:
                # A distinctive single term (`vwap`, `doji`, `supertrend`). Shorter ones
                # sit inside ordinary words too often to be read as a mention.
                phrases.add(cleaned)
    if not phrases:  # pragma: no cover - the registry is never empty in practice
        return re.compile(r"(?!)")
    alternation = "|".join(
        r"\s+".join(re.escape(word) for word in phrase.split())
        for phrase in sorted(phrases, key=len, reverse=True)
    )
    return re.compile(rf"(?<![a-z])(?:{alternation})(?![a-z])", re.IGNORECASE)


def names_market_mechanic(text: str) -> bool:
    """True when the text names a market quantity or behaviour.

    Capability resolution answers "which registered mechanic is this?". That question
    is only meaningful for wording that refers to the market at all, so this is the
    positive gate callers use before treating unrecognised wording as a *trading*
    instruction they failed to convert.

    Both vocabularies are consulted: the registry's own wording, plus general market
    language for mechanics the registry does not (yet) implement. Wording the platform
    cannot build still has to reach the resolver to be reported honestly.
    """
    collapsed = _collapse(text)
    return bool(
        _MARKET_VOCABULARY_RE.search(collapsed) or _registry_vocabulary_re().search(collapsed)
    )


def _states_measurable_condition(
    text: str,
    *,
    threshold: float | None,
    comparator: Comparator | None,
) -> bool:
    """True when the fragment asserts a measurable market requirement of its own.

    Used to stop the policy and conversation detectors from swallowing a fragment
    that also carries a real rule, as in "no auto-approval, and RSI must be under 30".
    """
    if threshold is not None and comparator is not None:
        return True
    return bool(_MARKET_VOCABULARY_RE.search(text)) and threshold is not None


def classify_turn(text: str) -> TurnFragmentReport:
    """Split a turn into fragments and classify each one deterministically."""
    collapsed = _collapse(text)
    if not collapsed:
        return TurnFragmentReport(text="", fragments=())
    parts = _split_fragments(collapsed)
    return TurnFragmentReport(
        text=collapsed,
        fragments=tuple(classify_fragment(part) for part in parts),
        turn_approval=is_approval_instruction(collapsed),
    )


def _split_fragments(text: str) -> tuple[str, ...]:
    # Presentation numbering is not a strategy threshold. Remove its label before
    # periods become sentence separators; the numbered content remains intact.
    protected = re.sub(
        r"(^|[\n;])\s*\d{1,2}[\).]\s+(?=\S)",
        r"\1",
        text,
        flags=re.MULTILINE,
    )
    protected = re.sub(r"(?<=\d)\.(?=\d)", "\x00", protected)
    # A conjunction inside a named metric is not a Boolean boundary. Protect the
    # grammatical "noun and noun + metric-type" shape before splitting clauses so
    # compound indicator names remain one source fragment.
    protected = re.sub(
        r"(?<!\w)([a-z][a-z0-9_-]*)\s+and\s+"
        r"([a-z][a-z0-9_-]*\s+(?:index|indicator|oscillator|metric|ratio|score))\b",
        r"\1 __hm_compound_and__ \2",
        protected,
        flags=re.IGNORECASE,
    )
    parts = re.split(r"[\n.;]+|,\s*|\s+\b(?:and|but|then|also|plus|except)\b\s+", protected)
    cleaned = tuple(
        " ".join(
            part.replace("\x00", ".").replace("__hm_compound_and__", "and").strip(" -:\t").split()
        )
        for part in parts
    )
    result = tuple(part for part in cleaned if part)
    return result or (text,)


def _excluded_symbols(text: str, symbols: tuple[str, ...]) -> tuple[str, ...]:
    """Return symbols this fragment removes from the universe.

    ``BTCUSDT only, exclude ETHUSDT`` splits into two fragments, so each call sees
    one clause. Within a clause, an exclusion marker before a symbol excludes it.
    """
    if not symbols:
        return ()
    lowered = text.casefold()
    upper = text.upper()
    spans: list[tuple[str, int, int]] = []
    for symbol in symbols:
        position = upper.find(symbol)
        length = len(symbol)
        if position < 0:
            # Separator form (BTC/USDT); fall back to the base asset position.
            base = symbol[:-4] if len(symbol) > 4 else symbol
            position = upper.find(base)
            length = len(base)
        spans.append((symbol, max(position, 0), length))
    spans.sort(key=lambda item: item[1])

    excluded: list[str] = []
    for index, (symbol, start, length) in enumerate(spans):
        previous_end = spans[index - 1][1] + spans[index - 1][2] if index else 0
        next_start = spans[index + 1][1] if index + 1 < len(spans) else len(lowered)
        # Each symbol owns only the words between it and its neighbours, so
        # `SOLUSDT only with BTCUSDT never included` excludes BTCUSDT alone.
        prefix = lowered[previous_end:start]
        suffix = lowered[start + length : next_start]
        explicit_include = bool(
            re.search(
                r"(?:include|included|symbol|watchlist)\s*[:=]\s*[*_`]*$",
                prefix,
            )
        )
        explicit_exclude = bool(
            re.search(
                r"(?:excluded?_?symbol|exclude|hard[-\s]*exclude|blocklist|"
                r"استبعاد|نستبعد|ممنوع)\s*[:=]?\s*[*_`]*$",
                prefix,
            )
        )
        keep_out = bool(re.search(r"\bkeep(?:ing)?\W*$", prefix) and re.match(r"^\W*out\b", suffix))
        # `not` immediately before a symbol, with nothing in between. The bare word is
        # ambiguous in general — `not below 30` is a comparison — but a symbol cannot
        # be compared to, so here it can only mean "keep this one out". Without it,
        # `BTC/USDT ... but not LTC/USDT` added LTC to the watchlist, because the
        # fragment reader cuts the sentence at `but` and the pair `but not` was gone
        # by the time this prefix was examined.
        adjacent_negation = bool(re.search(r"\b(?:not|nor|neither)\W*$", prefix))
        trailing_exclude = any(
            re.search(
                rf"^\W*(?:fully\s+|hard\s+)?{re.escape(marker)}(?!\s*:)",
                suffix,
            )
            for marker in _TRAILING_EXCLUSION_MARKERS
        )
        if (
            explicit_exclude
            or keep_out
            or (
                not explicit_include
                and (
                    any(marker in prefix for marker in _EXCLUSION_MARKERS)
                    or adjacent_negation
                    or trailing_exclude
                )
            )
        ):
            excluded.append(symbol)
    if excluded:
        return tuple(symbol for symbol in symbols if symbol in set(excluded))
    # `only BTCUSDT` is an inclusion, never an exclusion.
    if any(marker in lowered for marker in _INCLUSION_ONLY_MARKERS):
        return ()
    return ()


def extract_explicit_exclusions(text: str) -> tuple[str, ...]:
    """Find hard exclusions whose wording directly binds to a named symbol."""

    collapsed = _collapse(text)
    upper = collapsed.upper()
    excluded: list[str] = []
    for symbol in extract_symbols(collapsed):
        # Preserve exclusions expressed in a structured Boolean DSL. The ordinary
        # prefix/suffix reader intentionally uses a short punctuation window, which
        # cannot see through `EXCLUDE_GROUP(NOT(EQ(symbol, "XRPUSDT")))`.
        function_exclusion = re.search(
            rf"\b(?:EXCLUDE_GROUP\s*\([^)]{{0,40}})?NOT\s*\("
            rf"[\s\S]{{0,80}}?\bsymbol\s*,\s*[\"']?\s*{re.escape(symbol)}\b",
            collapsed,
            re.IGNORECASE,
        )
        if function_exclusion:
            excluded.append(symbol)
            continue
        variants = (
            symbol,
            symbol.replace("/", ""),
            symbol.replace("/", "-"),
            symbol.replace("/", "_"),
        )
        occurrences = [
            (match.start(), match.end())
            for variant in dict.fromkeys(variants)
            for match in re.finditer(re.escape(variant), upper)
        ]
        if not occurrences:
            continue
        explicitly_bound = [
            span
            for span in occurrences
            if re.search(
                r"(?:exclude|excluding|exclusions?|excluded|excluded?_?symbol|"
                r"hard[-\s]*exclude|blocklist|without|never\s+include|"
                r"not\s+include|(?:and\s+)?not(?:\s+symbol\s*(?:==|=)?)?|"
                r"drop|remove|(?<!yes/)no)(?:\W+only)?"
                r"|(?:\u0623|\u0627|\u0646|\u062a|\u064a)?"
                r"\u0633\u062a\u0628\u0639\u062f(?:\u0648\u0627|\u0647|\u0647\u0627)?"
                r"[^A-Za-z0-9]{0,20}$",
                collapsed[max(0, span[0] - 72) : span[0]].casefold(),
            )
        ]
        # Prefer the latest explicitly bound occurrence. A symbol may appear first
        # in approval prose and later in the authoritative exclusion clause.
        start, end = max(explicitly_bound or occurrences)
        prefix = collapsed[max(0, start - 72) : start].casefold()
        suffix = collapsed[end : min(len(collapsed), end + 48)].casefold()
        before = re.search(
            r"(?:(?:exclude|excluding|exclusions?|excluded|excluded?_?symbol|"
            r"hard[-\s]*exclude|blocklist|without|never\s+include|"
            r"not\s+include|(?:and\s+)?not(?:\s+symbol\s*(?:==|=)?)?|"
            r"drop|remove|(?<!yes/)no)(?:\W+only)?|"
            r"(?:\u0623|\u0627|\u0646|\u062a|\u064a)?"
            r"\u0633\u062a\u0628\u0639\u062f(?:\u0648\u0627|\u0647|\u0647\u0627)?|"
            r"استبعاد|نستبعد|ممنوع|مش\s+عايز(?:ه|ة)?)"
            r"[^A-Za-z0-9]{0,20}$",
            prefix,
        )
        after = re.match(
            r"^\W*(?:(?:please\s+)?confirm\b\W*)?"
            r"(?:is\s+|must\s+be\s+)?"
            r"(?:\d+(?:\.\d+)?%\W*)?"
            r"(?:fully\s+|hard\s+)?"
            r"(?:excluded|not\s+included|never\s+included|must\s+not\s+appear|"
            r"must\s+not\s+be\s+(?:present|included)|removed|omitted|"
            r"ممنوع|مستبعد|مش\s+عايز(?:ه|ة)?)\b",
            suffix,
        )
        keep_out = bool(re.search(r"\bkeep(?:ing)?\W*$", prefix) and re.match(r"^\W*out\b", suffix))
        if before or after or keep_out:
            excluded.append(symbol)
    return _unique(excluded)


#: Purely structural scaffolding. These words never name a market mechanic, so a
#: fragment made only of them plus already-parsed values carries no capability.
#: Anything outside this set counts as residual meaning and keeps the fragment in
#: capability resolution — an unknown word must never be silently discarded.
_STRUCTURAL_FILLER = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "but",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "from",
        "with",
        "by",
        "as",
        "is",
        "are",
        "be",
        "was",
        "were",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "there",
        "then",
        "so",
        "if",
        "when",
        "while",
        "i",
        "me",
        "my",
        "we",
        "us",
        "our",
        "you",
        "your",
        "please",
        "just",
        "want",
        "wants",
        "wanted",
        "need",
        "needs",
        "needed",
        "would",
        "should",
        "must",
        "could",
        "can",
        "will",
        "shall",
        "let",
        "lets",
        "make",
        "set",
        "setup",
        "setups",
        "change",
        "changes",
        "changed",
        "changing",
        "actually",
        "instead",
        "rather",
        "watch",
        "watching",
        "watchlist",
        "monitor",
        "monitoring",
        "monitors",
        "alert",
        "alerts",
        "alerting",
        "notify",
        "notification",
        "notifications",
        "scan",
        "scanner",
        "scanning",
        "screen",
        "screener",
        "trigger",
        "triggers",
        "triggered",
        "context",
        "condition",
        "conditions",
        "rule",
        "rules",
        "logic",
        "signal",
        "signals",
        "entry",
        "setup_mode",
        "mode",
        "side",
        "bias",
        "coin",
        "coins",
        "pair",
        "pairs",
        "symbol",
        "symbols",
        "asset",
        "assets",
        "ticker",
        "tickers",
        "market",
        "markets",
        "spot",
        "futures",
        "perp",
        "binance",
        "bybit",
        "okx",
        "kucoin",
        "exchange",
        "usdt",
        "usdc",
        "only",
        "solely",
        "exclusively",
        "all",
        "any",
        "each",
        "every",
        "now",
        "today",
        "currently",
        "current",
        "latest",
        "recent",
        "most",
        "available",
        "data",
        "evaluate",
        "evaluates",
        "evaluated",
        "evaluating",
        "evaluation",
        "compute",
        "computes",
        "computed",
        "computing",
        "print",
        "prints",
        "printed",
        "based",
        "whether",
        "valid",
        "validity",
        "requirement",
        "requirements",
        "check",
        "checks",
        "candle",
        "candles",
        "move",
        "moves",
        "movement",
        "please_note",
        "value",
        "values",
        "use",
        "using",
        "used",
        "decide",
        "decides",
        "deciding",
        "decision",
        "qualify",
        "qualifies",
        "qualifying",
        "apply",
        "applied",
        "keep",
        "kept",
        "still",
        "timeframe",
        "timeframes",
        "time",
        "frame",
        "chart",
        "candlestick",
        "exclude",
        "excluding",
        "excluded",
        "include",
        "including",
        "included",
        "never",
        "not",
        "no",
        "without",
        "omit",
        "ignore",
        "skip",
        "drop",
        "remove",
        "leave",
        "out",
        "than",
        "per",
        "over_the",
        "same",
        "one",
        "both",
    }
)


def _has_residual_content(text: str) -> bool:
    """True when wording survives deterministic parsing and may name a mechanic.

    Defaulting to ``True`` is deliberate: an unrecognised word is exactly what the
    capability registry exists to interpret. Only fragments fully accounted for by
    symbols, timeframes, operators, directions, thresholds and scaffolding are
    treated as carrying no mechanic.
    """
    tokens = [token for token in re.findall(r"[a-z0-9%]+", text.casefold()) if token]
    return any(token not in _STRUCTURAL_FILLER for token in tokens)


def _residual_vocabulary(
    text: str, *, symbols: tuple[str, ...], timeframes: tuple[str, ...]
) -> str:
    """Strip the parts already understood so only candidate mechanic wording remains."""
    residual = text
    for symbol in symbols:
        residual = re.sub(re.escape(symbol), " ", residual, flags=re.IGNORECASE)
        base = symbol[:-4] if len(symbol) > 4 else symbol
        residual = re.sub(rf"\b{re.escape(base)}\b", " ", residual, flags=re.IGNORECASE)
    residual = _TIMEFRAME_RE.sub(" ", residual)
    # A bare quantity is a threshold, not a mechanic. Removing it stops `at least 7.5%`
    # from looking like market vocabulary purely because of the percent sign.
    residual = _PERCENT_RE.sub(" ", residual)
    residual = _NUMBER_RE.sub(" ", residual)
    for term, _comparator in _OPERATOR_TERMS:
        residual = re.sub(
            rf"(?<![a-z]){re.escape(term)}(?![a-z])", " ", residual, flags=re.IGNORECASE
        )
    for term in _DIRECTION_TERMS:
        residual = re.sub(
            rf"(?<![a-z]){re.escape(term)}(?![a-z])", " ", residual, flags=re.IGNORECASE
        )
    for marker in (*_EXCLUSION_MARKERS, *_INCLUSION_ONLY_MARKERS):
        residual = re.sub(
            rf"(?<![a-z]){re.escape(marker.strip())}(?![a-z])",
            " ",
            residual,
            flags=re.IGNORECASE,
        )
    return " ".join(residual.split())


def _canonical_timeframe(amount: str, unit: str) -> str | None:
    unit = _UNIT_ALIASES.get(unit.casefold(), unit.casefold())
    return normalize_timeframe_alias(f"{amount}{unit}")


def _collapse(value: str) -> str:
    """Normalise spacing while keeping line breaks, which are real boundaries.

    Flattening newlines first merged separate statements into one fragment:
    `watch BTCUSDT and ETHUSDT…` followed by `only SOLUSDT now` became a single span,
    and the narrowing word `only` was then applied across both, marking SOLUSDT as
    *excluded* — the opposite of what the trader asked for. The fragment splitter
    already treats `\\n` as a break; it simply never saw one.
    """
    text = repair_utf8_mojibake(value or "")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
