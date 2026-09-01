"""One owner for the name this product greets somebody by.

Four places worked this out separately, and — exactly as this codebase keeps finding —
the four had already drifted apart:

============================  ==========  ==============  ====================
Where                         Cut off at  When unknown    Refused an address?
============================  ==========  ==============  ====================
``services/affiliate.py``     no limit    ``""``          no
``services/hilal_chat.py``    40          ``None``        by comment only
``services/public_chat.py``   80          nothing sent    no
``services/account_admin.py`` no limit    ``"there"``     no
============================  ==========  ==============  ====================

So the same person could be "Abdurrahman" in one email, cut short in the dashboard chat,
and "there" in another email — and three of the four would happily have greeted somebody
by their email address, which two of them carried comments promising never to do.

That promise was not idle. Until the three-screen sign-up asked for a name, an account
made with an email had its ``display_name`` filled in from the part of the address before
the ``@``, so every one of these readers was handing out a fragment of somebody's address
as their name. The sign-up asks for a name now and nothing is invented, but the guard
belongs here rather than in the comments of four separate functions.

Nothing here touches the database. It takes strings, so the model layer never has to be
imported to ask a question about a name.
"""

from __future__ import annotations

__all__ = ["GREETING_NAME_LIMIT", "greeting_name", "is_usable_name"]

#: How much of a name a greeting keeps. Long enough for any real first name, short enough
#: that a pasted sentence cannot become the whole of an email subject line.
GREETING_NAME_LIMIT: int = 40


def is_usable_name(value: str | None) -> bool:
    """Is this something we may greet a person by?

    An address is never a name, and it is refused whole rather than chopped at the ``@``:
    the local part of an address is the address in all but punctuation, so using it both
    reads as a mistake to its owner and leaks their address to anybody else who is shown
    it — an affiliate reading a list of the customers they referred, for instance.

    A value with no letters in it is not a name either. ``123456`` is what an address like
    ``123456@example.com`` used to produce.
    """

    text = str(value or "").strip()
    if not text or "@" in text:
        return False
    return any(character.isalpha() for character in text)


def greeting_name(*candidates: str | None, fallback: str = "") -> str:
    """What to call somebody, from the first candidate that is really a name.

    Pass the candidates in the order they should win. The most specific source goes
    first: a name typed for *this* purpose — the one on an affiliate application, say —
    beats the name on the account, because it is the name that person chose for the thing
    they are being written to about.

    Only the first word is returned, because every caller wants a greeting rather than a
    full name: "Assalamu Alaikum Amina," not "Assalamu Alaikum Amina Yusuf,".

    ``fallback`` is what to say when there is no name at all. It is a *word*, never a
    guess at the person's identity — an empty string for templates that simply drop the
    name, or something like "there" for a sentence that cannot be written without one.
    """

    for candidate in candidates:
        if not is_usable_name(candidate):
            continue
        first = str(candidate).strip().split()[0]
        if is_usable_name(first):
            return first[:GREETING_NAME_LIMIT]
    return fallback
