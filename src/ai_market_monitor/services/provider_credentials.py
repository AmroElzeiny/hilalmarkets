"""Which outbound callers send a credential, and which send none at all.

One owner for a single question: **when a far side answers 401 or 403, is that our key
being refused, or is it a stranger's website refusing an anonymous visitor?**

The two are opposite problems and they need opposite handling:

===========================  =====================================================
The call                     What 401/403 means
===========================  =====================================================
carries a credential         our key is wrong, expired or lacks a permission. An
                             operator must fix a setting. Nothing retries; a person
                             is told, and the setting is named.
carries no credential        the far side does not serve anonymous callers, or a
                             bot filter refused this IP. There is no key, so there
                             is nothing to fix and nobody to tell. It is an answer
                             about *that address*, and it is recorded as one.
===========================  =====================================================

Reading the second as the first is what put ``Provider credentials refused:
official_source (fetch_evidence) returned 403. The feature stays off until the key is
fixed.`` on an operator's phone. ``official_source`` is not an upstream service holding a
key — it is whichever website a coin happens to publish on, several hundred different
companies over one sweep. A Cloudflare bot filter answering 403 to a datacentre address
is the ordinary weather of the open web. The alert named a key that does not exist,
promised a feature had stopped when it had not, and arrived again every fifteen minutes.

**Why a declared table and not a look at the outgoing request.** Guessing from headers —
"is there an ``Authorization``?" — is a parser of its own, and it gets signed URLs, query
credentials and cookie sessions wrong. Guessing wrong in that direction is the expensive
one: it silences a real "your Stripe key expired". So the answer is declared per caller
and :mod:`tests.unit.test_invariant_provider_credentials` refuses to pass while any
``provider=`` literal in ``src`` is missing from this table. Adding a provider without
declaring it fails the suite; it cannot drift quietly.

An unknown name is read as **authenticated** on purpose. That keeps the alert, which is
the safe way to be wrong: a spurious message about a key is a nuisance, a swallowed one
is an outage nobody hears about.
"""

from __future__ import annotations

__all__ = [
    "PROVIDER_CREDENTIALS",
    "UNAUTHENTICATED_PROVIDERS",
    "authenticates",
    "credential_setting",
]


#: Every ``provider=`` name used for an outbound call, mapped to the environment setting
#: that holds its credential — or ``None`` for a caller that sends none.
#:
#: The value is the name a person types into ``.env``, so an alert can say which setting
#: to look at instead of "check the configured API key", which was true of nine settings
#: at once.
PROVIDER_CREDENTIALS: dict[str, str | None] = {
    # ----- reads somebody else's public website; never holds a key -----------------
    #: Whichever site a coin publishes on. A different company on every call.
    "official_source": None,
    #: The Securities Commission Malaysia's own public pages.
    "sc_malaysia": None,
    #: Fasset's public Shariah reports.
    "fasset": None,
    # ----- holds a credential ------------------------------------------------------
    "openai": "OPENAI_API_KEY",
    "stripe": "STRIPE_SECRET_KEY",
    "creem": "CREEM_API_KEY",
    "nowpayments": "NOWPAYMENTS_API_KEY",
    "telegram": "TELEGRAM_BOT_TOKEN",
    "whatsapp": "WHATSAPP_ACCESS_TOKEN",
    "coinmarketcap": "COINMARKETCAP_API_KEY",
    "coingecko": "COINGECKO_API_KEY",
    "google_search": "GOOGLE_SEARCH_API_KEY",
    "brave_search": "BRAVE_SEARCH_API_KEY",
    "google_sheets": "WAITLIST_GOOGLE_SHEETS_WEBHOOK_SECRET",
    "market_metadata": "MARKET_METADATA_API_KEY",
    #: Four settings, one per category of context — ``MACRO_MARKET_API_KEY``,
    #: ``EVENT_FEED_API_KEY``, ``TOKEN_CATEGORY_API_KEY``,
    #: ``DERIVATIVES_CONTEXT_API_KEY``. Which one was refused is in the ``operation``
    #: field of the alert, which carries the category name.
    "context_provider": "the <CATEGORY>_API_KEY for this category",
    # ----- names used as labels, not for outbound HTTP ------------------------------
    #: Recorded on internal bookkeeping rows so a hand-made change is attributable. No
    #: request leaves the process under these names, so no credential can be refused.
    "admin": None,
    "free": None,
    "static": None,
    #: Market data is read through the ccxt library's own sessions, which carry the
    #: exchange keys when an exchange needs them. Nothing here goes through the
    #: reliability layer, so it can never raise this alert.
    "ccxt": None,
}


#: The callers that send nothing. Kept as its own name because it reads as the rule it is
#: — "these send no credential" — at the two places that ask.
UNAUTHENTICATED_PROVIDERS = frozenset(
    name for name, setting in PROVIDER_CREDENTIALS.items() if setting is None
)


def authenticates(provider: str) -> bool:
    """Whether a call under this name carries a credential of ours.

    Unknown names answer ``True``. See the module docstring: keeping an alert we did not
    need is recoverable, losing one we did is not.
    """

    return PROVIDER_CREDENTIALS.get(provider, "unknown") is not None


def credential_setting(provider: str) -> str | None:
    """The setting name to put in front of an operator, when there is one."""

    return PROVIDER_CREDENTIALS.get(provider)
