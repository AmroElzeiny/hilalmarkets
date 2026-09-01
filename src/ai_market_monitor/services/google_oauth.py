"""The Google door into Hilal Markets.

One extra way in, and deliberately the smallest one that can exist. Google is asked for
exactly two things — the email address and the person's name — and nothing else. No
contacts, no calendar, no profile picture, no ongoing access: the two scopes below are
the two Google itself calls "basic", the token is used once, and nothing from Google is
stored except the name and the address the account is made from.

**A Google account is an email account here.** It signs into the same
:class:`~ai_market_monitor.db.models.enums.IdentityProvider.EMAIL` identity the password
door uses, because the email address *is* the identity. That means:

* pressing Google with an address that already has a password lands in that same
  account, rather than quietly making a second one beside it;
* an account made through Google has no password at all, and ``verify_password`` refuses
  a null hash, so the Google door cannot weaken the password door;
* nothing is duplicated — there is one account per address, whichever door was used.

``email_verified`` is required. Without it, anybody who could persuade Google to issue a
token for an unconfirmed address would be handed the matching Hilal Markets account.

Why the identity token is read without checking its signature: it is not fetched from a
browser. It arrives in the body of a server-to-server HTTPS response from
``oauth2.googleapis.com``, a request that carries this application's own client secret.
The transport is the proof. ``aud`` and ``iss`` are still checked, because a response
meant for a different application is a real mistake and it costs one comparison to catch.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ai_market_monitor.core.config import Settings

__all__ = [
    "GOOGLE_AUTHORIZATION_ENDPOINT",
    "GOOGLE_TOKEN_ENDPOINT",
    "GoogleOAuthError",
    "GoogleOAuthService",
    "GoogleProfile",
]

#: Where the person is sent to choose an account, and where the code is redeemed.
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

#: The two smallest scopes Google offers: the address, and the name. Nothing else is
#: asked for, so nothing else can be granted.
GOOGLE_SCOPES = ("openid", "email", "profile")

#: Who Google says it is. Both spellings are current and Google uses either.
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

#: A slow Google is a Google that did not answer. Ten seconds is long enough for a token
#: exchange and short enough that a person is not left looking at a spinning popup.
_TIMEOUT_SECONDS = 10.0


class GoogleOAuthError(ValueError):
    """Something went wrong on the way through Google.

    ``code`` is answered in plain words by ``core/auth_pages.py``, like every other code
    these pages can show. Nothing here writes a sentence for a customer.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GoogleProfile:
    """Everything we keep from Google, which is three short strings."""

    #: Google's own permanent id for the account. Recorded so a later address change is
    #: recognisable; never used as the identity itself.
    subject: str
    email: str
    first_name: str
    last_name: str


