"""Which hosted model sits behind each tier.

Hosted only. The target machine is a small VPS, and a local model there would be slow
enough that engineers stop using the tool, which is the same outcome as not building it.
There is deliberately no code path that loads weights.

Configuration order, cheapest to change last:

1. ``HM_OI_<TIER>_MODEL`` in the environment — a one-session override.
2. ``.agents/models.json`` — the checked-in default for everybody.
3. The built-in fallback below, so a fresh checkout still starts.

The API key is read from ``HM_OI_API_KEY``. It does **not** fall back to
``OPENAI_API_KEY`` unless ``HM_OI_ALLOW_SHARED_KEY`` is set, because the product's key
pays for customer turns and its spend is reported as customer spend. An engineering
session quietly billed to that key would corrupt the only number that tells anybody
whether the product's AI costs are under control.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from hm_oi.paths import agents_dir, repo_root
from hm_oi.routing import RoutingDecision, Tier

#: Where a session's key comes from, and the escape hatch that lets it share the
#: product's key. Named here so the doctor command and the profile cannot disagree.
API_KEY_ENV: Final[str] = "HM_OI_API_KEY"
SHARED_KEY_OPT_IN_ENV: Final[str] = "HM_OI_ALLOW_SHARED_KEY"
PRODUCT_KEY_ENV: Final[str] = "OPENAI_API_KEY"
BASE_URL_ENV: Final[str] = "HM_OI_BASE_URL"

#: A ceiling on what one Open Interpreter session may spend, in US dollars. Open
#: Interpreter enforces this itself through ``interpreter.max_budget``; it is set here so
#: the value has one owner. A runaway loop is a cost bug, not only an annoyance.
SESSION_BUDGET_ENV: Final[str] = "HM_OI_SESSION_BUDGET_USD"
DEFAULT_SESSION_BUDGET_USD: Final[float] = 2.00

#: Whether Open Interpreter asks the model for its answer as a *tool call* rather than as
#: a fenced code block in ordinary text.
#:
#: On by default, and this was settled by measurement rather than preference. With tool
#: calling switched off, a live session was asked to run one command and answered:
#:
#:     I can't run the command because the execution tool is not available in this
#:     session.
#:
#: Open Interpreter's original protocol — "write a markdown code block and it will run" —
#: is still in the system message, and these models simply do not act on it. They look for
#: tools, find none, and report that they cannot do anything. Left that way the assistant
#: reads the repository rules perfectly and then investigates nothing.
#:
#: Set ``HM_OI_FUNCTION_CALLING=0`` if you point a tier at an older model that prefers the
#: text protocol. Nothing about safety changes either way: the permission guard sits on
#: ``computer.terminal.run``, and both paths go through it.
FUNCTION_CALLING_ENV: Final[str] = "HM_OI_FUNCTION_CALLING"


@dataclass(frozen=True, slots=True)
class TierModel:
    """One tier's binding: the model string litellm will receive, and how hard it thinks."""

    tier: Tier
    model: str
    reasoning_effort: str
    #: Rough guide only, for the session log and for choosing between tiers by hand.
    #: Never used as a billing figure — the provider's own accounting is authoritative.
    approx_input_usd_per_million: float | None = None
    approx_output_usd_per_million: float | None = None

    @property
    def provider(self) -> str:
        """The litellm provider prefix, or ``unknown`` if the model string has none."""

        return self.model.split("/", 1)[0] if "/" in self.model else "unknown"


#: The built-in fallback. Every name is drawn from the pricing table this repository
#: already configures for its product calls, so an account that can run HilalMarkets can
#: run this tool.
#:
#: All three were chosen by testing them, not by reading a price list. Each one had to
#: accept a tool call on ``/v1/chat/completions`` with no special handling, because Open
#: Interpreter is a tool-calling client and a model that refuses tools does nothing at
#: all here. ``gpt-5.6-luna`` — the model the product itself uses — is deliberately not
#: among them: it rejects tools whenever a reasoning effort is set, and litellm sets one
#: for it automatically, so every turn fails with HTTP 400.
DEFAULT_MODELS: Final[dict[Tier, TierModel]] = {
    Tier.FAST: TierModel(
        tier=Tier.FAST,
        model="openai/gpt-5-nano",
        reasoning_effort="minimal",
        approx_input_usd_per_million=0.05,
        approx_output_usd_per_million=0.40,
    ),
    Tier.NORMAL: TierModel(
        tier=Tier.NORMAL,
        model="openai/gpt-5-mini",
        reasoning_effort="low",
        approx_input_usd_per_million=0.25,
        approx_output_usd_per_million=2.00,
    ),
    Tier.DEEP: TierModel(
        tier=Tier.DEEP,
        model="openai/gpt-5.4-mini",
        reasoning_effort="high",
        approx_input_usd_per_million=0.75,
        approx_output_usd_per_million=4.50,
    ),
}


def _models_file(root: Path | None = None) -> Path:
    return agents_dir(root) / "models.json"


