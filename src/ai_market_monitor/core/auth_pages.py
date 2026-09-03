"""One owner for everything the sign-in pages say, count and check.

Six pages — ``/signup``, ``/signup/password``, ``/signup/verify``, ``/signin``,
``/signin/code`` and ``/reset-password``. Before this module every one of those facts
lived in two places at once, and the two disagreed:

*The password rule.* :func:`password_validation_error` checks each rule in
:data:`PASSWORD_RULES` and names the one that failed. The route threw that sentence away
and forwarded a bare ``invalid_password``, and the template wrote its own summary of the
rule by hand — twice, in two different wordings. So a person was told "at least 6
characters with lowercase, capital, number and special character" *after* a round trip to
the server, instead of being told which one was missing while they typed. The rules are
data here, each one carrying the regular expression a browser needs to check the identical
thing, so the checklist on the page and the check on the server can never drift apart —
and the count lives in one tuple, so removing a rule cannot leave a page promising it.

*The wait before a new code.* ``timedelta(seconds=60)`` was written twice inside
:mod:`ai_market_monitor.services.web_auth`, and the page could not show a countdown
because the number was not reachable from anywhere else. It is :data:`CODE_RESEND_SECONDS`
now, imported by both.

*What an error means.* The template carried a fifteen-branch chain of ``elif`` and ended
with ``error.replace('_', ' ').title()`` — which is how a customer came to read
"Smtp Authentication Failed" and "Invalid Login" on a login screen. Worse, four SMTP
codes printed *operator* instructions to the customer: one of them told the person
signing up to "set EMAIL_ADAPTER=smtp in the active environment and restart the app".
Every code these pages can receive is answered here, in plain words, with the next
step attached to it, and anything unrecognised falls back to a sentence rather than to a
prettified code.

*Which step of the journey this is.* Signing up is three pages and nothing said so.

Nothing here decides policy. The password rules are the ones the server already enforced,
the wait is the same sixty seconds, and the journeys are the routes that already exist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "AUTH_PAGES",
    "PRODUCT_PROMISES",
    "AuthAlert",
    "AuthPageCopy",
    "CODE_RESEND_SECONDS",
    "JourneyStep",
    "PASSWORD_RULES",
    "PasswordRule",
    "ProductPromise",
    "alert_for",
    "browser_password_rules",
    "journey_for",
    "page_copy",
    "password_validation_error",
]


# ---------------------------------------------------------------------------
# The password rule.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PasswordRule:
    """One thing a password must have.

    ``check`` is the authority: the server calls it and nothing else decides whether a
    password is accepted. ``browser_pattern`` is the same rule written as a regular
    expression so the page can tick the box while somebody types, and it is deliberately
    **never looser** than ``check``. A browser that says "done" where the server says
    "not done" would bounce a person after they pressed the button, which is the exact
    failure this whole module exists to remove; a browser that is a shade stricter only
    ever asks for a slightly stronger password, which is harmless.

    That is why the Unicode property escapes are written out. ``str.islower()`` in Python
    is the Unicode *Lowercase* property, not ``[a-z]``, so ``[a-z]`` in the browser would
    refuse to tick a rule the server had already accepted. ``\\p{Lowercase}`` is the same
    set, and ``\\p{Nd}`` is a shade narrower than ``str.isdigit()`` — on the safe side.

    There were five rules. The fifth asked for a symbol, and it is gone: a symbol is the
    rule people fail most often, it pushes them towards one predictable ``!`` on the end,
    and length does far more for a password than punctuation does. Removing it is only
    safe because this is the single owner — the server, the checklist on the page and the
    tests all read this tuple, so nothing anywhere is still asking for the sixth
    character somebody no longer has to type.
    """

    #: Stable name, used by tests and by the page's markup.
    key: str
    #: What the person reads in the checklist. Sentence case, no jargon.
    label: str
    #: What the server says when this is the rule that failed.
    failure: str
    #: The authority.
    check: Callable[[str], bool]
    #: The same rule, for a browser. Compiled with the ``u`` flag.
    browser_pattern: str


PASSWORD_RULES: Final[tuple[PasswordRule, ...]] = (
    PasswordRule(
        key="length",
        label="6 letters or more",
        failure="Password must contain at least 6 characters.",
        check=lambda value: len(value) >= 6,
        browser_pattern=r".{6,}",
    ),
    PasswordRule(
        key="lowercase",
        label="a small letter, like a",
        failure="Password must include a lowercase letter.",
        check=lambda value: any(character.islower() for character in value),
        browser_pattern=r"\p{Lowercase}",
    ),
    PasswordRule(
        key="uppercase",
        label="a capital letter, like A",
        failure="Password must include a capital letter.",
        check=lambda value: any(character.isupper() for character in value),
        browser_pattern=r"\p{Uppercase}",
    ),
    PasswordRule(
        key="digit",
        label="a number, like 7",
        failure="Password must include a number.",
        check=lambda value: any(character.isdigit() for character in value),
        browser_pattern=r"\p{Nd}",
    ),
)


def password_validation_error(password: str) -> str | None:
    """The first rule this password breaks, or ``None`` when it breaks none.

    The order matters and is the order a person reads the checklist in, so the sentence
    the server returns always names the rule nearest the top that is still missing.
    """

    value = password or ""
    for rule in PASSWORD_RULES:
        if not rule.check(value):
            return rule.failure
    return None


def browser_password_rules() -> list[dict[str, str]]:
    """The rules in the shape the page's script reads them in."""

    return [
        {"key": rule.key, "label": rule.label, "pattern": rule.browser_pattern}
        for rule in PASSWORD_RULES
    ]


