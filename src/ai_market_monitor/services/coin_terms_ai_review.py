"""A second reader for new coins: an AI model reads the same pages the rule read.

The automated screen reads a project's own pages with a fixed vocabulary. That catches
the plain wording — "borrow and lend", "casino" — and misses a project that describes
the same business in words no list anticipated. This module asks a model to read the
same pages and say what the project does, so the words the vocabulary missed are not
missed by the product.

**The model decides nothing.** It fills typed fields and nothing else:

* which :class:`Activity` the project carries on — the same buckets the screen uses;
* what holding the token pays — the same :class:`HolderReturn` answer;
* for every official and news link the provider lists, whether it is the project's own;
* its doubts and its trust points, as plain sentences for the reviewer to read.

Whether any of that is a term against the methodology is then answered by
:func:`sharia_automated_screen.blocking_terms`, the one owner of that rule. The model is
never asked "is this coin allowed" and nothing it says can make a coin allowed.

**Every claim is grounded, or it is thrown away.** An activity counts only when the model
copied a sentence of at least four words, word for word, from a page it was given, and
named that page. A claim whose quotation is not on the page is not softened into a
weaker claim; it is refused, and the refusal itself goes into the report as a doubt so
the reviewer can see what the model tried to say. Confidence is not asked for: it is the
model's opinion of itself, and only the page can show a threshold was invented.

**A term can only hold a coin back from the project's own description of itself.** The
same rule the screen keeps: a project's newsroom writes about the whole market, and a
news page that mentions lending is not a project that lends. A term found on a news page
goes into the report as a doubt for the reviewer, and never holds the coin back alone.

**Holding back is not a status.** A coin held back simply has no Shariah status, which is
what it had before; it stays out of every halal list, and a person confirms or releases
it in the task this report is filed under.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AIUsageEvent
from ai_market_monitor.engine.grounded_patch import MIN_QUOTE_WORDS, quote_is_grounded
from ai_market_monitor.services.ai_provider import (
    AIProviderConfigError,
    is_configured,
    resolve_feature_model,
    resolve_provider,
)
from ai_market_monitor.services.coin_evidence_crawler import (
    EvidenceDocument,
    EvidenceFolder,
)
from ai_market_monitor.services.coinmarketcap import CoinLinks
from ai_market_monitor.services.openai_structured_call import (
    StructuredCallError,
    structured_call,
)
from ai_market_monitor.services.sharia_automated_screen import (
    AssetFacts,
    blocking_terms,
)
from ai_market_monitor.services.sharia_conditions import (
    Activity,
    HolderReturn,
    blocking_activities,
)
from ai_market_monitor.services.sharia_evidence_screen import (
    ACTIVITY_IN_PLAIN_WORDS,
    EvidenceDecision,
    EvidenceVerdict,
)
from ai_market_monitor.services.sharia_source_catalog import is_same_project_site
from ai_market_monitor.services.system_brain import estimate_usage_cost

logger = logging.getLogger(__name__)

#: The version of the question asked. Stored on every report so a reviewer reading an
#: old one knows it was asked differently.
PROMPT_VERSION = "coin-terms-v1"

#: How much of each page, and of all pages together, the model is given. A dozen pages
#: of documentation is far more than one answer needs, and the grounding check reads the
#: same trimmed text the model read — a quote from a part it never saw cannot pass.
MAX_CHARS_PER_PAGE = 12_000
MAX_CHARS_TOTAL = 90_000
MAX_PAGES = 16
MAX_LINKS = 40

#: Caps on what is *kept* from the model's free sentences. They truncate; they never
#: raise. A report that failed because a doubt was long would lose every other finding.
MAX_SENTENCE_CHARS = 500
MAX_SENTENCES = 12

#: How many times a coin's AI reading is attempted before the report stays rule-only.
MAX_AI_ATTEMPTS = 3

#: Which provider field each link came from, as the model is told it.
LINK_FIELDS: tuple[str, ...] = (
    "website",
    "whitepaper",
    "announcement",
    "source_code",
    "explorer",
    "twitter",
    "chat",
    "reddit",
    "message_board",
)

#: The provider fields that name the project's own publishing. For these, whether the
#: address sits on the project's own site is also checked by the catalog's rule, so the
#: reviewer sees the model's opinion beside a fact the system established itself.
SITE_LINK_FIELDS: frozenset[str] = frozenset({"website", "whitepaper", "announcement"})

HoldState = Literal["held_back", "for_review", "not_enough_data"]
AIState = Literal["completed", "failed", "not_configured", "disabled", "skipped_no_pages"]

#: States the next sweep tries again, bounded by :data:`MAX_AI_ATTEMPTS`.
#:
#: ``not_configured`` and ``disabled`` are deliberately absent. The sweep works in market
#: order, so a state that is retried without counting an attempt would re-read the same
#: top coins every day and no new coin would ever reach the front of the queue.
RETRY_AI_STATES: frozenset[str] = frozenset({"pending", "failed"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivityClaim(_Strict):
    activity: Activity
    quote: str
    page_url: str


class HolderReturnClaim(_Strict):
    #: ``None`` when the pages never say what holding the token pays.
    answer: HolderReturn | None
    quote: str
    page_url: str


class LinkCheck(_Strict):
    url: str
    judgement: Literal["official", "not_official", "unclear"]
    reason: str


class NewsNote(_Strict):
    page_url: str
    what_it_says: str
    touches_methodology: bool
    quote: str


class CoinTermsAnswer(_Strict):
    """Everything the model may say. Nothing in it is a verdict."""

    what_the_project_does: str
    activities: list[ActivityClaim]
    holder_return: HolderReturnClaim
    link_checks: list[LinkCheck]
    news: list[NewsNote]
    doubts: list[str]
    trust_points: list[str]


_INSTRUCTIONS = (
    "You are Hilal Markets' research reader for new crypto coins. You read the pages "
    "supplied and report facts. You never say a coin is halal, haram, allowed, "
    "forbidden, approved or rejected; a person decides that. Write in simple English. "
    "\n\nWhat to fill:\n"
    "1. what_the_project_does: two or three plain sentences.\n"
    "2. activities: every activity from the supplied list that the PROJECT ITSELF "
    "carries on. Not its partners, not apps built on it, not the wider market. For each "
    "one, copy one sentence of at least "
    f"{MIN_QUOTE_WORDS} words EXACTLY as written on one supplied page, and give that "
    "page's url. Never paraphrase. If you cannot copy a sentence that shows it, do not "
    "list the activity.\n"
    "3. holder_return: what simply holding the token pays the holder, using the "
    "supplied options, with an exact quoted sentence and its page url. Use null, an "
    "empty quote and an empty page_url when the pages do not say.\n"
    "4. link_checks: one entry for EVERY supplied link. official means it clearly "
    "belongs to this project; not_official means it clearly belongs to somebody else or "
    "is a fake or unrelated site; unclear otherwise. Give a short reason.\n"
    "5. news: one entry for every supplied page whose kind is official_news or "
    "official_community. Say in one sentence what it says, whether it touches any "
    "activity in the supplied list, and copy one exact sentence from it.\n"
    "6. doubts: anything a careful reviewer should check — contradictions, missing "
    "information, vague claims, signs the pages are not the project's own.\n"
    "7. trust_points: concrete things that make the pages trustworthy, such as a "
    "published audit, open source code, or a clear team and company.\n"
    "Use only the supplied pages. Do not use outside knowledge for any claim."
)


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    activity: Activity
    quote: str
    url: str
    primary: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "activity": self.activity.value,
            "meaning": ACTIVITY_IN_PLAIN_WORDS.get(self.activity, self.activity.value),
            "quote": self.quote,
            "url": self.url,
            "own_description": self.primary,
        }


@dataclass(slots=True)
class AIReview:
    """What the model said, after every claim was checked against the pages."""

    state: AIState
    model: str = ""
    reasoning_effort: str = ""
    error_code: str = ""
    what_it_does: str = ""
    claims: list[GroundedClaim] = field(default_factory=list)
    #: Plain sentences naming each claim the pages did not support.
    refused: list[str] = field(default_factory=list)
    holder_return: HolderReturn | None = None
    holder_return_quote: str = ""
    holder_return_url: str = ""
    holder_return_primary: bool = False
    link_checks: list[dict[str, Any]] = field(default_factory=list)
    news: list[dict[str, Any]] = field(default_factory=list)
    doubts: list[str] = field(default_factory=list)
    trust_points: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        return self.state == "completed"

    def blocked_terms(self) -> list[dict[str, Any]]:
        """Terms against the methodology the model *proved* from the project's own pages.

        Asked of :func:`blocking_terms`, the rule's one owner. Only claims grounded on a
        page where the project describes itself are handed to it; a claim from a news
        page cannot hold a coin back.
        """

        own = [claim for claim in self.claims if claim.primary]
        holder_return = self.holder_return if self.holder_return_primary else None
        facts = AssetFacts(
            canonical_symbol="-",
            asset_name="",
            activities=frozenset(claim.activity for claim in own),
            holder_return=holder_return,
        )
        blocking, reasons = blocking_terms(facts)
        terms: list[dict[str, Any]] = []
        for activity, reason in zip(blocking, reasons, strict=True):
            support = next((c for c in own if c.activity is activity), None)
            terms.append(
                {
                    "source": "ai",
                    "activity": activity.value,
                    "reason": reason,
                    "quote": support.quote if support else self.holder_return_quote,
                    "url": support.url if support else self.holder_return_url,
                }
            )
        return terms

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "prompt_version": PROMPT_VERSION,
            "error_code": self.error_code,
            "what_it_does": self.what_it_does,
            "activities": [claim.as_dict() for claim in self.claims],
            "refused_claims": list(self.refused),
            "holder_return": {
                "answer": self.holder_return.value if self.holder_return else None,
                "quote": self.holder_return_quote,
                "url": self.holder_return_url,
            },
            "usage": dict(self.usage),
        }


def _sentence(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[: MAX_SENTENCE_CHARS - 1] + "…" if len(text) > MAX_SENTENCE_CHARS else text


def _sentences(values: Sequence[Any]) -> list[str]:
    return [s for s in (_sentence(v) for v in values) if s][:MAX_SENTENCES]


def _url_key(url: str) -> str:
    return (url or "").strip().rstrip("/").casefold()


def provider_links(record: CoinLinks | None) -> list[tuple[str, str]]:
    """Every address the provider lists for the coin, as ``(field, url)``, deduplicated."""

    if record is None:
        return []
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for name in LINK_FIELDS:
        for url in getattr(record, name, ()) or ():
            key = _url_key(str(url))
            if key and key not in seen:
                seen.add(key)
                links.append((name, str(url)))
    return links[:MAX_LINKS]


def pages_for_model(folder: EvidenceFolder) -> list[EvidenceDocument]:
    """The pages the model is shown, own-description pages first, within the budget."""

    ordered = sorted(folder.documents, key=lambda doc: (not doc.is_primary, doc.url))
    return ordered[:MAX_PAGES]


def _trimmed_texts(pages: Sequence[EvidenceDocument]) -> dict[str, str]:
    remaining = MAX_CHARS_TOTAL
    texts: dict[str, str] = {}
    for page in pages:
        if remaining <= 0:
            break
        text = page.text[: min(MAX_CHARS_PER_PAGE, remaining)]
        texts[page.url] = text
        remaining -= len(text)
    return texts


def methodology_payload() -> dict[str, Any]:
    """The activity list the model chooses from, said the way the screen says it.

    Built from :func:`blocking_activities` and :data:`ACTIVITY_IN_PLAIN_WORDS` on every
    call, so approving a condition changes what the model is asked about with no edit
    here. The model is shown which activities block only so it looks for them; it is
    never asked to decide that they block.
    """

    blocking = blocking_activities()
    return {
        "activities": [
            {
                "activity": activity.value,
                "meaning": ACTIVITY_IN_PLAIN_WORDS.get(activity, activity.value),
                "our_rules_look_closely_at_this": activity in blocking,
            }
            for activity in Activity
        ],
        "holder_return_options": {
            HolderReturn.NONE.value: "holding the token pays the holder nothing",
            HolderReturn.FROM_WORK.value: (
                "the holder is paid for work: validating, staking, running a service"
            ),
            HolderReturn.FROM_LENDING_OR_PROMISE.value: (
                "the holder is paid from lending money out, or a promised or fixed rate"
            ),
        },
    }


class CoinTermsAIReviewer:
    """Asks the model once per coin, and checks every answer against the pages."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    def usage_event(self, review: AIReview) -> AIUsageEvent | None:
        """The AI spending ledger row for one answer, or ``None`` when there is none.

        Built here, beside the call that spent the money, so the screening pipeline that
        stores it never has to know which provider or model answered. The price comes
        from the product's one cost calculator, :func:`estimate_usage_cost`.
        """

        if not review.completed:
            return None
        try:
            provider = resolve_provider(review.model)
        except AIProviderConfigError:
            # The ledger records spending; it must never be the reason a coin's report
            # is lost. An answer from a model the product cannot name is not billable
            # through any provider it knows, so there is nothing to record.
            logger.warning("coin_terms_usage_unknown_model", extra={"model": review.model})
            return None
        usage = dict(review.usage or {})
        output_details = usage.get("output_tokens_details") or {}
        input_details = usage.get("input_tokens_details") or {}
        priced = review.model in self.settings.openai_model_pricing_usd_per_million
        return AIUsageEvent(
            operation="coin_terms_review",
            provider=provider,
            model=review.model,
            reasoning_effort=review.reasoning_effort,
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(input_details.get("cached_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
            estimated_cost_usd=estimate_usage_cost(
                self.settings, model=review.model, usage=usage
            ),
            pricing_source="configured_model_pricing" if priced else "no_configured_pricing",
            outcome="completed",
            raw_usage=usage,
            created_at=datetime.now(UTC),
        )

    def unavailable(self) -> AIState | None:
        """Why the model cannot be asked right now, or ``None`` when it can."""

        if not self.settings.coin_terms_ai_enabled:
            return "disabled"
        try:
            model = resolve_feature_model(
                self.settings.coin_terms_ai_model, setting_name="COIN_TERMS_AI_MODEL"
            )
            return None if is_configured(self.settings, model) else "not_configured"
        except AIProviderConfigError:
            return "not_configured"

    async def review(
        self,
        *,
        symbol: str,
        name: str,
        folder: EvidenceFolder,
        record: CoinLinks | None,
    ) -> AIReview:
        """Read one coin. Never raises: a failure is a report that says it failed."""

        model = self.settings.coin_terms_ai_model
        effort = self.settings.coin_terms_ai_reasoning_effort
        blocked = self.unavailable()
        if blocked is not None:
            return AIReview(state=blocked, model=model, reasoning_effort=effort)
        if folder.is_empty:
            return AIReview(state="skipped_no_pages", model=model, reasoning_effort=effort)

        pages = pages_for_model(folder)
        texts = _trimmed_texts(pages)
        links = provider_links(record)
        payload = {
            "coin": {"symbol": symbol, "name": name},
            "methodology": methodology_payload(),
            "pages": [
                {
                    "url": page.url,
                    "kind": page.category,
                    "title": page.title,
                    "text": texts[page.url],
                }
                for page in pages
                if page.url in texts
            ],
            "links": [{"listed_as": kind, "url": url} for kind, url in links],
        }
        try:
            answer, usage = await structured_call(
                self.settings,
                schema_model=CoinTermsAnswer,
                schema_name="hilalmarkets_coin_terms_review",
                instructions=_INSTRUCTIONS,
                payload=payload,
                model=model,
                reasoning_effort=effort,
                max_output_tokens=self.settings.coin_terms_ai_max_output_tokens,
                timeout_seconds=self.settings.coin_terms_ai_timeout_seconds,
                stage="coin_terms_review",
                transport=self.transport,
            )
        except StructuredCallError as exc:
            logger.info(
                "coin_terms_ai_review_failed", extra={"symbol": symbol, "code": exc.code}
            )
            return AIReview(
                state="failed", model=model, reasoning_effort=effort, error_code=exc.code
            )
        except Exception as exc:  # noqa: BLE001 - one coin must not end the sweep
            logger.warning(
                "coin_terms_ai_review_error",
                extra={"symbol": symbol, "error": type(exc).__name__},
            )
            return AIReview(
                state="failed",
                model=model,
                reasoning_effort=effort,
                error_code=type(exc).__name__[:60],
            )
        review = ground(
            answer,
            pages={page.url: page for page in pages},
            texts=texts,
            links=links,
            official_website=record.website[0] if record and record.website else None,
        )
        review.model = model
        review.reasoning_effort = effort
        review.usage = usage
        return review


def ground(
    answer: CoinTermsAnswer,
    *,
    pages: Mapping[str, EvidenceDocument],
    texts: Mapping[str, str],
    links: Sequence[tuple[str, str]],
    official_website: str | None,
) -> AIReview:
    """Keep what the pages support; turn everything else into a named doubt.

    Pure: no network, no database. This is the whole of the trust placed in the model,
    so it is written to be tested on its own with any answer a model could give.
    """

    review = AIReview(state="completed", what_it_does=_sentence(answer.what_the_project_does))

    def located(url: str) -> tuple[str, str] | None:
        wanted = _url_key(url)
        for page_url, text in texts.items():
            if _url_key(page_url) == wanted:
                return page_url, text
        return None

    seen: set[tuple[Activity, bool]] = set()
    for claim in answer.activities:
        found = located(claim.page_url)
        if found is None or not quote_is_grounded(claim.quote, found[1]):
            review.refused.append(
                _sentence(
                    f"The AI said the project does '{claim.activity.value}', but the words "
                    f"it quoted are not on the page it named ({claim.page_url or 'no page'})."
                )
            )
            continue
        page = pages[found[0]]
        key = (claim.activity, page.is_primary)
        if key in seen:
            continue
        seen.add(key)
        review.claims.append(
            GroundedClaim(
                activity=claim.activity,
                quote=_sentence(claim.quote),
                url=found[0],
                primary=page.is_primary,
            )
        )

    stated = answer.holder_return
    if stated.answer is not None:
        found = located(stated.page_url)
        if found is not None and quote_is_grounded(stated.quote, found[1]):
            review.holder_return = stated.answer
            review.holder_return_quote = _sentence(stated.quote)
            review.holder_return_url = found[0]
            review.holder_return_primary = pages[found[0]].is_primary
        else:
            review.refused.append(
                _sentence(
                    f"The AI said holding the token pays '{stated.answer.value}', but the "
                    "words it quoted are not on the page it named."
                )
            )

    judged = {_url_key(check.url): check for check in answer.link_checks}
    for kind, url in links:
        check = judged.get(_url_key(url))
        on_site = (
            is_same_project_site(url, official_website)
            if kind in SITE_LINK_FIELDS and official_website and kind != "website"
            else None
        )
        review.link_checks.append(
            {
                "url": url,
                "listed_as": kind,
                "judgement": check.judgement if check else "not_checked",
                "reason": _sentence(check.reason) if check else "The AI did not check it.",
                "on_project_site": on_site,
            }
        )
        if check is None:
            review.doubts.append(_sentence(f"The AI did not check the {kind} link {url}."))
        elif check.judgement == "not_official":
            review.doubts.append(
                _sentence(
                    f"The AI thinks the {kind} link {url} is not the project's own: "
                    f"{check.reason}"
                )
            )
        elif check.judgement == "unclear":
            review.doubts.append(
                _sentence(f"The AI could not tell whether {url} is the project's own.")
            )

    for note in answer.news:
        found = located(note.page_url)
        if found is None or not quote_is_grounded(note.quote, found[1]):
            review.refused.append(
                _sentence(
                    "The AI summarised a news page, but the words it quoted are not on "
                    f"the page it named ({note.page_url or 'no page'})."
                )
            )
            continue
        review.news.append(
            {
                "url": found[0],
                "what_it_says": _sentence(note.what_it_says),
                "touches_methodology": note.touches_methodology,
                "quote": _sentence(note.quote),
            }
        )
        if note.touches_methodology:
            review.doubts.append(
                _sentence(
                    f"A news page may touch our methodology: {note.what_it_says} "
                    f"({found[0]})"
                )
            )

    # Terms found only on news or commentary pages. They cannot hold a coin back, and
    # that is exactly why the reviewer must see them.
    refusing = blocking_activities()
    for kept in review.claims:
        if not kept.primary and kept.activity in refusing:
            review.doubts.append(
                _sentence(
                    f"A page that is not the project's own description says "
                    f"'{kept.quote}' ({kept.url}). This alone does not hold the coin back."
                )
            )

    review.doubts = _sentences([*review.doubts, *answer.doubts])
    review.trust_points = _sentences(answer.trust_points)
    review.refused = _sentences(review.refused)
    return review


def rule_terms(decision: EvidenceDecision) -> list[dict[str, Any]]:
    """Terms the fixed rule found, in the same shape as the AI's."""

    if decision.verdict is not EvidenceVerdict.NOT_ELIGIBLE:
        return []
    terms: list[dict[str, Any]] = []
    for index, activity in enumerate(decision.blocking_activities):
        reason = decision.reasons[index] if index < len(decision.reasons) else None
        terms.append(
            {
                "source": "rule",
                "activity": activity.value,
                "reason": reason.text if reason else "",
                "quote": reason.quote if reason else "",
                "url": reason.url if reason else "",
            }
        )
    return terms


@dataclass(frozen=True, slots=True)
class CoinReport:
    hold_state: HoldState
    summary: str
    body: dict[str, Any]

    @property
    def held_back(self) -> bool:
        return self.hold_state == "held_back"


def build_report(
    decision: EvidenceDecision,
    review: AIReview,
    folder: EvidenceFolder,
    record: CoinLinks | None,
) -> CoinReport:
    """One report from both readings, and what the machine does while it waits.

    ``held_back`` when either reader proved a term against the methodology from the
    project's own pages. Nothing else holds a coin back, and nothing here ever marks a
    coin as passed: a coin with no term found is still only *for review*.
    """

    name = decision.name or decision.symbol
    terms = [*rule_terms(decision), *(review.blocked_terms() if review.completed else [])]

    hold_state: HoldState
    if terms:
        hold_state = "held_back"
        summary = (
            f"Held back. {len(terms)} {'term' if len(terms) == 1 else 'terms'} against "
            f"our methodology {'was' if len(terms) == 1 else 'were'} found on "
            f"{name}'s own pages. It stays out of every screened list until a person "
            "confirms or releases it."
        )
    elif decision.verdict is EvidenceVerdict.NOT_ENOUGH_DATA:
        hold_state = "not_enough_data"
        summary = (
            f"We could not read enough of {name}'s own pages to check it. "
            "Nothing was decided."
        )
    else:
        hold_state = "for_review"
        summary = (
            f"No term against our methodology was found on {name}'s own pages. "
            "This is not an approval: a person must review it."
        )

    doubts: list[str] = []
    if not review.completed:
        doubts.append(_AI_NOT_RUN.get(review.state, _AI_NOT_RUN["failed"]))
    doubts.extend(review.refused)
    doubts.extend(review.doubts)
    doubts.extend(f"Open question: {item}" for item in decision.open_questions)
    if folder.failures:
        doubts.append(
            f"{len(folder.failures)} page(s) could not be read. They are listed below."
        )

    trust: list[str] = []
    if decision.primary_documents_read:
        trust.append(
            f"{decision.primary_documents_read} of the project's own pages were read."
        )
    if record is not None and record.website:
        trust.append("CoinMarketCap lists an official website for it.")
    if record is not None and record.source_code:
        trust.append("CoinMarketCap lists public source code for it.")
    trust.extend(review.trust_points)

    body = {
        "version": 1,
        "hold_state": hold_state,
        "summary": summary,
        "terms_found": terms,
        "doubts": _sentences(doubts),
        "trust_points": _sentences(trust),
        "link_checks": list(review.link_checks),
        "news": list(review.news),
        "ai": review.as_dict(),
        "rule": {
            "verdict": decision.verdict.value,
            "reasons": [item.as_dict() for item in decision.reasons],
            "open_questions": list(decision.open_questions),
        },
        "pages_read": decision.documents_read,
        "own_pages_read": decision.primary_documents_read,
        "pages_not_read": [
            {"url": url, "why": code} for url, code in sorted(folder.failures.items())
        ][:MAX_LINKS],
        "human_reviewed": False,
    }
    return CoinReport(hold_state=hold_state, summary=summary, body=body)


_AI_NOT_RUN: dict[str, str] = {
    "failed": (
        "The AI check did not finish, so only the fixed rules were applied. "
        f"It is tried again on the next runs, {MAX_AI_ATTEMPTS} times in all."
    ),
    "not_configured": (
        "The AI check is not set up on this server, so only the fixed rules were applied."
    ),
    "disabled": "The AI check is switched off, so only the fixed rules were applied.",
    "skipped_no_pages": "No page could be read, so the AI had nothing to check.",
}


__all__ = [
    "MAX_AI_ATTEMPTS",
    "PROMPT_VERSION",
    "RETRY_AI_STATES",
    "AIReview",
    "CoinReport",
    "CoinTermsAIReviewer",
    "CoinTermsAnswer",
    "GroundedClaim",
    "build_report",
    "ground",
    "methodology_payload",
    "provider_links",
    "rule_terms",
]
