"""The lines Hilal may not cross, tested as rules rather than as examples.

Every check here is parametrised over a whole family of phrasings. A fix that only
makes one sentence refuse has to fail this file — which is the point, because "should
I buy bitcoin" and "is now a good time to buy" are the same question wearing different
words, and an assistant that refuses one and answers the other is worse than one that
answers both, since nobody would know which they had got.

Four families are covered:

* asking Hilal to **decide** a strategy — to author one, to judge one, or to choose a
  number to put in a field — refused, and offered the help it can give instead;
* asking Hilal for **financial advice** — refused, and told plainly why;
* asking Hilal to **show** somebody how to use the canvas — never refused, because
  this is now one of the two things Hilal is for;
* ordinary questions about **listings, Passports, standards, reports and plans** —
  never refused, because a refusal there would be the assistant failing at its job.

The third family is the one that moved. Hilal used to refuse anything with the word
"strategy" or "monitor" near it, which caught "how do I connect these two cards" along
with everything else. The line is no longer the subject; it is **who is deciding**:

    which of our cards says the thing you already decided you want   → helped
    which number to put in that card                                 → refused
    whether the whole thing is any good                              → refused

The pair "build me a monitor" / "help me build a monitor" is the whole rule in two
sentences, and both appear below.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.hilal_chat import HilalChatReply
from ai_market_monitor.services.hilal_chat import daily_allowance
from ai_market_monitor.services.hilal_chat_agent import refusal_for
from ai_market_monitor.services.hilal_chat_knowledge import spelling_keys

# --------------------------------------------------------------------------------
# Financial advice. Never, in any wording.
# --------------------------------------------------------------------------------

ADVICE = [
    "should I buy bitcoin",
    "Should I sell my ETH?",
    "should i hold or sell",
    "should I invest in SOL",
    "should i short this",
    "is it a good time to buy BTC",
    "is now a good idea to invest",
    "which coin should I buy",
    "what coins should i pick",
    "what token should I invest in",
    "price prediction for ETH",
    "give me a forecast",
    "what is your price target",
    "will BTC go up",
    "will this pump",
    "will ETH crash next week",
    "will it moon",
    "when should i buy",
    "where to exit",
    "how much should I invest",
    "how much to buy",
    "is this a good investment",
    "are they a safe buy",
    "is it a risky trade",
    "what stop loss level do you recommend",
    "what stop level should i use",
    "what leverage size should I use",
    "what take profit price",
    "can you guarantee returns",
    "guaranteed returns?",
    "is this risk free",
    "promise me a profit",
    "what returns can I expect",
]


@pytest.mark.parametrize("question", ADVICE, ids=lambda item: item[:40])
def test_hilal_refuses_every_way_of_asking_for_financial_advice(question: str) -> None:
    reply = refusal_for(question)
    assert reply is not None, f"Hilal would have answered {question!r}"
    assert reply.mode == "REFUSAL"


@pytest.mark.parametrize("question", ADVICE, ids=lambda item: item[:40])
def test_a_refusal_offers_something_it_can_actually_do(question: str) -> None:
    """A bare "I can't help" is honest and useless at the same time (rule B4)."""
    reply = refusal_for(question)
    assert reply is not None
    assert reply.suggestions, "the refusal offered no way forward"
    assert len(reply.reply) > 60, "the refusal does not explain itself"


# --------------------------------------------------------------------------------
# Deciding a strategy. Never Hilal's, in any wording.
# --------------------------------------------------------------------------------

#: "You produce it." Nothing in any of these asks to be shown how.
AUTHORSHIP = [
    "build me a strategy",
    "build a monitor for me",
    "can you build my setup",
    "make me a strategy",
    "create a trading rule",
    "write a strategy for BTC",
    "design a setup",
    "set up a monitor",
    "generate a bot",
    "put together a trading system",
    "give me a good strategy",
    "set me up a monitor",
    "do it all for me",
    "finish the whole thing for me",
    # With an adjective or two in front of the noun. Each one used to be a hole: the
    # pattern wanted the noun immediately after the article.
    "build me a trading strategy",
    "build me a simple crypto strategy",
    "make a good trading setup",
    "create a profitable strategy",
    "can you build me a trading bot",
    "could you write a simple monitor",
]

#: "You judge it", or "you choose the number". Both are decisions about money.
JUDGEMENT = [
    "what strategy should i use",
    "which indicator should I use",
    "what rsi should i use",
    "what settings should I use",
    # The two- and three-word forms. A pattern that allowed only one word between the
    # question and "should I" answered every one of these.
    "what rsi settings should i use",
    "which ema value should I use",
    "what timeframe should i use",
    "what indicator settings do i use",
    "what number should i put",
    "what value should I choose",
    "what threshold do i use",
    "pick a level for me",
    "best strategy for altcoins",
    "what is the optimal timeframe",
    "give me good settings",
    "ideal parameters please",
    "what is the best threshold",
    "the best conditions to use",
    # With an adjective in front of the noun. The same hole as the strategy family,
    # found a fourth time — this time by a browser test typing what a person types.
    "the best RSI value",
    "a good entry level",
    "what is the best trading strategy",
    "the ideal moving average setting",
    "tune my strategy",
    "optimise my setup",
    "backtest my strategy",
    "is my strategy good",
    "is this setup correct",
    "will this monitor work",
    "how good is my board",
]

STRATEGY = AUTHORSHIP + JUDGEMENT


@pytest.mark.parametrize("question", STRATEGY, ids=lambda item: item[:40])
def test_hilal_refuses_every_way_of_asking_it_to_decide_a_strategy(question: str) -> None:
    reply = refusal_for(question)
    assert reply is not None, f"Hilal would have tried to answer {question!r}"
    assert reply.mode == "REFUSAL"


@pytest.mark.parametrize("question", STRATEGY, ids=lambda item: item[:40])
def test_deciding_stays_refused_even_when_asked_to_be_shown(question: str) -> None:
    """"Show me" turns authorship into guidance. It must never turn a judgement into one.

    Wrapping a decision in a polite request to be taught is the obvious way round the
    new rule, and it is the one thing the new rule must not allow: "help me pick the
    best level" is still somebody asking Hilal to choose their number.
    """
    for wrapper in ("help me ", "show me ", "can we ", "i want to know "):
        if question in AUTHORSHIP:
            continue
        reply = refusal_for(f"{wrapper}{question}")
        assert reply is not None, f"{wrapper}{question!r} slipped through"


def test_a_strategy_refusal_offers_the_help_it_can_give() -> None:
    """Refusing without saying what happens next leaves the person stuck.

    It used to send them to the Monitor page and stop there. Hilal now goes with them,
    so the refusal has to say so — otherwise the product's own best answer is hidden
    behind a wall of "not me".
    """
    reply = refusal_for("build me a strategy")
    assert reply is not None
    words = reply.reply.lower()
    assert "yours" in words, "the refusal did not say whose the decision is"
    assert any(offer in words for offer in ("show you", "walk", "beside you")), (
        "the refusal did not offer to guide them instead"
    )
    assert reply.suggestions, "the refusal offered nothing to press"


def test_a_question_that_is_both_is_treated_as_the_advice_it_also_is() -> None:
    """"What is the best strategy to buy Bitcoin now" is both. Of the two, being clear
    about not giving financial advice is the more serious one."""
    reply = refusal_for("what is the best strategy, should i buy bitcoin now")
    assert reply is not None
    assert "buy" in reply.reply.lower()


# --------------------------------------------------------------------------------
# Showing somebody how to use the canvas. Now one of the two things Hilal is for.
# --------------------------------------------------------------------------------

BUILDER_HELP = [
    # The pair that is the whole rule.
    "help me build a monitor",
    "how do I build a monitor",
    "i want to build a monitor",
    "let's build my monitor",
    "can we set up a monitor together",
    "show me how to create a monitor",
    "walk me through setting up a monitor",
    "teach me how to design a setup",
    # Working the canvas.
    "how do I add a condition",
    "how do I connect two cards",
    "how do I cancel a connection",
    "how do I remove a card",
    "how do I group two conditions",
    "where is the add button",
    "what does this card mean",
    "what does all of these mean",
    "which card tells me when a price drops",
    "what conditions should i add",
    "what conditions do I have on the board",
    "what rules do i use",
    # Asking about the state of their own draft.
    "what is my board still missing",
    "why is my monitor not ready",
    "fix my monitor",
    "improve my monitor",
    "what should I do next",
    "help me finish my monitor",
]


@pytest.mark.parametrize("question", BUILDER_HELP, ids=lambda item: item[:40])
def test_hilal_helps_somebody_work_the_canvas(question: str) -> None:
    """These were all refused before, and every one of them is somebody asking to be
    shown how to use a page they are already looking at."""
    assert refusal_for(question) is None, (
        f"{question!r} was refused, and it is a question about using the product"
    )


# --------------------------------------------------------------------------------
# Somebody who is lost. The most useful message there is, and the easiest to lose.
# --------------------------------------------------------------------------------

BEING_LOST = [
    "i am lost",
    "I'm lost on this page",
    "i don't understand any of this",
    "I do not understand",
    "this is confusing",
    "i'm confused",
    "i am stuck",
    "i don't know what to do",
    "what do i do now",
    "nothing is happening",
    "it is not working",
    "i give up",
    "can you explain this page",
    "what is this screen for",
    "what is a group",
    "what is a condition",
    "what does set aside mean",
    "what does all of these mean",
    "how do i connect a card",
    "how do i disconnect a card",
    "help",
    "?",
]


@pytest.mark.parametrize("question", BEING_LOST, ids=lambda item: item[:40])
def test_being_lost_is_never_treated_as_a_refusal(question: str) -> None:
    """A person saying they are stuck must reach Hilal, not a safety refusal.

    The whole family, not the one phrasing: somebody who is confused writes the shortest
    message they can, and "help", "?" and "i give up" all mean the same thing. Answering
    one of them and refusing another would be worse than refusing all three, because
    nobody could tell which they had got.
    """

    assert refusal_for(question) is None, (
        f"{question!r} was refused, and it is somebody asking for help using the product"
    )


# --------------------------------------------------------------------------------
# The job itself. These must never be refused.
# --------------------------------------------------------------------------------

ITS_ACTUAL_JOB = [
    "is BTC eligible",
    "why is XRP excluded",
    "what does eligible with qualifications mean",
    "which screening standard is being used",
    "what is a Passport",
    "when was ETH last reviewed",
    "why did SOL's status change",
    "how many coins are eligible",
    "which exchanges do you cover",
    "what meme coins do you have",
    "what stablecoins are listed",
    "what does my plan include",
    "how much is the paid plan",
    "how do I download a report",
    "what is in the evidence report",
    "hello",
    "hi, can you help me",
    "شكرا",
    "merhaba",
    "what can you do",
    "what is a methodology",
    "who decides if a coin is eligible",
    "is dogecoin listed",
    "tell me about bitcoin's review",
]


@pytest.mark.parametrize("question", ITS_ACTUAL_JOB, ids=lambda item: item[:40])
def test_hilal_never_refuses_the_thing_it_is_for(question: str) -> None:
    assert refusal_for(question) is None, (
        f"{question!r} was refused, and it is exactly what Hilal exists to answer"
    )


# --------------------------------------------------------------------------------
# An answer is written for a person, never for a machine.
# --------------------------------------------------------------------------------

MACHINERY = [
    'Here is the data: {"symbol": "BTC", "status": "eligible"}',
    "```python\nprint('hello')\n```",
    "The canonical_asset field says BTC.",
    "Look at methodology_id to find it.",
    "<div>Bitcoin is eligible</div>",
    "The value is null for that coin.",
    "Its assessment_id is the one you want.",
    "```",
    "Set supported_comparators to gte.",
]


@pytest.mark.parametrize("text", MACHINERY, ids=lambda item: item[:34])
def test_an_answer_containing_machinery_is_refused_not_shown(text: str) -> None:
    """Rule J2. A prompt asking for no code is a request; this is the guarantee."""
    with pytest.raises(ValidationError):
        HilalChatReply(mode="ANSWER", reply=text, language="English")


PLAIN = [
    "Bitcoin is eligible under the standard in use, reviewed in March.",
    "That coin is not listed here, so I have nothing recorded for it.",
    "Your plan includes the screened market and one monitor.",
    "It changed because the source it relies on was updated.",
    "نعم، هذه العملة مؤهلة حسب المعيار المستخدم.",
]


@pytest.mark.parametrize("text", PLAIN, ids=lambda item: item[:34])
def test_an_ordinary_answer_passes_untouched(text: str) -> None:
    reply = HilalChatReply(mode="ANSWER", reply=text, language="English")
    assert reply.reply == text.strip()


def test_a_follow_up_suggestion_has_to_fit_on_a_button() -> None:
    with pytest.raises(ValidationError):
        HilalChatReply(
            mode="ANSWER",
            reply="Bitcoin is eligible.",
            language="English",
            suggestions=["x" * 200],
        )


# --------------------------------------------------------------------------------
# The daily allowance.
# --------------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    return Settings(secret_key="x" * 48, database_url="sqlite+aiosqlite:///:memory:", **overrides)


def test_a_free_person_gets_ten_cents_a_day() -> None:
    assert float(daily_allowance(_settings(), paying=False)) == pytest.approx(0.10)


def test_a_paying_person_gets_five_times_that() -> None:
    settings = _settings()
    free = daily_allowance(settings, paying=False)
    paid = daily_allowance(settings, paying=True)
    assert paid == free * 5


def test_the_multiplier_is_the_one_setting_that_decides_it() -> None:
    """Changing the setting has to change the answer, or the setting is decoration."""
    settings = _settings(hilal_chat_free_daily_usd=0.25, hilal_chat_paid_daily_multiplier=3)
    assert float(daily_allowance(settings, paying=False)) == pytest.approx(0.25)
    assert float(daily_allowance(settings, paying=True)) == pytest.approx(0.75)


# --------------------------------------------------------------------------------
# Coin names, spelled every way a person spells them.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "listed"),
    [
        ("btc", "BTC"),
        ("BTC", "BTC"),
        ("$BTC", "BTC"),
        ("btc.", "BTC"),
        ("bitcoin", "Bitcoin"),
        ("Bitcoin", "Bitcoin"),
        ("the bitcoin coin", "Bitcoin"),
        ("bitcoin token", "Bitcoin"),
        ("shiba inu", "Shiba Inu"),
        ("shibainu", "Shiba Inu"),
    ],
)
def test_a_coin_is_found_however_it_is_spelled(typed: str, listed: str) -> None:
    """The spellings are mechanical, so the vocabulary can stay in the listings.

    ``spelling_keys`` produces every mechanical form of a name; a listing and a
    question match when their sets overlap. Nothing here knows what a coin is called —
    that comes from the row.
    """

    assert spelling_keys(typed) & spelling_keys(listed), (
        f"somebody typing {typed!r} would not find {listed!r}"
    )


def test_two_different_coins_do_not_collide() -> None:
    assert not (spelling_keys("bitcoin") & spelling_keys("ethereum"))
    assert not (spelling_keys("BTC") & spelling_keys("BCH"))