# ---------------------------------------------------------------------------
# The one-time code.
# ---------------------------------------------------------------------------

#: How long somebody must wait before a second code may be sent, in seconds.
#:
#: The rule lives here because two places enforce it and a third place — the page —
#: needs to show it as a countdown. It was written as ``timedelta(seconds=60)`` twice
#: inside the sign-up path and the sign-in path, so "wait one minute" was a sentence in
#: a template that nothing kept true.
CODE_RESEND_SECONDS: Final[int] = 60


# ---------------------------------------------------------------------------
# The journey.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JourneyStep:
    """One stop on the way in."""

    key: str
    title: str
    hint: str
    icon: str
    #: The end of the road rather than something to do. Drawn differently and left out
    #: of "step 2 of 2", because arriving is not a form to fill in.
    destination: bool = False


@dataclass(frozen=True)
class Journey:
    steps: tuple[JourneyStep, ...]
    #: Index into :attr:`steps` of the step this page is.
    current: int

    @property
    def action_steps(self) -> tuple[JourneyStep, ...]:
        return tuple(step for step in self.steps if not step.destination)

    @property
    def position(self) -> int:
        """Which action step this is, counting from one."""

        return sum(1 for step in self.steps[: self.current + 1] if not step.destination)

    @property
    def total(self) -> int:
        return len(self.action_steps)

    @property
    def shows_counter(self) -> bool:
        """One thing to do is not a journey, and saying "step 1 of 1" is noise."""

        return self.total > 1


_DASHBOARD = JourneyStep(
    key="dashboard",
    title="Your dashboard",
    hint="Your Watchlists, your monitors and your evidence.",
    icon="dashboard",
    destination=True,
)

#: Signing up is three screens, and each one asks for one thing.
#:
#: It used to be a single screen with five boxes on it — first name, last name, email,
#: password, password again — plus a five-line checklist under the password. On a laptop
#: that form did not fit the window, so the button a person came to press was below the
#: fold on the first page of the product they had just decided to try.
#:
#: Two boxes on screen one still fits, and it fails better: a taken email address, and a
#: name we cannot use, are both found before anybody has chosen a password rather than
#: after. The two name boxes became one — a person with a single name, or four, should
#: not have to decide which half of themselves to leave out.
_SIGNUP_STEPS: Final[tuple[JourneyStep, ...]] = (
    JourneyStep(
        key="details",
        title="Your details",
        hint="Your name, and where your code goes.",
        icon="user",
    ),
    JourneyStep(
        key="password",
        title="Your password",
        hint="Something you will remember, typed twice.",
        icon="lock",
    ),
    JourneyStep(
        key="confirm",
        title="Confirm your email",
        hint="We send a six-digit code. You type it back.",
        icon="shield_check",
    ),
    _DASHBOARD,
)

_SIGNIN_STEPS: Final[tuple[JourneyStep, ...]] = (
    JourneyStep(
        key="signin",
        title="Sign in",
        hint="Your email and your password.",
        icon="lock",
    ),
    _DASHBOARD,
)

