"""What this product's own words mean, in plain language, written once.

Hilal is held to one rule above all others: **every fact it states must come from the
evidence it was given** (rule B6). That rule had a hole in it. The evidence carried
coins, standards, plans and the board a person is drawing — but nothing at all about
what the *words on that board* mean. So when somebody sitting on the canvas asked the
most common beginner question there is — "what is a Group?" — Hilal had two ways to
answer and both were wrong: say it did not know, about the product it is the expert on,
or make something up.

This is the missing evidence. Each entry is one word a person can read on their own
screen and one plain sentence saying what it is. It is handed to the model with every
turn, exactly like a coin's review or a plan's price, and it is citable in the same way.

**What belongs here, and what does not.**

* *Belongs* — the meaning of a word this product prints: a condition, a Group, the
  board, "set aside", the checklist. These are facts about our own software.
* *Does not belong* — how to perform a gesture. That comes from the canvas's own "Keys
  and gestures" panel, travels as ``how_this_board_is_worked`` and is quoted rather than
  restated. A second description of a drag would be a second opinion about it, and the
  first person to see the two disagree would be a customer.
* *Does not belong* — anything about markets, money or Shariah rulings. The entry for
  Shariah status below deliberately says only *where a status comes from*, never what one
  means; only the review process decides that, and Hilal repeats it rather than
  explaining it.

Nothing here is a second copy of a label. The words are the words the interface prints —
"all of these", "set aside", "condition" — and
``test_every_product_word_matches_the_interface`` is what notices when the interface
changes one of them and this file has not.
"""

from __future__ import annotations

from typing import Any

#: One word, and what it is, for somebody who has never seen this product before.
#:
#: The key is the word as a person reads it on screen. The sentence is written for a
#: beginner who may not be a native English speaker: short, everyday words, and never a
#: second technical word used to explain the first.
PRODUCT_WORDS: dict[str, str] = {
    "monitor": (
        "A monitor is one thing you have asked us to watch the market for. You say what "
        "has to happen, and we tell you when it happens. A monitor never buys or sells "
        "anything."
    ),
    "condition": (
        "A condition is one thing that has to be true, like 'the price drops below "
        "yesterday's low'. On the board each condition is one card. A monitor is made of "
        "conditions."
    ),
    "card": (
        "A card is one condition, drawn as a small box on the board. The name at the top "
        "says what it looks at, and the fields inside are the parts you fill in yourself."
    ),
    "board": (
        "The board is the open space where the cards sit. Moving around the board "
        "changes only what you are looking at. It never changes the monitor."
    ),
    "group": (
        "A group is a box that holds several cards and says how they go together. There "
        "are three kinds, and the card itself tells you which one it is in: 'all of "
        "these', 'any of these' and 'none of these'."
    ),
    "all of these": (
        "A group where every card inside it has to be true at the same time before you "
        "are told anything."
    ),
    "any of these": (
        "A group where one card inside it being true is enough for you to be told."
    ),
    "none of these": (
        "A group that has to stay false. If any card inside it becomes true, you are not "
        "told."
    ),
    "connecting a card": (
        "Joining a card to a group makes it part of that group, and part of the monitor. "
        "A card has to be joined to something to count."
    ),
    "cancelling a connection": (
        "Taking a card out of its group. The card stays on the board and keeps every "
        "number you typed into it; it just stops being part of the monitor until you "
        "join it again."
    ),
    "set aside": (
        "A card that is on the board but not part of the monitor. Nothing you typed into "
        "it is lost. It is simply not being used yet."
    ),
    "must be true": (
        "A card marked this way has to be true. If it is not, you are not told, however "
        "many other cards are true."
    ),
    "the checklist": (
        "The list the page keeps of anything still missing or worth looking at. Each line "
        "is about one thing, and the page writes it, not you."
    ),
    "how ready it is": (
        "A number the page works out from its own checklist, to show how much of the "
        "monitor is filled in. It says nothing about whether the monitor is a good idea."
    ),
    "what it watches": (
        "The set of coins a monitor is pointed at. Only coins that have passed screening "
        "can be watched."
    ),
    "ways to be told": (
        "Where a message reaches you: here in the dashboard, by email, or on Telegram. "
        "You choose these on the Connections page."
    ),
    "draft": (
        "A monitor you are still drawing. A draft is saved as you work, and it watches "
        "nothing at all until you approve it."
    ),
    "approve": (
        "The moment you read a monitor and agree to it. Nothing is ever monitored before "
        "you do this, and no change to a monitor takes effect until you approve it too. "
        "Only you can approve — the assistant cannot."
    ),
    "watchlist": (
        "A saved list of the coins you want to keep together, so you are told when one of "
        "them changes."
    ),
    "alert": (
        "A message telling you that a monitor's conditions all happened. It says which "
        "conditions matched and what the values were. It is never advice to buy or sell."
    ),
    "opportunity": (
        "A setup one of your monitors is following right now — forming, close, or "
        "matched. Seeing one is not a reason to trade; it is only what the market did."
    ),
    "evidence passport": (
        "The full record behind one coin: the standard used, the reasons, the sources, "
        "the date it was reviewed, and every change to it over time."
    ),
    "shariah status": (
        "The result our own review process recorded for a coin, under a named standard "
        "and version, on a named date. Only that review decides a status. The assistant "
        "repeats what was recorded and never decides anything itself."
    ),
}


#: The id every word is cited by, so a definition can be pointed at like any other row.
def word_id(word: str) -> str:
    return f"word:{word.replace(' ', '_')}"


def product_words() -> list[dict[str, Any]]:
    """Every product word, shaped like the other evidence rows.

    Handed over on every turn rather than looked up by keyword. The whole set is about
    twenty short sentences, and guessing which ones a question needs is exactly the kind
    of second opinion this codebase keeps paying for — somebody stuck on the canvas
    rarely asks using the word they are stuck on.
    """

    return [
        {
            "id": word_id(word),
            "kind": "word_this_product_uses",
            "word": word,
            "what_it_means": meaning,
        }
        for word, meaning in PRODUCT_WORDS.items()
    ]
