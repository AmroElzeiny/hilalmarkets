"""HilalMarkets profile for Open Interpreter.

Load it with:

    interpreter --profile tools/oi/hilalmarkets_profile.py

Open Interpreter executes this file with one name already defined: ``interpreter``, the
live session object. It does not import this module, so there is nothing here to import
elsewhere; everything real lives in ``src/hm_oi`` where it can be tested on its own.

Two notes about how Open Interpreter treats this file:

* It is parsed and re-emitted with ``ast.unparse`` before running, which removes
  comments. Anything that needs to be *read* by a person belongs in ``src/hm_oi`` or in
  ``AGENTS.md``, not here.
* ``from interpreter import interpreter`` is stripped automatically, so this file must
  never depend on that import existing.

This profile does not fork or patch Open Interpreter. It sets its documented settings and
wraps one documented method. An upgrade that keeps ``computer.terminal.run`` keeps
working; an upgrade that removes it stops the session with a clear message rather than
running without the permission policy.
"""

import os
import sys
from pathlib import Path

# Locate the repository. Two constraints shape this, and both were found by running it:
#
# 1. ``__file__`` does not exist here. Open Interpreter does not import this file — it
#    reads the text, rewrites it with ``ast.unparse`` and runs it through ``exec`` with
#    only ``interpreter`` in scope. Reaching for ``__file__`` raises ``NameError`` before
#    a single setting is applied.
# 2. It is written as a flat loop rather than a function on purpose. ``exec`` may be
#    given separate globals and locals, and module-level code then behaves like a class
#    body: a *function* defined here could not see these imports, while this loop can.
#
# ``HM_OI_REPO_ROOT`` is set by both launchers. The walk upwards is the fallback for
# somebody running ``interpreter --profile ...`` by hand from inside the repository.
_MARKERS = ("pyproject.toml", "src", "alembic.ini")
_ROOT = None

_declared = os.environ.get("HM_OI_REPO_ROOT", "").strip()
_candidates = []
if _declared:
    _candidates.append(Path(_declared).expanduser().resolve())
_here = Path.cwd().resolve()
_candidates.extend([_here, *_here.parents])

for _candidate in _candidates:
    if all((_candidate / _marker).exists() for _marker in _MARKERS):
        _ROOT = _candidate
        break

if _ROOT is None:
    raise SystemExit(
        "Could not find the HilalMarkets repository. Start the session with "
        "tools/oi/hm-oi.ps1 (Windows) or tools/oi/hm-oi.sh (Linux), or run "
        "interpreter --profile from inside the repository."
    )

# ``hm_oi`` lives in the repository, not in the virtual environment Open Interpreter runs
# from. Its own environment deliberately holds nothing from this project, so that
# installing Open Interpreter can never disturb the pinned dependencies the product and
# the release gate rely on.
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
os.environ.setdefault("HM_OI_REPO_ROOT", str(_ROOT))

from hm_oi.profile import DEFAULT_TIER, apply_to, build_profile  # noqa: E402
from hm_oi.routing import Tier  # noqa: E402


def _requested_tier():
    raw = str(os.environ.get("HM_OI_TIER", "") or "").strip().casefold()
    try:
        return Tier(raw) if raw else DEFAULT_TIER
    except ValueError:
        return DEFAULT_TIER


def _unattended():
    raw = str(os.environ.get("HM_OI_AUTO_RUN", "") or "").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


_tier = _requested_tier()
_profile = build_profile(_ROOT, tier=_tier)
_applied = apply_to(interpreter, _profile, auto_run=_unattended())  # noqa: F821

print("")
print("  HilalMarkets engineering assistant")
print(f"  repository   {_profile.root}")
print(f"  tier         {_tier.value.upper()}  ({_profile.model.model})")
print(f"  budget       ${_profile.session_budget_usd:.2f} for this session")
print(f"  skills       {', '.join(s.name for s in _profile.skills) or 'none'}")
print(
    f"  policy       {len(_profile.policy.rules)} rules enforced before any code runs"
)
_confirm = "OFF - running unattended" if _unattended() else "ON - you approve each block"
print(f"  confirm      {_confirm}")
if _profile.warnings:
    print("")
    for _warning in _profile.warnings:
        print(f"  ! {_warning}")
print("")
print("  This tool investigates, tests and reports. It is not part of the product,")
print("  it never reaches production, and it cannot approve or activate anything.")
print("")

if not _applied.get("guard"):
    raise SystemExit(
        "The HilalMarkets permission policy could not be installed. Refusing to start."
    )