_CODE_STEPS: Final[tuple[JourneyStep, ...]] = (
    JourneyStep(
        key="ask",
        title="Ask for a code",
        hint="Type the email you signed up with.",
        icon="mail",
    ),
    JourneyStep(
        key="enter",
        title="Enter the code",
        hint="Six digits, straight from your inbox.",
        icon="shield_check",
    ),
    _DASHBOARD,
)

_RESET_STEPS: Final[tuple[JourneyStep, ...]] = (
    JourneyStep(
        key="ask",
        title="Ask for a code",
        hint="Type the email you signed up with.",
        icon="mail",
    ),
    JourneyStep(
        key="choose",
        title="Choose a new password",
        hint="Enter the code, then pick something new.",
        icon="lock",
    ),
    JourneyStep(
        key="signin",
        title="Sign in again",
        hint="With the password you just chose.",
        icon="check",
        destination=True,
    ),
)


# ---------------------------------------------------------------------------
# The three things the product promises.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductPromise:
    """One thing Hilal Markets does, or one thing it deliberately never does.

    A title and nothing else. Each promise used to carry an explaining sentence under it,
    and three sentences of small print under the button is the part of a sign-in page
    nobody reads — it made the card taller for no one's benefit. The titles say the same
    three things in four words each.
    """

    key: str
    #: Four or five words. It is a tick in a row of three, not a paragraph.
    title: str


#: What sits under the button on every one of these pages.
#:
#: These three sentences were written out inside ``auth.html`` and drawn down the side of
#: the screen in a tall dark panel. The panel is gone — it filled half of a laptop window
#: with something nobody reads while they are signing in, and it pushed the form itself
#: off the bottom. The same three promises are a compact row of ticks under the button
#: now, where a person actually looks before they press it.
#:
#: They live here rather than in the template for the reason every vocabulary in this
#: codebase does: two pages were already free to disagree about them, and the copy tests
#: cannot check a sentence that only exists inside markup.
PRODUCT_PROMISES: Final[tuple[ProductPromise, ...]] = (
    ProductPromise(key="evidence", title="Evidence you can open"),
    ProductPromise(key="approval", title="You approve every rule"),
    ProductPromise(key="watching", title="Watching, never trading"),
)


# ---------------------------------------------------------------------------
# What each page says.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthPageCopy:
    """Every word a page needs, in one place, so no branch of the template invents any."""

    #: The value ``page`` already carries in the route.
    page: str
    #: Which of the page's two states this is. ``""`` when the page has only one.
    state: str
    #: The browser tab, and the heading. Sentence case (brand guide, section 11).
    title: str
    #: One sentence under the heading saying what happens next.
    lede: str
    #: The label on the button that finishes this page.
    submit: str
    #: Which journey, and where in it.
    journey: Journey


def _state_for(page: str, *, has_email: bool, code_sent: bool) -> str:
    """Which half of a two-part page a request is on.

    ``/signin/code`` and ``/reset-password`` are each two forms behind one address: ask
    for a code, then type it. The template used to work this out itself, in two places,
    with two slightly different conditions.
    """

    if page in {"signin_code", "reset_password"}:
        return "enter" if (code_sent or has_email) else "ask"
    return ""


def journey_for(page: str, state: str) -> Journey:
    if page == "signup":
        return Journey(_SIGNUP_STEPS, 0)
    if page == "signup_password":
        return Journey(_SIGNUP_STEPS, 1)
    if page == "signup_verify":
        return Journey(_SIGNUP_STEPS, 2)
    if page == "signin":
        return Journey(_SIGNIN_STEPS, 0)
    if page == "signin_code":
        return Journey(_CODE_STEPS, 1 if state == "enter" else 0)
    if page == "reset_password":
        return Journey(_RESET_STEPS, 1 if state == "enter" else 0)
    return Journey(_SIGNIN_STEPS, 0)


#: Every page the ``auth.html`` template can be asked to draw.
AUTH_PAGES: Final[tuple[str, ...]] = (
    "signup",
    "signup_password",
    "signup_verify",
    "signin",
    "signin_code",
    "reset_password",
)