def _from_file(root: Path | None = None) -> dict[Tier, TierModel]:
    path = _models_file(root)
    if not path.exists():
        return dict(DEFAULT_MODELS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A broken config file must not stop an engineer working. The built-in defaults
        # are always usable, and the doctor command reports the file as unreadable.
        return dict(DEFAULT_MODELS)

    bound = dict(DEFAULT_MODELS)
    tiers = payload.get("tiers") if isinstance(payload, dict) else None
    if not isinstance(tiers, dict):
        return bound
    for name, raw in tiers.items():
        try:
            tier = Tier(str(name))
        except ValueError:
            continue
        if not isinstance(raw, dict) or not str(raw.get("model") or "").strip():
            continue
        bound[tier] = TierModel(
            tier=tier,
            model=str(raw["model"]).strip(),
            reasoning_effort=str(raw.get("reasoning_effort") or "low"),
            approx_input_usd_per_million=_optional_float(raw.get("approx_input_usd_per_million")),
            approx_output_usd_per_million=_optional_float(
                raw.get("approx_output_usd_per_million")
            ),
        )
    return bound


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def tier_models(
    root: Path | None = None, env: Mapping[str, str] | None = None
) -> dict[Tier, TierModel]:
    """The binding in force: file defaults with environment overrides applied."""

    environment = os.environ if env is None else env
    bound = _from_file(root)
    for tier in Tier:
        override = str(environment.get(f"HM_OI_{tier.value.upper()}_MODEL", "") or "").strip()
        if override:
            # An override changes the model, never the recorded cost estimate: keeping a
            # stale price next to a new model would be worse than having no price.
            bound[tier] = replace(
                bound[tier],
                model=override,
                approx_input_usd_per_million=None,
                approx_output_usd_per_million=None,
            )
        effort = str(
            environment.get(f"HM_OI_{tier.value.upper()}_REASONING_EFFORT", "") or ""
        ).strip()
        if effort:
            bound[tier] = replace(bound[tier], reasoning_effort=effort)
    return bound


def bind_model(
    decision: RoutingDecision,
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> RoutingDecision:
    """Attach the model for the chosen tier to a routing decision.

    Kept separate from ``route_task`` so the routing rules stay testable with no
    configuration, no environment and no provider present.
    """

    model = tier_models(root, env)[decision.tier]
    return replace(
        decision,
        model=model.model,
        provider=model.provider,
        reasoning_effort=model.reasoning_effort,
    )


def api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The key this session may spend, or ``None``.

    Sharing the product's key is possible but never accidental.
    """

    environment = os.environ if env is None else env
    own = str(environment.get(API_KEY_ENV, "") or "").strip()
    if own:
        return own
    shared = str(environment.get(SHARED_KEY_OPT_IN_ENV, "") or "").strip().casefold()
    if shared in {"1", "true", "yes", "on"}:
        return str(environment.get(PRODUCT_KEY_ENV, "") or "").strip() or None
    return None


def api_key_source(env: Mapping[str, str] | None = None) -> str:
    """Where the key came from, for the doctor report. Never the key itself."""

    environment = os.environ if env is None else env
    if str(environment.get(API_KEY_ENV, "") or "").strip():
        return API_KEY_ENV
    if api_key(environment) is not None:
        return f"{PRODUCT_KEY_ENV} (shared, opted in)"
    return "missing"


def session_budget_usd(env: Mapping[str, str] | None = None) -> float:
    """What one session may spend before Open Interpreter stops it."""

    environment = os.environ if env is None else env
    raw = str(environment.get(SESSION_BUDGET_ENV, "") or "").strip()
    if not raw:
        return DEFAULT_SESSION_BUDGET_USD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SESSION_BUDGET_USD
    # A negative or zero ceiling would read as "unlimited" to some clients. Refuse it and
    # fall back to the default rather than accidentally removing the ceiling entirely.
    return value if value > 0 else DEFAULT_SESSION_BUDGET_USD


def base_url(env: Mapping[str, str] | None = None) -> str | None:
    """An optional gateway or proxy in front of the provider."""

    environment = os.environ if env is None else env
    return str(environment.get(BASE_URL_ENV, "") or "").strip() or None


def use_function_calling(env: Mapping[str, str] | None = None) -> bool:
    """Whether to let Open Interpreter use tool calls. See ``FUNCTION_CALLING_ENV``."""

    environment = os.environ if env is None else env
    raw = str(environment.get(FUNCTION_CALLING_ENV, "") or "").strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def configuration_summary(
    root: Path | None = None, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Everything the doctor command reports. Contains no secret."""

    root = root or repo_root()
    bound = tier_models(root, env)
    return {
        "models_file": str(_models_file(root)),
        "models_file_present": _models_file(root).exists(),
        "api_key_source": api_key_source(env),
        "api_key_present": api_key(env) is not None,
        "base_url": base_url(env),
        "session_budget_usd": session_budget_usd(env),
        "tiers": {
            str(tier): {
                "model": model.model,
                "provider": model.provider,
                "reasoning_effort": model.reasoning_effort,
                "approx_input_usd_per_million": model.approx_input_usd_per_million,
                "approx_output_usd_per_million": model.approx_output_usd_per_million,
            }
            for tier, model in bound.items()
        },
    }
