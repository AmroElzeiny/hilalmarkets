"""The deploy scripts must not be able to break the server, or empty its database.

On 22 August 2026 the live server went down and SSH stopped answering. It **looked** like
a full disk and it was not — the disk was 25% used. The real cause was memory, and it is
covered by `test_invariant_container_memory_limits.py`.

Looking into it turned up two real faults in `deploy/` anyway. Neither caused that outage;
each would have filled the disk given time:

* `redeploy-clean.sh` wrote one full database dump on every deploy and nothing ever
  deleted them. The folder grew with no limit at all, on the same disk as the database.
* Nothing checked for free space before a deploy that throws the whole build cache away
  and rebuilds every image layer from zero. A build that runs out of room part way leaves
  its half-written layers behind and never reaches the step that clears them, so every
  failed attempt would leave the server with *less* room than before.

A third rule came out of the outage itself: **swap**. It is a setting on the machine, so no
test can assert it — but a deploy can report it, and a deploy is the moment somebody is
looking. That report is the only thing that would catch missing swap again after a server
rebuild.

Each rule below is asserted over **every** script in `deploy/`, present and future, so a
fix that only helps the one script that failed does not pass here. The rules are about the
shape of the scripts, not about one command in one of them.

`test_resource_guard_behaviour_suite` runs `tests/shell/resource_guard_cases.sh`, which is where
the actual behaviour (which dump survives, what a bad limit does) is proved. It needs a
working bash and skips where there is not one, so these text rules are the part that
always runs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy"
RESOURCE_GUARD = DEPLOY_DIR / "resource_guard.sh"
SHELL_SUITE = ROOT / "tests" / "shell" / "resource_guard_cases.sh"

DEPLOY_SCRIPTS = sorted(DEPLOY_DIR.glob("*.sh"))
SCRIPT_IDS = [path.name for path in DEPLOY_SCRIPTS]

# The one function that reads free space, and the one that deletes old dumps. Every script
# gets them by sourcing resource_guard.sh; nothing re-implements either.
GUARD_FUNCTIONS = (
    "disk_free_gb",
    "disk_mount_of",
    "disk_require_free",
    "disk_prune_backups",
    "memory_report_swap",
)

HEREDOC_START = re.compile(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")


def executable_lines(script: Path) -> list[str]:
    """The lines of ``script`` that the shell actually runs.

    Comments and here-document bodies are removed. Both of them quote the dangerous
    commands **on purpose** — `free-disk.sh` warns in prose never to run
    ``docker volume prune``, and `redeploy-clean.sh` prints recovery hints containing
    other commands. A scan that reads those as code would fail on the very warnings that
    exist to prevent the mistake, and the honest way to fix that is to read the file the
    way the shell reads it rather than to weaken the rule.
    """
    kept: list[str] = []
    terminator: str | None = None
    for raw in script.read_text(encoding="utf-8").splitlines():
        if terminator is not None:
            if raw.strip() == terminator:
                terminator = None
            continue
        if raw.lstrip().startswith("#"):
            continue
        match = HEREDOC_START.search(raw)
        if match:
            terminator = match.group(1)
        kept.append(raw)
    return kept


def executable_text(script: Path) -> str:
    return "\n".join(executable_lines(script))


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=SCRIPT_IDS)
def test_no_deploy_script_can_delete_a_docker_volume(script: Path) -> None:
    """Nothing in `deploy/` may run a command that deletes a Docker volume.

    The PostgreSQL data, the Redis data, the exports and the TLS certificates are all
    Docker volumes. When the stack is stopped Docker counts them as unused, so
    ``docker volume prune`` and ``docker system prune --volumes`` delete the entire
    database. This project already lost its database once, on 19 August 2026, and these
    are the commands an operator reaches for when a disk is full.
    """
    forbidden = {
        "docker volume prune": re.compile(r"\bdocker\s+volume\s+prune\b"),
        "docker system prune with --volumes": re.compile(
            r"\bdocker\s+system\s+prune\b[^\n]*(--volumes|\s-\w*v)"
        ),
        "compose down with --volumes": re.compile(
            r"\bdown\b[^\n|]*(--volumes|\s-v\b)"
        ),
    }
    text = executable_text(script)
    offenders = [name for name, pattern in forbidden.items() if pattern.search(text)]
    assert not offenders, (
        f"{script.name} runs {offenders}, which deletes Docker volumes — that is the "
        "database, Redis, the exports and the TLS certificates."
    )


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=SCRIPT_IDS)
def test_a_script_that_writes_dumps_also_deletes_old_ones(script: Path) -> None:
    """Writing a database dump without deleting old ones is what filled the disk."""
    text = executable_text(script)
    if "predeploy-" not in text:
        pytest.skip(f"{script.name} does not write pre-deploy dumps")
    assert "disk_prune_backups" in text, (
        f"{script.name} writes a pre-deploy dump but never calls disk_prune_backups, so "
        "the backup folder grows with no limit until the disk is full."
    )


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=SCRIPT_IDS)
def test_a_script_that_builds_images_checks_for_room_first(script: Path) -> None:
    """A build must not start on a disk that cannot hold it."""
    lines = executable_lines(script)
    build_at = next(
        (
            index
            for index, line in enumerate(lines)
            if re.search(r"\bdocker\s+build\b", line)
            or re.search(r"\bbuild\b\s+(--no-cache|--pull|$)", line)
            or re.search(r'"\$\{COMPOSE\[@\]\}"\s+build\b', line)
        ),
        None,
    )
    if build_at is None:
        pytest.skip(f"{script.name} does not build images")
    check_at = next(
        (index for index, line in enumerate(lines) if "disk_require_free" in line),
        None,
    )
    assert check_at is not None, (
        f"{script.name} builds images but never calls disk_require_free. A build that "
        "runs out of room leaves its half-written layers behind, so the next attempt has "
        "even less space."
    )
    assert check_at < build_at, (
        f"{script.name} checks the free space at line {check_at + 1} but already builds "
        f"at line {build_at + 1}. The check has to come first to prevent anything."
    )


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=SCRIPT_IDS)
def test_a_script_that_touches_the_live_server_reports_the_swap(script: Path) -> None:
    """Swap is the one protection that cannot live in this repository — so report it.

    The server had none. A Celery child grew to 1.4 GB on a 3.9 GB machine, and with no
    swap the kernel had no slack: it killed `systemd`, so SSH went down with the site.

    Adding swap is a one-off command on the machine, which means it is a one-off command
    somebody forgets after the next rebuild. Nothing would ever say so again. These
    scripts are the only code in the repository that runs *on* the server, so they are the
    only place a reminder can live.

    It is reported, never enforced: missing swap does not break the deploy that is
    running, and refusing would block an emergency deploy for a fault it did not cause.
    """
    if script == RESOURCE_GUARD:
        return
    text = executable_text(script)
    assert "memory_report_swap" in text, (
        f"{script.name} runs on the live server but never calls memory_report_swap. "
        "Missing swap is invisible until the machine dies, and these scripts are the only "
        "place that can point it out."
    )


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=SCRIPT_IDS)
def test_only_the_guard_measures_the_disk(script: Path) -> None:
    """One owner for "how much room is left", so no two scripts can disagree.

    A plain ``df -h`` that only shows a human the disk is fine anywhere. What may live in
    exactly one place is *measuring* it — reading a number out of ``df`` and deciding
    something from it. Two such readers drift apart, and this repository's recurring
    fault is exactly that: two implementations of one idea, each understanding a
    different subset.
    """
    if script == RESOURCE_GUARD:
        return
    text = executable_text(script)
    # A single `|` is a pipe into a reader. `||` is "or else", which is how `df -h` is
    # allowed to fall back to a message when it cannot read the disk.
    assert not re.search(r"\bdf\b[^\n|]*\|(?!\|)", text), (
        f"{script.name} pipes df into something. Measuring free space belongs in "
        "deploy/resource_guard.sh only; call disk_free_gb or disk_require_free instead."
    )
    assert not re.search(r"\bdf\s+-\w*P", text), (
        f"{script.name} uses df in its machine-readable form. Call disk_free_gb from "
        "deploy/resource_guard.sh instead."
    )


@pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=SCRIPT_IDS)
def test_a_script_using_the_guard_also_sources_it(script: Path) -> None:
    """Calling a shared function without sourcing the file is a crash at run time."""
    if script == RESOURCE_GUARD:
        return
    text = executable_text(script)
    used = [name for name in GUARD_FUNCTIONS if name in text]
    if not used:
        pytest.skip(f"{script.name} does not use the disk guard")
    assert re.search(r"^\s*(source|\.)\s+.*resource_guard\.sh", text, re.MULTILINE), (
        f"{script.name} calls {used} but never sources deploy/resource_guard.sh, so it would "
        "fail with 'command not found' the moment it ran."
    )


@pytest.mark.parametrize(
    "script", DEPLOY_SCRIPTS + [SHELL_SUITE], ids=SCRIPT_IDS + [SHELL_SUITE.name]
)
def test_every_script_can_actually_run_on_the_server(script: Path) -> None:
    """A recovery script that will not start is worse than no recovery script.

    `core.autocrlf` is `true` on the Windows machines this repository is developed on, so
    a shell script can reach the Ubuntu server with a carriage return at the end of every
    line. Bash then stops on the second line with ``$'\\r': command not found``, and the
    message says nothing at all about line endings. `.gitattributes` pins ``*.sh`` to Unix
    line endings; this fails if one gets through anyway.
    """
    raw = script.read_bytes()
    assert b"\r" not in raw, (
        f"{script.name} contains a carriage return. It will not run on the Ubuntu server: "
        "bash stops at the second line. Check .gitattributes pins *.sh to eol=lf."
    )
    assert raw.startswith(b"#!"), (
        f"{script.name} has no shebang line, so how it is interpreted depends on who runs "
        "it."
    )


def test_resource_guard_refuses_to_delete_every_dump() -> None:
    """The limit can never be read as zero.

    A server with no backup at all is a worse problem than a server with a full disk, so
    an unusable limit is refused rather than rounded to something. The behaviour itself is
    proved in `tests/shell/resource_guard_cases.sh`; this is the part that runs everywhere.
    """
    text = RESOURCE_GUARD.read_text(encoding="utf-8")
    assert re.search(r"keep\s*<\s*1", text), (
        "resource_guard.sh must refuse a keep count below 1, or a mistyped setting deletes "
        "every database dump on the server."
    )


def test_free_disk_never_touches_volumes_and_says_so() -> None:
    """The emergency script is read by someone under pressure at 3am.

    It must warn, in its own text, against the two commands that delete the database, so
    that the warning is in front of the person who is about to search the web for a way
    to free space quickly.
    """
    text = (DEPLOY_DIR / "free-disk.sh").read_text(encoding="utf-8")
    assert "docker system prune -a --volumes" in text
    assert "docker volume prune" in text
    assert "NEVER run" in text


def _bash_can_fork() -> str | None:
    """The bash to run the shell suite with, or ``None``.

    Git Bash on this project's Windows machine cannot fork child processes, so a shell
    suite there dies at its first command substitution. That is a broken tool, not a
    broken test, and it must read as a skip rather than as a failure.
    """
    bash = shutil.which("bash")
    if bash is None:
        return None
    try:
        probe = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", 'value="$(echo forked)"; echo "$value"'],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bash if probe.returncode == 0 and probe.stdout.strip() == "forked" else None


def test_resource_guard_behaviour_suite() -> None:
    """Run the real shell tests: which dump survives, and what a bad limit does."""
    bash = _bash_can_fork()
    if bash is None:
        pytest.skip("no bash that can start child processes on this machine")
    result = subprocess.run(
        [bash, "--noprofile", "--norc", str(SHELL_SUITE), str(ROOT)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"tests/shell/resource_guard_cases.sh failed\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