#: Heading, the line under it, and the button.
#:
#: The middle one is empty on the four pages whose heading already said the whole thing.
#: "Sign in" under a heading reading "Sign in" is a line a person reads and learns
#: nothing from, and four such lines were making the card taller than the screen.
#:
#: It is kept exactly where it still teaches something, which is every page that has to
#: explain the six-digit code: where it went, how long it lasts, what to do with it. That
#: is an instruction, not a welcome.
_TITLES: Final[dict[tuple[str, str], tuple[str, str, str]]] = {
    ("signup", ""): (
        "Create your account",
        "",
        "Continue",
    ),
    ("signup_password", ""): (
        "Choose a password",
        "",
        "Send my code",
    ),
    ("signup_verify", ""): (
        "Confirm your email",
        "Type the six-digit code we just sent you.",
        "Verify and create account",
    ),
    ("signin", ""): (
        "Sign in",
        "",
        "Sign in",
    ),
    ("signin_code", "ask"): (
        "Sign in without a password",
        "We email you a six-digit code instead. Nothing to remember.",
        "Email me a code",
    ),
    ("signin_code", "enter"): (
        "Enter your code",
        "Type the six digits we just sent you.",
        "Verify and sign in",
    ),
    ("reset_password", "ask"): (
        "Reset your password",
        "",
        "Email me a code",
    ),
    ("reset_password", "enter"): (
        "Choose a new password",
        "Type the code we sent, then pick a password you will remember.",
        "Save new password",
    ),
}


def page_copy(page: str, *, has_email: bool = False, code_sent: bool = False) -> AuthPageCopy:
    """Everything the template needs to draw one page, decided here rather than there."""

    state = _state_for(page, has_email=has_email, code_sent=code_sent)
    title, lede, submit = _TITLES.get(
        (page, state),
        ("Sign in", "Welcome back.", "Sign in"),
    )
    return AuthPageCopy(
        page=page,
        state=state,
        title=title,
        lede=lede,
        submit=submit,
        journey=journey_for(page, state),
    )


# ---------------------------------------------------------------------------
# What went right, and what went wrong.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthAlert:
    """One thing to tell the person, and the one thing they can do about it."""

    #: ``success``, ``error`` or ``info``. Decides colour **and** icon **and** the word
    #: in front of the message, because colour alone is never a status (brand guide 10).
    tone: str
    #: A short heading. Sentence case, no error code, no jargon.
    title: str
    #: One or two plain sentences: what happened and what to do.
    body: str
    #: A button that performs the next step, when there is one worth offering.
    action_label: str = ""
    action_href: str = ""
    #: True when the action re-sends a code, so the page can wire it to the countdown.
    action_resends: bool = False


#: Every message code that can land on one of these pages.
_MESSAGES: Final[dict[str, tuple[str, str, str]]] = {
    "logout_successful": (
        "success",
        "You are signed out",
        "Sign in again whenever you want to.",
    ),
    "password_reset_successful": (
        "success",
        "Your password is changed",
        "Sign in with the new password.",
    ),
    "session_required": (
        "info",
        "Please sign in again",
        "You were away for a while, so we ended the old session to keep the account safe.",
    ),
    "plan_selected": (
        "info",
        "Your plan choice is saved",
        "Sign in and we take you straight to it.",
    ),
}


def _code_sent_message(page: str, ttl_minutes: int) -> AuthAlert:
    """"We sent a code" — worded per page, because the truth differs per page.

    ``/signin/code`` never says whether the address has an account: it answers the same
    way either way, so the wording must not promise a code that was not sent. The other
    two only reach this message once the account is known, so hedging there would be a
    second, needless lie.
    """

    if page == "signin_code":
        body = (
            f"If that email has an account, a six-digit code is on its way. "
            f"It works for {ttl_minutes} minutes."
        )
    else:
        body = f"Check your inbox for six digits. The code works for {ttl_minutes} minutes."
    return AuthAlert(tone="success", title="Code sent", body=body)


