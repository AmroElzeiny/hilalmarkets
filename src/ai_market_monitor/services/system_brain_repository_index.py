from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import RepositoryEvidenceIndex
from ai_market_monitor.schemas.system_brain import EvidenceEnvelope
from ai_market_monitor.services.system_brain_privacy import redact_customer_text

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = next(
    (
        candidate
        for candidate in (Path.cwd().resolve(), _PACKAGE_ROOT)
        if (candidate / "pyproject.toml").is_file() and (candidate / "src").is_dir()
    ),
    _PACKAGE_ROOT,
)
INDEX_ROOTS = ("src", "docs", "alembic", "scripts", "tests")
ROOT_FILES = ("README.md", "pyproject.toml", "alembic.ini", "docker-compose.yml", "Dockerfile")
INDEX_EXTENSIONS = {".css", ".html", ".js", ".json", ".md", ".py", ".toml", ".yaml", ".yml"}
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "playwright-report",
    "reports",
    "test-results",
    "chatbot_eval_runs",
    "coverage",
    "dist",
}
SENSITIVE_NAMES = {
    ".env",
    ".env.production",
    "credentials.json",
    "secrets.json",
}
SENSITIVE_NAME_PARTS = ("credential", "secret", "private_key", "access_token")
_SYMBOL = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_HIDDEN_PROMPT_MARKERS = (
    "system prompt",
    "developer prompt",
    "hidden prompt",
    "prompt_template",
    "planner instructions",
    "composer instructions",
    "you are the hilal",
)
_QUERYABLE_CLASSIFICATIONS = frozenset({"internal_code", "internal_test", "internal_documentation"})


class RepositoryEvidenceIndexService:
    """Maintenance-time indexing and query-time database reads only."""

    async def refresh(self, session: AsyncSession) -> dict[str, int]:
        candidates = _candidate_paths()
        existing = {
            item.path: item
            for item in (await session.scalars(select(RepositoryEvidenceIndex))).all()
        }
        seen: set[str] = set()
        changed = unchanged = excluded = 0
        commit = _current_commit()
        for path in candidates:
            relative = path.relative_to(REPOSITORY_ROOT).as_posix()
            seen.add(relative)
            try:
                raw = path.read_bytes()
            except OSError:
                excluded += 1
                continue
            if len(raw) > 512_000 or b"\x00" in raw:
                excluded += 1
                continue
            digest = hashlib.sha256(raw).hexdigest()
            current = existing.get(relative)
            if current is not None and current.content_hash == digest:
                unchanged += 1
                continue
            text = raw.decode("utf-8", errors="ignore")
            symbols = list(dict.fromkeys(_SYMBOL.findall(text)))[:200]
            refs = [
                {"symbol": symbol, "line": _symbol_line(text, symbol)} for symbol in symbols[:100]
            ]
            if current is None:
                current = RepositoryEvidenceIndex(path=relative)
                session.add(current)
            current.content_hash = digest
            current.updated_commit = commit
            current.symbol_names = symbols
            classification = _classification(relative, text)
            # Prompt-bearing implementation remains inventory-visible by path/hash/symbol,
            # but its hidden instructions are never available to an interactive agent.
            current.searchable_text = (
                redact_customer_text(text, limit=512_000)
                if classification in _QUERYABLE_CLASSIFICATIONS
                else ""
            )
            current.line_references = refs
            current.sensitivity_classification = classification
            current.indexed_at = datetime.now(UTC)
            changed += 1
        stale = set(existing) - seen
        if stale:
            await session.execute(
                delete(RepositoryEvidenceIndex).where(RepositoryEvidenceIndex.path.in_(stale))
            )
        await session.flush()
        return {
            "changed": changed,
            "unchanged": unchanged,
            "removed": len(stale),
            "excluded": excluded,
        }

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        limit: int = 20,
    ) -> EvidenceEnvelope:
        terms = [term for term in re.findall(r"[A-Za-z0-9_/-]{3,}", query)[:12]]
        if not terms:
            return EvidenceEnvelope(
                data=[],
                evidence_refs=[],
                freshness="index currentness unknown",
                coverage="none",
                limitations=["Provide a more specific repository search term."],
            )
        clauses = [
            RepositoryEvidenceIndex.searchable_text.ilike(f"%{_escape(term)}%", escape="\\")
            for term in terms
        ]
        rows = list(
            (
                await session.scalars(
                    select(RepositoryEvidenceIndex)
                    .where(
                        or_(*clauses),
                        RepositoryEvidenceIndex.sensitivity_classification.in_(
                            _QUERYABLE_CLASSIFICATIONS
                        ),
                    )
                    .order_by(RepositoryEvidenceIndex.indexed_at.desc())
                    .limit(max(1, min(limit, 50)))
                )
            ).all()
        )
        data: list[dict[str, Any]] = []
        refs: list[str] = []
        for row in rows:
            line, excerpt = _matching_excerpt(row.searchable_text, terms)
            ref = f"repo:{row.path}:{line}"
            refs.append(ref)
            data.append(
                {
                    "path": row.path,
                    "line": line,
                    "excerpt": excerpt,
                    "symbols": row.symbol_names[:20],
                    "content_hash": row.content_hash,
                    "updated_commit": row.updated_commit,
                    "evidence_ref": ref,
                }
            )
        latest = max((row.indexed_at for row in rows), default=None)
        return EvidenceEnvelope(
            data=data,
            evidence_refs=refs,
            freshness=latest.isoformat() if latest else "index empty",
            coverage=f"{len(rows)} indexed files matched",
            limitations=[]
            if rows
            else [
                "No indexed repository evidence matched. Run the maintenance indexer "
                "if the repository changed."
            ],
        )

    async def excerpt(
        self,
        session: AsyncSession,
        *,
        path: str,
        line: int = 1,
        lines: int = 30,
    ) -> EvidenceEnvelope:
        row = await session.scalar(
            select(RepositoryEvidenceIndex).where(RepositoryEvidenceIndex.path == path)
        )
        if row is None or row.sensitivity_classification not in _QUERYABLE_CLASSIFICATIONS:
            return EvidenceEnvelope(
                data=None,
                evidence_refs=[],
                freshness="unavailable",
                coverage="none",
                limitations=["The path is not present in the authorized repository index."],
            )
        all_lines = row.searchable_text.splitlines()
        start = max(0, line - 1)
        stop = min(len(all_lines), start + max(1, min(lines, 80)))
        ref = f"repo:{row.path}:{start + 1}"
        return EvidenceEnvelope(
            data={
                "path": row.path,
                "line": start + 1,
                "excerpt": "\n".join(all_lines[start:stop]),
                "content_hash": row.content_hash,
            },
            evidence_refs=[ref],
            freshness=row.indexed_at.isoformat(),
            coverage=f"lines {start + 1}-{stop} of {len(all_lines)}",
            limitations=["Excerpt comes from the maintained index, not a live filesystem read."],
        )


