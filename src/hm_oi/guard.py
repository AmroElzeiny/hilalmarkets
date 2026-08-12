"""Where the permission policy stops being advice and starts being enforcement.

Open Interpreter runs every block of code through one function:
``interpreter.computer.terminal.run(language, code, stream, display)``. Everything the
model decides to execute, in any of the ten languages it supports, arrives there. This
module replaces that function with one that checks the policy first.

Why this point and not the prompt: an instruction in the system message is a request. The
model usually honours it, and "usually" is the wrong word to attach to reading a
production credential. Wrapping the executor means a refusal does not depend on the model
agreeing to be refused.

Why a wrapper and not a fork: Open Interpreter is a dependency, and a forked dependency
stops receiving fixes. The wrapper is about forty lines and survives an upgrade, so long
as ``terminal.run`` keeps its name — which ``verify_guard_surface`` checks at start-up
rather than discovering the hard way, silently, with the guard bypassed.

A refusal is returned as normal command output rather than raised as an exception. The
model then sees *why* it was refused and can choose a different approach, which is what
you want; an exception would end the session and teach it nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hm_oi import telemetry
from hm_oi.permissions import Decision, PermissionPolicy, PermissionVerdict, load_policy

#: Marks the wrapped function, so installing twice does not stack two guards — which
#: would double every log line and make a refusal message appear twice.
_GUARD_FLAG = "_hm_oi_guarded"


class GuardSurfaceMissing(RuntimeError):
    """Open Interpreter does not look the way this guard expects.

    Raised at start-up on purpose. A version whose executor moved would otherwise run
    completely unguarded, and the only sign would be that nothing was ever refused —
    which looks exactly like a well-behaved session.
    """


def verify_guard_surface(interpreter: Any) -> None:
    """Check the thing we are about to wrap is still there."""

    terminal = getattr(getattr(interpreter, "computer", None), "terminal", None)
    if terminal is None or not callable(getattr(terminal, "run", None)):
        raise GuardSurfaceMissing(
            "This version of Open Interpreter has no computer.terminal.run to protect. "
            "The HilalMarkets permission policy cannot be enforced, so the session is "
            "refused. Check tools/oi/requirements.txt against the installed version."
        )


def _refusal_text(verdict: PermissionVerdict, auto_run: bool) -> str:
    if verdict.decision is Decision.DENY:
        return (
            f"REFUSED by the HilalMarkets permission policy [{verdict.rule_id}].\n"
            f"{verdict.reason}\n\n"
            "Do not try to reach the same thing another way. Say what you needed and "
            "why, and let the engineer decide."
        )
    return (
        f"NEEDS A PERSON [{verdict.rule_id}].\n"
        f"{verdict.reason}\n\n"
        + (
            "This session is running unattended, so it cannot be confirmed. Report what "
            "you wanted to run and stop."
            if auto_run
            else "Ask the engineer to confirm before running it."
        )
    )


def _as_output(text: str) -> list[dict[str, str]]:
    """Open Interpreter's own shape for console output."""

    return [{"type": "console", "format": "output", "content": text + "\n"}]


def install_guard(
    interpreter: Any,
    policy: PermissionPolicy | None = None,
    *,
    log: bool = True,
    on_verdict: Callable[[PermissionVerdict, str], None] | None = None,
) -> PermissionPolicy:
    """Put the policy in front of Open Interpreter's executor.

    Returns the policy in force, so the caller can report on it.
    """

    verify_guard_surface(interpreter)
    active = policy or load_policy()
    terminal = interpreter.computer.terminal
    original = terminal.run
    if getattr(original, _GUARD_FLAG, False):
        return active

    def guarded(
        language: str, code: str, stream: bool = False, display: bool = False
    ) -> Any:
        verdict = active.evaluate(code)

        # ``auto_run`` is read here, at execution time, rather than captured when the
        # guard was installed. A session that turns it on halfway through must not keep
        # the weaker rule it started with.
        auto_run = bool(getattr(interpreter, "auto_run", False))
        blocked = verdict.decision is Decision.DENY or (
            verdict.decision is Decision.CONFIRM and auto_run
        )

        if log:
            telemetry.record_permission(verdict, code, language)
        if on_verdict is not None:
            on_verdict(verdict, str(language))

        if blocked:
            output = _as_output(_refusal_text(verdict, auto_run))
            # ``stream=True`` expects something to iterate. Returning the list itself
            # would iterate the dictionaries' keys and print nonsense.
            return iter(output) if stream else output

        return original(language, code, stream=stream, display=display)

    setattr(guarded, _GUARD_FLAG, True)
    guarded.__wrapped__ = original  # type: ignore[attr-defined]
    terminal.run = guarded
    return active


def uninstall_guard(interpreter: Any) -> bool:
    """Put the original executor back. For tests, not for sessions."""

    terminal = getattr(getattr(interpreter, "computer", None), "terminal", None)
    if terminal is None:
        return False
    current = getattr(terminal, "run", None)
    if current is None or not getattr(current, _GUARD_FLAG, False):
        return False
    terminal.run = current.__wrapped__
    return True


def guard_is_installed(interpreter: Any) -> bool:
    terminal = getattr(getattr(interpreter, "computer", None), "terminal", None)
    return bool(getattr(getattr(terminal, "run", None), _GUARD_FLAG, False))