#: Everything that can go wrong, answered in plain words.
#:
#: The value is (tone, title, body, action label, action href). An empty href means the
#: page fills one in — the sign-up and sign-in links carry the plan and Telegram choices
#: a person arrived with, and losing those would silently drop what they picked.
_ERRORS: Final[dict[str, tuple[str, str, str, str, str]]] = {
    "account_banned": (
        "error",
        "This account is blocked",
        "You cannot sign in with it. Write to us if you think this is a mistake.",
        "Contact support",
        "support",
    ),
    "account_unavailable": (
        "error",
        "This account is not open yet",
        "It is not ready to sign in with. Write to us and we will tell you why.",
        "Contact support",
        "support",
    ),
    "account_exists": (
        "error",
        "You already have an account",
        "This email is taken. Sign in with your password instead.",
        "Go to sign in",
        "signin",
    ),
    # The same fact as above, for the other kind of account. It is answered separately
    # because the sentence above sends somebody to a password, and an account made with
    # Google does not have one — so that answer put those people in a loop they could not
    # get out of. There is still only ever one account per address; only the door differs.
    "account_exists_google": (
        "error",
        "You already have an account",
        "You made it with Google. Go to sign in and use the Google button. "
        "It is the same account either way.",
        "Go to sign in",
        "signin",
    ),
    # Somebody typed a password into an account that has none. Their password is not
    # wrong — there is nothing to check it against — so "email or password is incorrect"
    # was false as well as useless. Both ways forward are named: the button that works
    # today, and how to have a password from now on.
    "google_sign_in_required": (
        "error",
        "This account uses Google",
        "You made it with Google, so it has no password yet. Use the Google button "
        "above. If you would rather use a password, set one now.",
        "Set a password",
        "reset",
    ),
    "account_not_registered": (
        "error",
        "We cannot find that account",
        "No account uses this email. Check the spelling, or create one.",
        "Create an account",
        "signup",
    ),
    "invalid_login": (
        "error",
        "That email and password do not match",
        "Check both and try again. You can also sign in with a code sent to your email.",
        "Email me a code instead",
        "signin_code",
    ),
    "invalid_email": (
        "error",
        "That email does not look right",
        "Check for a small mistake, like a missing @ or an extra space.",
        "",
        "",
    ),
    # This code could always be raised and never had an answer written for it, so the
    # page fell through to "Something went wrong" — which tells a person nothing about
    # which of the boxes they need to change.
    "invalid_name": (
        "error",
        "That name does not look right",
        "Please write your name using letters. It can be up to 60 characters.",
        "",
        "",
    ),
    "invalid_password": (
        "error",
        "That password is not strong enough",
        "The list under the password box shows what is still missing.",
        "",
        "",
    ),
    "password_mismatch": (
        "error",
        "The two passwords are different",
        "Type the same password in both boxes.",
        "",
        "",
    ),
    "invalid_code": (
        "error",
        "That code did not work",
        "Check the six digits, or ask for a new code.",
        "Send a new code",
        "resend",
    ),
    "code_expired": (
        "error",
        "That code has expired",
        "Codes last a few minutes. Ask for a new one.",
        "Send a new code",
        "resend",
    ),
    "code_locked": (
        "error",
        "Too many tries",
        "We stopped checking that code to keep the account safe. Ask for a new one.",
        "Send a new code",
        "resend",
    ),
    "code_recently_sent": (
        "info",
        "A code is already on its way",
        "Give it a moment to arrive before asking for another.",
        "",
        "",
    ),
    "identity_broken": (
        "error",
        "Something is wrong with this account",
        "We cannot sign you in. Please write to us and we will fix it.",
        "Contact support",
        "support",
    ),
    "telegram_already_linked": (
        "error",
        "That Telegram account is already connected",
        "It belongs to a different Hilal Markets account. Sign in with that one instead.",
        "",
        "",
    ),
    "telegram_link_used": (
        "error",
        "That Telegram link was already used",
        "Open Telegram and start the connection again to get a fresh link.",
        "",
        "",
    ),
    "telegram_link_expired": (
        "error",
        "That Telegram link has expired",
        "Open Telegram and start the connection again to get a fresh link.",
        "",
        "",
    ),
    "telegram_link_invalid": (
        "error",
        "That Telegram link does not work",
        "Open Telegram and start the connection again to get a fresh link.",
        "",
        "",
    ),
    "telegram_bot_missing": (
        "error",
        "Telegram is not switched on yet",
        "You can still sign in here. Telegram can be connected later from Settings.",
        "",
        "",
    ),
    "dashboard_link_invalid": (
        "error",
        "That link does not work any more",
        "Sign in here instead and you will land in the same place.",
        "",
        "",
    ),
    "dashboard_link_used": (
        "error",
        "That link was already used",
        "Each link works once. Sign in here instead.",
        "",
        "",
    ),
    "dashboard_link_expired": (
        "error",
        "That link has expired",
        "Sign in here instead and you will land in the same place.",
        "",
        "",
    ),
    "user_missing": (
        "error",
        "We cannot find that account",
        "It may have been removed. Create a new account, or write to us.",
        "Create an account",
        "signup",
    ),
    "invalid_target": (
        "error",
        "That link points nowhere",
        "Sign in here instead.",
        "",
        "",
    ),
    # ── The Google door ──────────────────────────────────────────────────────
    #
    # Every one of these is something a person can act on. Nothing here names OAuth, a
    # token, a state parameter or a redirect address: those are our words for our
    # problem, and a customer can do nothing with any of them.
    "google_cancelled": (
        "info",
        "You closed the Google window",
        "Nothing was created and nothing changed. Try again whenever you like.",
        "",
        "",
    ),
    "google_unavailable": (
        "error",
        "Google did not answer",
        "This one is on us, not on you. Try again in a moment, or use your email.",
        "",
        "",
    ),
    "google_disabled": (
        "error",
        "Google sign-in is switched off",
        "Use your email and a password instead. Both doors lead to the same account.",
        "Create an account",
        "signup",
    ),
    "google_link_expired": (
        "error",
        "That Google window was open too long",
        "Press the Google button again and it will only take a moment.",
        "",
        "",
    ),
    "google_email_unverified": (
        "error",
        "Google has not confirmed that email",
        "Confirm the address inside your Google account, or sign up with your email here.",
        "Create an account",
        "signup",
    ),
    "google_email_missing": (
        "error",
        "Google did not share an email address",
        "We need one to make an account. Sign up with your email instead.",
        "Create an account",
        "signup",
    ),
}

