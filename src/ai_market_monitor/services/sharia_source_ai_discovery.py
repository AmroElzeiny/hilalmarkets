"""Ask a model where a project publishes — but only once every free way has failed.

The layered resolver has five ways of finding a coin's own news and community pages, and
all five are free: a curated table, the identity a reviewer approved, the links the
project's own homepage carries, an open-web search, and the addresses projects usually
publish under. For most coins one of them works. For a small tail — a project whose site
is a JavaScript shell, whose blog lives on a platform nobody guessed, whose homepage the
identity record never captured — all five come back with nothing, and the coin lands in a
review queue asking a person to go and type an address.

This is the sixth way, and it is the **last** one:

* it runs for a coin only when a required category still has **zero** working links after
  every free layer has had its turn, so it is never the reason a page was found that a
  search would have found;
* it costs money, so it is off unless switched on and it asks one question per coin;
* what it returns is a **list of addresses**, nothing else. No judgement, no status, no
  description of what the project does.

**Nothing it says is believed.** Its answers are handed to
:func:`sharia_source_catalog.search_candidates`, the same filter the search engine's
answers go through, which keeps only addresses provably on the project's own domain or
carrying the project's own handle. Everything that survives is then fetched, checked
against the site's robots policy, read, and dated. A model that invents
``https://example.com/blog`` produces an address that fails its proof and disappears —
exactly like a wrong guess from the convention layer.

That is the whole safety argument, and it is deliberately *not* a confidence threshold:
confidence is the model's opinion of itself and cannot tell a real address from an
invented one. Only fetching it can.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_call
from ai_market_monitor.services.sharia_source_catalog import SearchResult

logger = logging.getLogger(__name__)

__all__ = ["AISourceDiscovery", "SUGGESTION_SCHEMA"]

#: The only shape an answer may take: addresses, and nothing else.
#:
#: A closed schema is what stops the model volunteering an opinion the product must never
#: hold — "this project does lending", "this one looks fine". It is asked for addresses;
#: it can return addresses.
SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["addresses"],
    "properties": {
        "addresses": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["url", "what_it_is"],
                "properties": {
                    "url": {"type": "string", "maxLength": 500},
                    "what_it_is": {"type": "string", "maxLength": 120},
                },
            },
        }
    },
}

_INSTRUCTIONS = (
    "You are given the name and ticker of a crypto project and, when it is known, the "
    "official website a reviewer already approved. Return the web addresses where that "
    "project itself publishes announcements, and where its own public community talks: "
    "its blog or newsroom, its GitHub releases, its Telegram announcement channel, its X "
    "account, its governance forum, its subreddit. "
    "Rules: return only addresses that belong to the project itself, never news coverage "
    "about it, never exchange listing pages, never market-data sites, never a single "
    "article or a single post. Return the feed, not one item from it. Use https. If you "
    "are not confident an address is the project's own, leave it out — an empty list is a "
    "correct answer and a wrong address is not. Do not describe what the project does, do "
    "not judge it, and do not say anything about religion, compliance or eligibility."
)


class AISourceDiscovery:
    """One question per coin, asked last, answered in addresses only."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._answers: dict[str, tuple[SearchResult, ...]] = {}

    @property
    def configured(self) -> bool:
        """Whether asking a model is switched on and possible."""

        return bool(
            self.settings.sharia_source_ai_discovery_enabled
            and self.settings.openai_api_key is not None
        )

    def requirement(self) -> str:
        """What is missing, said plainly, for a person reading a case."""

        if not self.settings.sharia_source_ai_discovery_enabled:
            return (
                "Asking a model for addresses is switched off "
                "(SHARIA_SOURCE_AI_DISCOVERY_ENABLED=false)."
            )
        if self.settings.openai_api_key is None:
            return "Asking a model for addresses needs OPENAI_API_KEY."
        return ""

    async def suggest(
        self,
        *,
        asset_name: str,
        symbol: str,
        official_website: str | None,
        already_tried: tuple[str, ...] = (),
    ) -> tuple[SearchResult, ...]:
        """Addresses the model offers for one coin. Never raises; answers with nothing.

        ``already_tried`` is sent so the model does not spend its answer repeating the
        addresses the free layers have just proved do not work. It is a hint, not a rule:
        anything it returns is filtered and proved regardless.
        """

        if not self.configured:
            return ()
        key = f"{asset_name}|{symbol}".casefold()
        cached = self._answers.get(key)
        if cached is not None:
            return cached
        try:
            payload = self._payload(
                asset_name=asset_name,
                symbol=symbol,
                official_website=official_website,
                already_tried=already_tried,
            )
            response = await self._post(payload)
            answer = _rows_to_results(response)
        except (ProviderCallError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # A model having a bad day must not stop the sweep proving the links the
            # product already holds — the same rule the web searcher follows.
            logger.info(
                "Assisted source discovery for %s failed: %s", symbol, type(exc).__name__
            )
            answer = ()
        self._answers[key] = answer
        return answer

    def _payload(
        self,
        *,
        asset_name: str,
        symbol: str,
        official_website: str | None,
        already_tried: tuple[str, ...],
    ) -> dict[str, Any]:
        question = {
            "project_name": asset_name,
            "ticker": symbol,
            "approved_official_website": official_website or None,
            "already_tried_and_did_not_work": list(already_tried[:12]),
        }
        return {
            "model": self.settings.sharia_source_ai_model,
            "store": False,
            "reasoning": {"effort": self.settings.sharia_source_ai_reasoning_effort},
            "max_output_tokens": self.settings.sharia_source_ai_max_output_tokens,
            "instructions": _INSTRUCTIONS,
            "input": json.dumps(question, sort_keys=True),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "hilalmarkets_official_addresses",
                    "strict": True,
                    "schema": SUGGESTION_SCHEMA,
                }
            },
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self.settings.openai_api_key
        if api_key is None:
            raise ValueError("No API key is configured.")
        outcome = await provider_call(
            self.settings,
            "POST",
            f"{str(self.settings.openai_base_url).rstrip('/')}/responses",
            provider="openai",
            operation="sharia_source_discovery",
            model=str(payload.get("model") or ""),
            timeout=self.settings.sharia_source_ai_timeout_seconds,
            mutation_committed=False,
            transport=self.transport,
            headers={
                "Authorization": f"Bearer {api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response = outcome.response
        if response is None or response.status_code >= 400:
            raise ValueError("The model did not answer.")
        return dict(response.json())


def _output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in response.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for content in item.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
    return "".join(parts)


def _rows_to_results(response: dict[str, Any]) -> tuple[SearchResult, ...]:
    """Turn the model's answer into the shape the catalog judges.

    Read through here and nowhere else, so a change in the provider's response shape
    breaks in one place instead of quietly producing an empty list somewhere downstream.
    """

    text = _output_text(response)
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except ValueError:
        # An answer that is not the shape it was asked for is no answer. Returning
        # nothing keeps this a *reader*: a parser that raises would turn a bad reply
        # into a failure of the sweep that was only asking a question.
        return ()
    rows = parsed.get("addresses") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return ()
    produced: list[SearchResult] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url:
            continue
        produced.append(SearchResult(url=url, title=str(raw.get("what_it_is") or "")[:300]))
    return tuple(produced)