def _candidate_paths() -> list[Path]:
    paths = [REPOSITORY_ROOT / name for name in ROOT_FILES]
    for root_name in INDEX_ROOTS:
        root = REPOSITORY_ROOT / root_name
        if root.exists():
            paths.extend(root.rglob("*"))
    return sorted(
        path
        for path in set(paths)
        if path.is_file()
        and path.suffix.casefold() in INDEX_EXTENSIONS
        and path.name.casefold() not in SENSITIVE_NAMES
        and not path.name.casefold().startswith(".env")
        and not any(part in path.name.casefold() for part in SENSITIVE_NAME_PARTS)
        and not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def _current_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()[:64]
    except (OSError, subprocess.SubprocessError):
        return None


def _classification(path: str, text: str) -> str:
    normalized = text.casefold()
    if any(marker in normalized for marker in _HIDDEN_PROMPT_MARKERS):
        return "restricted_prompt"
    if path.startswith("tests/"):
        return "internal_test"
    if path.startswith("docs/") or path.endswith("README.md"):
        return "internal_documentation"
    return "internal_code"


def _symbol_line(text: str, symbol: str) -> int:
    for index, line in enumerate(text.splitlines(), 1):
        if re.search(rf"\b{re.escape(symbol)}\b", line):
            return index
    return 1


def _matching_excerpt(text: str, terms: list[str]) -> tuple[int, str]:
    lines = text.splitlines()
    index = next(
        (
            i
            for i, line in enumerate(lines)
            if any(term.casefold() in line.casefold() for term in terms)
        ),
        0,
    )
    start = max(0, index - 2)
    return index + 1, "\n".join(lines[start : index + 5])[:2400]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