class GoogleOAuthService:
    """Build the link, guard the round trip, and turn a code into a name and an address."""

    #: The signature namespace for the state parameter. Its own salt, so a token minted
    #: for anything else in this product can never be replayed as a Google state.
    state_salt = "google-oauth-state-v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.serializer = URLSafeTimedSerializer(
            settings.app_secret_key.get_secret_value()
        )

    # ── Guards ───────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self.settings.google_signin_enabled

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise GoogleOAuthError(
                "google_disabled",
                "Google sign-in is not configured on this deployment.",
            )

    # ── The round trip ───────────────────────────────────────────────────────

    def issue_state(self, payload: dict[str, str]) -> str:
        """Sign what the round trip has to remember.

        The plan a person picked, the Telegram link they arrived with and which of the
        two pages they pressed the button on all have to survive a trip through Google.
        Carrying them in a signed value rather than in a cookie means the callback works
        in a popup window, where a freshly set cookie is not reliably readable.
        """

        self._require_enabled()
        return self.serializer.dumps(payload, salt=self.state_salt)

    def read_state(self, state: str) -> dict[str, str]:
        try:
            payload = self.serializer.loads(
                state,
                salt=self.state_salt,
                max_age=self.settings.google_oauth_state_ttl_minutes * 60,
            )
        except SignatureExpired as exc:
            raise GoogleOAuthError(
                "google_link_expired",
                "The Google sign-in window was open for too long.",
            ) from exc
        except BadSignature as exc:
            raise GoogleOAuthError(
                "google_link_expired",
                "That Google sign-in could not be matched to a request we started.",
            ) from exc
        if not isinstance(payload, dict):
            raise GoogleOAuthError(
                "google_link_expired",
                "That Google sign-in could not be matched to a request we started.",
            )
        return {str(key): str(value) for key, value in payload.items()}

    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        """Where the popup goes.

        ``prompt=select_account`` is deliberate: on a shared machine, or for somebody
        with two Google accounts, silently reusing whichever one the browser happens to
        be signed into is how a person ends up in an account that is not theirs.
        """

        self._require_enabled()
        query = urlencode(
            {
                "client_id": (self.settings.google_oauth_client_id or "").strip(),
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GOOGLE_SCOPES),
                "state": state,
                "access_type": "online",
                "include_granted_scopes": "true",
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"

    async def exchange(self, *, code: str, redirect_uri: str) -> GoogleProfile:
        """Redeem the one-time code and read the name and the address out of it."""

        self._require_enabled()
        secret = self.settings.google_oauth_client_secret
        form = {
            "code": code,
            "client_id": (self.settings.google_oauth_client_id or "").strip(),
            "client_secret": secret.get_secret_value() if secret else "",
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(GOOGLE_TOKEN_ENDPOINT, data=form)
        except httpx.HTTPError as exc:
            raise GoogleOAuthError(
                "google_unavailable",
                "Google did not answer the sign-in request.",
            ) from exc
        if response.status_code >= 400:
            raise GoogleOAuthError(
                "google_unavailable",
                f"Google refused the sign-in exchange with status {response.status_code}.",
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleOAuthError(
                "google_unavailable",
                "Google answered the sign-in request with something unreadable.",
            ) from exc
        if not isinstance(body, dict):
            raise GoogleOAuthError(
                "google_unavailable",
                "Google answered the sign-in request with something unreadable.",
            )
        claims = self._claims_from(body.get("id_token"))
        return self._profile_from(claims)

    # ── Reading what came back ───────────────────────────────────────────────

    def _claims_from(self, id_token: Any) -> dict[str, Any]:
        if not isinstance(id_token, str) or id_token.count(".") != 2:
            raise GoogleOAuthError(
                "google_unavailable",
                "Google answered without an identity token.",
            )
        _, payload_part, _ = id_token.split(".")
        padding = "=" * (-len(payload_part) % 4)
        try:
            raw = base64.urlsafe_b64decode(payload_part + padding)
            claims = json.loads(raw)
        except (ValueError, binascii.Error) as exc:
            raise GoogleOAuthError(
                "google_unavailable",
                "Google's identity token could not be read.",
            ) from exc
        if not isinstance(claims, dict):
            raise GoogleOAuthError(
                "google_unavailable",
                "Google's identity token could not be read.",
            )
        audience = str(claims.get("aud") or "")
        if audience != (self.settings.google_oauth_client_id or "").strip():
            raise GoogleOAuthError(
                "google_unavailable",
                "Google's identity token was issued for a different application.",
            )
        if str(claims.get("iss") or "") not in GOOGLE_ISSUERS:
            raise GoogleOAuthError(
                "google_unavailable",
                "Google's identity token did not come from Google.",
            )
        return claims

    def _profile_from(self, claims: dict[str, Any]) -> GoogleProfile:
        email = str(claims.get("email") or "").strip()
        if not email or "@" not in email:
            raise GoogleOAuthError(
                "google_email_missing",
                "Google did not share an email address.",
            )
        # Google sends this as a real boolean in the identity token and as the string
        # "true" in some older payloads. Both are accepted; anything else is not — a
        # missing claim is not a confirmed address.
        verified = claims.get("email_verified")
        if verified is not True and str(verified).strip().lower() != "true":
            raise GoogleOAuthError(
                "google_email_unverified",
                "Google has not confirmed that email address.",
            )
        first_name, last_name = _split_name(claims)
        return GoogleProfile(
            subject=str(claims.get("sub") or "").strip(),
            email=email,
            first_name=first_name,
            last_name=last_name,
        )


def _split_name(claims: dict[str, Any]) -> tuple[str, str]:
    """First and last name, from whichever of the three claims Google actually sent.

    ``given_name`` and ``family_name`` are the reliable pair, but a Google account with
    a single-word name has only ``name``. Reading one claim and giving up would have left
    those people with no name at all — and then the welcome email greets nobody.
    """

    given = " ".join(str(claims.get("given_name") or "").split())
    family = " ".join(str(claims.get("family_name") or "").split())
    if given or family:
        return given[:60], family[:60]
    whole = " ".join(str(claims.get("name") or "").split())
    if not whole:
        return "", ""
    first, _, rest = whole.partition(" ")
    return first[:60], rest[:60]
