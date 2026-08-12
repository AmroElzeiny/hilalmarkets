"""HilalMarkets engineering assistant configuration for Open Interpreter.

This package configures an **engineering** tool. It is not part of the product.

Nothing in ``ai_market_monitor`` may import this package, and nothing here may be
imported by a request the customer's browser can reach. The separation is checked by
``scripts/check_oi_boundary.py`` rather than left to a convention somebody has to
remember, because the failure it prevents is silent: an engineering assistant that
becomes reachable from a customer request is an unbounded code-execution surface
attached to the product.

Two different AI systems live in this repository. They share no code and no keys:

===========================  ==================================================
HilalMarkets production AI   ``ai_market_monitor.services.ai_*``. Reads a
                             customer's words, proposes a typed draft, and is
                             subordinate to the compiler, Sharia screening,
                             provider gates and the customer's own approval. It
                             may never execute code.
Open Interpreter engineering Everything in this package. Reads the repository,
AI                           runs tests, explains what it found. It never sees a
                             customer, never touches production state, and never
                             appears in a product decision.
===========================  ==================================================
"""

from __future__ import annotations

from hm_oi.catalog import CommandEntry, CommandSafety, load_catalog
from hm_oi.paths import repo_root
from hm_oi.permissions import PermissionPolicy, PermissionVerdict, load_policy
from hm_oi.routing import RoutingDecision, TaskRequest, Tier, route_task
from hm_oi.skills import Skill, load_skills

__all__ = [
    "CommandEntry",
    "CommandSafety",
    "PermissionPolicy",
    "PermissionVerdict",
    "RoutingDecision",
    "Skill",
    "TaskRequest",
    "Tier",
    "load_catalog",
    "load_policy",
    "load_skills",
    "repo_root",
    "route_task",
]
