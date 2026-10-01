"""The AI reader of new coins: it may read, it may never decide or invent.

Every rule is asserted across its whole family: every activity the methodology blocks,
every activity it does not, every kind of page, every kind of link. A fix that only
helped one coin or one activity must fail here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.grounded_patch import MIN_QUOTE_WORDS, quote_is_grounded
from ai_market_monitor.services.coin_evidence_crawler import (
    EvidenceDocument,
    EvidenceFolder,
)
from ai_market_monitor.services.coin_terms_ai_review import (
    LINK_FIELDS,
    AIReview,
    CoinTermsAIReviewer,
    CoinTermsAnswer,
    build_report,
    ground,
    methodology_payload,
    provider_links,
)
from ai_market_monitor.services.coinmarketcap import CoinLinks
from ai_market_monitor.services.sharia_automated_screen import (
    AssetFacts,
    blocking_terms,
    screen,
)
from ai_market_monitor.services.sharia_conditions import (
    Activity,
    HolderReturn,
    blocking_activities,
)
from ai_market_monitor.services.sharia_evidence_screen import (
    EvidenceDecision,
    EvidenceVerdict,
    GroundedReason,
)
from ai_market_monitor.services.sharia_source_catalog import NEWS, WEBSITE

SITE = "https://project.example"
OWN = f"{SITE}/about"
NEWS_PAGE = f"{SITE}/blog/update"
SENTENCE = "Our protocol lets every member take part in the network every single day."

BLOCKING = sorted(blocking_activities(), key=lambda a: a.value)
NOT_BLOCKING = sorted(
    (a for a in Activity if a not in blocking_activities()), key=lambda a: a.value
)


def _folder(text: str = SENTENCE) -> EvidenceFolder:
    now = datetime.now(UTC)
    return EvidenceFolder(
        symbol="NEWX",
        documents=[
            EvidenceDocument(url=OWN, category=WEBSITE, title="About", text=text, fetched_at=now),
            EvidenceDocument(
                url=NEWS_PAGE, category=NEWS, title="News", text=text, fetched_at=now
            ),
        ],
    )


def _pages(folder: EvidenceFolder) -> tuple[dict, dict]:
    return {d.url: d for d in folder.documents}, {d.url: d.text for d in folder.documents}


def _answer(**overrides) -> CoinTermsAnswer:
    payload = {
        "what_the_project_does": "It runs a network.",
        "activities": [],
        "holder_return": {"answer": None, "quote": "", "page_url": ""},
        "link_checks": [],
        "news": [],
        "doubts": [],
        "trust_points": [],
    }
    payload.update(overrides)
    return CoinTermsAnswer.model_validate(payload)


def _record(**links) -> CoinLinks:
    return CoinLinks(symbol="NEWX", cmc_id=1, name="New X", **links)


def _decision(verdict=EvidenceVerdict.ELIGIBLE, **fields) -> EvidenceDecision:
    return EvidenceDecision(
        symbol="NEWX",
        name="New X",
        verdict=verdict,
        documents_read=2,
        primary_documents_read=1,
        **fields,
    )


def _grounded(answer: CoinTermsAnswer, record: CoinLinks | None = None) -> AIReview:
    folder = _folder()
    pages, texts = _pages(folder)
    return ground(
        answer,
        pages=pages,
        texts=texts,
        links=provider_links(record),
        official_website=SITE,
    )


# --- Quote grounding ---------------------------------------------------------------


@pytest.mark.parametrize(
    "quote",
    [
        SENTENCE,
        SENTENCE.upper(),
        "  our protocol   lets every member  ",
        "Our protocol lets every member take part",
        f'"{SENTENCE}"',
    ],
)
def test_a_real_passage_is_grounded_whatever_its_case_or_spacing(quote):
    assert quote_is_grounded(quote, SENTENCE)


def test_typographic_quote_marks_do_not_count_as_invented_words():
    assert quote_is_grounded("the network’s members take part", "The network's members take part.")


@pytest.mark.parametrize(
    "quote",
    [
        "Our protocol allows every member to join the network daily.",  # paraphrase
        "Our protocol lends money to every member.",  # invented
        "",
        "Our protocol",  # too short to say anything
        " ".join(SENTENCE.split()[: MIN_QUOTE_WORDS - 1]),
    ],
)
def test_a_paraphrase_an_invention_or_a_fragment_is_not_grounded(quote):
    assert not quote_is_grounded(quote, SENTENCE)


# --- The rule has one owner --------------------------------------------------------


@pytest.mark.parametrize("activity", BLOCKING, ids=lambda a: a.value)
def test_blocking_terms_and_the_screen_agree_on_every_blocked_activity(activity):
    facts = AssetFacts(canonical_symbol="X", asset_name="X", activities=frozenset({activity}))
    blocking, _ = blocking_terms(facts)
    assert blocking == [activity]
    assert screen(facts).blocking_activities == tuple(blocking)


@pytest.mark.parametrize("activity", NOT_BLOCKING, ids=lambda a: a.value)
def test_an_activity_the_methodology_allows_is_never_a_term(activity):
    facts = AssetFacts(canonical_symbol="X", asset_name="X", activities=frozenset({activity}))
    assert blocking_terms(facts)[0] == []


def test_the_model_is_asked_about_every_activity_and_told_which_ones_the_rules_watch():
    payload = methodology_payload()
    listed = {
        row["activity"]: row["our_rules_look_closely_at_this"] for row in payload["activities"]
    }
    assert set(listed) == {a.value for a in Activity}
    assert {a for a, watched in listed.items() if watched} == {a.value for a in BLOCKING}
    assert set(payload["holder_return_options"]) == {k.value for k in HolderReturn}


# --- What holds a coin back ---------------------------------------------------------


@pytest.mark.parametrize("activity", BLOCKING, ids=lambda a: a.value)
def test_a_grounded_term_on_the_projects_own_page_holds_the_coin_back(activity):
    review = _grounded(
        _answer(activities=[{"activity": activity, "quote": SENTENCE, "page_url": OWN}])
    )
    terms = review.blocked_terms()
    assert [t["activity"] for t in terms] == [activity.value]
    assert terms[0]["quote"] and terms[0]["url"] == OWN

    report = build_report(_decision(), review, _folder(), None)
    assert report.hold_state == "held_back"
    assert report.body["human_reviewed"] is False


@pytest.mark.parametrize("activity", BLOCKING, ids=lambda a: a.value)
def test_the_same_term_on_a_news_page_is_a_doubt_and_never_holds_the_coin_back(activity):
    review = _grounded(
        _answer(activities=[{"activity": activity, "quote": SENTENCE, "page_url": NEWS_PAGE}])
    )
    assert review.blocked_terms() == []
    assert any("does not hold the coin back" in doubt for doubt in review.doubts)
    assert build_report(_decision(), review, _folder(), None).hold_state == "for_review"


@pytest.mark.parametrize("activity", BLOCKING, ids=lambda a: a.value)
@pytest.mark.parametrize(
    ("quote", "page"),
    [
        ("Our protocol lends money to every member.", OWN),  # not on the page
        (SENTENCE, f"{SITE}/somewhere-else"),  # a page it was never given
        (SENTENCE, ""),  # no page at all
    ],
    ids=["invented-quote", "unknown-page", "no-page"],
)
def test_an_ungrounded_term_is_refused_and_shown_never_used(activity, quote, page):
    review = _grounded(
        _answer(activities=[{"activity": activity, "quote": quote, "page_url": page}])
    )
    assert review.claims == []
    assert review.blocked_terms() == []
    assert len(review.refused) == 1
    report = build_report(_decision(), review, _folder(), None)
    assert report.hold_state == "for_review"
    assert review.refused[0] in report.body["doubts"]


@pytest.mark.parametrize("activity", NOT_BLOCKING, ids=lambda a: a.value)
def test_a_grounded_allowed_activity_never_holds_a_coin_back(activity):
    review = _grounded(
        _answer(activities=[{"activity": activity, "quote": SENTENCE, "page_url": OWN}])
    )
    assert review.blocked_terms() == []


@pytest.mark.parametrize(
    ("answer", "page", "held"),
    [
        (HolderReturn.FROM_LENDING_OR_PROMISE, OWN, True),
        (HolderReturn.FROM_LENDING_OR_PROMISE, NEWS_PAGE, False),
        (HolderReturn.FROM_WORK, OWN, False),
        (HolderReturn.NONE, OWN, False),
    ],
)
def test_what_holding_pays_holds_a_coin_back_only_when_it_is_lending_on_its_own_page(
    answer, page, held
):
    review = _grounded(
        _answer(holder_return={"answer": answer, "quote": SENTENCE, "page_url": page})
    )
    terms = review.blocked_terms()
    assert bool(terms) is held
    if held:
        assert terms[0]["activity"] == Activity.INTEREST_BEARING_HOLDING.value


def test_an_ungrounded_holder_return_is_refused():
    review = _grounded(
        _answer(
            holder_return={
                "answer": HolderReturn.FROM_LENDING_OR_PROMISE,
                "quote": "Holders earn a fixed ten percent yield from loans.",
                "page_url": OWN,
            }
        )
    )
    assert review.holder_return is None
    assert review.blocked_terms() == []
    assert review.refused


def test_a_term_the_fixed_rule_found_holds_the_coin_back_even_when_the_ai_failed():
    activity = BLOCKING[0]
    decision = _decision(
        EvidenceVerdict.NOT_ELIGIBLE,
        blocking_activities=[activity],
        reasons=[GroundedReason("It lends money.", quote=SENTENCE, url=OWN)],
    )
    report = build_report(decision, AIReview(state="failed"), _folder(), None)
    assert report.hold_state == "held_back"
    assert report.body["terms_found"][0]["source"] == "rule"
    assert any("did not finish" in doubt for doubt in report.body["doubts"])


def test_open_questions_alone_never_hold_a_coin_back():
    decision = _decision(
        EvidenceVerdict.NOT_ELIGIBLE, open_questions=["what does the business do?"]
    )
    report = build_report(decision, AIReview(state="completed"), _folder(), None)
    assert report.hold_state == "for_review"


@pytest.mark.parametrize(
    "state", ["completed", "failed", "not_configured", "disabled", "skipped_no_pages"]
)
def test_a_coin_with_nothing_read_is_not_enough_data_whatever_the_ai_did(state):
    decision = _decision(EvidenceVerdict.NOT_ENOUGH_DATA)
    assert build_report(decision, AIReview(state=state), _folder(), None).hold_state == (
        "not_enough_data"
    )


# --- Links ------------------------------------------------------------------------


@pytest.mark.parametrize("kind", LINK_FIELDS)
def test_every_listed_link_is_in_the_report_even_when_the_ai_skipped_it(kind):
    url = f"https://elsewhere.example/{kind}"
    review = _grounded(_answer(), _record(**{kind: (url,)}))
    assert [(c["listed_as"], c["url"], c["judgement"]) for c in review.link_checks] == [
        (kind, url, "not_checked")
    ]
    assert any(url in doubt for doubt in review.doubts)


@pytest.mark.parametrize(
    ("judgement", "doubted"),
    [("official", False), ("not_official", True), ("unclear", True)],
)
def test_a_link_the_ai_doubts_becomes_a_doubt(judgement, doubted):
    url = f"{SITE}/whitepaper.pdf"
    review = _grounded(
        _answer(link_checks=[{"url": url, "judgement": judgement, "reason": "Because."}]),
        _record(whitepaper=(url,)),
    )
    assert review.link_checks[0]["judgement"] == judgement
    assert review.link_checks[0]["on_project_site"] is True
    assert any(url in doubt for doubt in review.doubts) is doubted


def test_a_link_the_ai_invented_is_not_in_the_report():
    review = _grounded(
        _answer(
            link_checks=[
                {"url": "https://invented.example", "judgement": "official", "reason": "x"}
            ]
        ),
        _record(website=(SITE,)),
    )
    assert [c["url"] for c in review.link_checks] == [SITE]


@pytest.mark.parametrize("touches", [True, False])
def test_a_news_page_note_is_kept_only_when_grounded_and_flagged_when_it_matters(touches):
    good = {
        "page_url": NEWS_PAGE,
        "what_it_says": "An update.",
        "touches_methodology": touches,
        "quote": SENTENCE,
    }
    bad = {**good, "quote": "Something the page never said at all."}
    review = _grounded(_answer(news=[good, bad]))
    assert len(review.news) == 1
    assert len(review.refused) == 1
    assert any("news page may touch" in doubt for doubt in review.doubts) is touches


# --- Diagnostics never become the failure --------------------------------------------


def test_very_long_model_sentences_are_cut_never_refused():
    review = _grounded(_answer(doubts=["x" * 50_000] * 40, trust_points=["y" * 50_000]))
    assert len(review.doubts) <= 12
    assert all(len(item) <= 500 for item in [*review.doubts, *review.trust_points])


# --- The call itself ---------------------------------------------------------------


def _settings(**overrides) -> Settings:
    values = {"opencode_go_api_key": "test-opencode-key-1234567890", **overrides}
    return Settings(_env_file=None, **values)


def test_the_default_model_is_muse_on_high_effort():
    settings = Settings(_env_file=None)
    assert settings.coin_terms_ai_model == "muse-spark-1.3-contributor"
    assert settings.coin_terms_ai_reasoning_effort == "high"
    assert settings.coin_terms_ai_enabled is True


@pytest.mark.parametrize(
    ("overrides", "state"),
    [
        ({"coin_terms_ai_enabled": False}, "disabled"),
        ({"opencode_go_api_key": None}, "not_configured"),
        ({"coin_terms_ai_model": "no-such-model"}, "not_configured"),
    ],
)
async def test_an_unavailable_model_is_reported_not_raised(overrides, state):
    reviewer = CoinTermsAIReviewer(_settings(**overrides))
    review = await reviewer.review(symbol="NEWX", name="New X", folder=_folder(), record=None)
    assert review.state == state


async def test_a_coin_with_no_pages_is_not_sent_to_the_model():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    reviewer = CoinTermsAIReviewer(_settings(), transport=httpx.MockTransport(handler))
    review = await reviewer.review(
        symbol="NEWX", name="New X", folder=EvidenceFolder(symbol="NEWX"), record=None
    )
    assert review.state == "skipped_no_pages"
    assert calls == []


async def test_one_call_to_opencode_with_high_effort_and_a_grounded_answer():
    sent: list[dict] = []
    answer = _answer(
        activities=[{"activity": BLOCKING[0], "quote": SENTENCE, "page_url": OWN}]
    ).model_dump(mode="json")

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(answer)}],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )

    reviewer = CoinTermsAIReviewer(_settings(), transport=httpx.MockTransport(handler))
    review = await reviewer.review(
        symbol="NEWX", name="New X", folder=_folder(), record=_record(website=(SITE,))
    )
    assert review.state == "completed"
    assert len(sent) == 1
    assert sent[0]["model"] == "muse-spark-1.3-contributor"
    assert sent[0]["reasoning"] == {"effort": "high"}
    assert "service_tier" not in sent[0]
    assert [t["activity"] for t in review.blocked_terms()] == [BLOCKING[0].value]


def _text_reply(text: str) -> httpx.Response:
    message = {"type": "message", "content": [{"type": "output_text", "text": text}]}
    return httpx.Response(200, json={"output": [message]})


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(429),
        _text_reply("not json"),
        _text_reply('{"what_the_project_does": 1}'),
    ],
    ids=["5xx", "429", "not-json", "wrong-shape"],
)
async def test_any_provider_failure_is_a_failed_review_never_an_exception(response):
    reviewer = CoinTermsAIReviewer(
        _settings(), transport=httpx.MockTransport(lambda request: response)
    )
    review = await reviewer.review(symbol="NEWX", name="New X", folder=_folder(), record=None)
    assert review.state == "failed"
    assert review.error_code
    assert review.claims == []