#: The one answer for every way email can fail to leave the building.
#:
#: Four of these used to print an instruction meant for whoever runs the server — one
#: told the person signing up to set an environment variable and restart the app. A
#: customer can do nothing about any of them, and every new SMTP code added upstream
#: would have leaked the same way, so the class is matched rather than the members.
_EMAIL_FAILURE_PREFIXES: Final[tuple[str, ...]] = ("smtp_", "email_")

_EMAIL_FAILURE: Final[AuthAlert] = AuthAlert(
    tone="error",
    title="We could not send the email",
    body="This one is on us, not on you. Please try again in a few minutes.",
    action_label="Contact support",
    action_href="support",
)

_UNKNOWN: Final[AuthAlert] = AuthAlert(
    tone="error",
    title="Something went wrong",
    body="Please try again. If it keeps happening, write to us and we will look into it.",
    action_label="Contact support",
    action_href="support",
)


def alert_for(
    *,
    page: str,
    message: str | None,
    error: str | None,
    ttl_minutes: int,
    links: dict[str, str],
) -> AuthAlert | None:
    """The one thing to tell this person, or ``None`` when there is nothing to say.

    ``links`` maps the action names above onto real addresses, so this module never has
    to know that the sign-in link carries a plan code.
    """

    if error:
        if error.startswith(_EMAIL_FAILURE_PREFIXES):
            alert = _EMAIL_FAILURE
        else:
            found = _ERRORS.get(error)
            alert = (
                AuthAlert(
                    tone=found[0],
                    title=found[1],
                    body=found[2],
                    action_label=found[3],
                    action_href=found[4],
                )
                if found
                else _UNKNOWN
            )
        return _with_link(alert, links)
    if message:
        if message == "code_sent":
            return _code_sent_message(page, ttl_minutes)
        found_message = _MESSAGES.get(message)
        if found_message is None:
            return None
        return AuthAlert(tone=found_message[0], title=found_message[1], body=found_message[2])
    return None


def _with_link(alert: AuthAlert, links: dict[str, str]) -> AuthAlert:
    """Turn an action name into an address, and drop the action when there is none."""

    if not alert.action_label:
        return alert
    if alert.action_href == "resend":
        return AuthAlert(
            tone=alert.tone,
            title=alert.title,
            body=alert.body,
            action_label=alert.action_label,
            action_href="",
            action_resends=True,
        )
    href = links.get(alert.action_href, "")
    if not href:
        return AuthAlert(tone=alert.tone, title=alert.title, body=alert.body)
    return AuthAlert(
        tone=alert.tone,
        title=alert.title,
        body=alert.body,
        action_label=alert.action_label,
        action_href=href,
    )
