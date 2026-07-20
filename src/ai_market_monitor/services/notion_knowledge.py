from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ai_market_monitor.core.config import Settings

_ALLOWED_SUFFIXES = frozenset({".csv", ".json", ".md", ".txt"})
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_SECRET_LINE_RE = re.compile(
    r"^[^\r\n:=]{0,160}"
    r"(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|password|private[_ -]?key)"
    r"[^\r\n:=]{0,40}\s*[:=]\s*[^\r\n]+$",
    re.IGNORECASE | re.MULTILINE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "and",
        "are",
        "can",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "the",
        "to",
        "what",
        "when",
        "where",
        "why",
        "with",
        "you",
    }
)


@dataclass(frozen=True, slots=True)
class NotionKnowledgeDocument:
    source_id: str
    relative_path: str
    title: str
    content: str
    content_hash: str
    tokens: frozenset[str]


class NotionKnowledgeService:
    """Bounded read-only retrieval from the project-owned Notion export."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.public_chat_notion_root).expanduser().resolve()
        self.documents = (
            _load_documents(
                str(self.root),
                _workspace_signature(self.root),
                settings.public_chat_notion_max_file_bytes,
            )
            if settings.public_chat_notion_enabled
            else ()
        )

    @property
    def available(self) -> bool:
        return bool(self.documents)

    def retrieve(
        self,
        question: str,
        *,
        previous_source_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        query_tokens = _tokens(question)
        previous = set(previous_source_ids or [])
        ranked = sorted(
            (
                (
                    _document_score(question, query_tokens, document)
                    + (0.10 if document.source_id in previous else 0),
                    document,
                )
                for document in self.documents
            ),
            key=lambda item: (item[0], item[1].relative_path),
            reverse=True,
        )
        selected = [
            (score, document)
            for score, document in ranked
            if score > 0 or document.source_id in previous
        ][: self.settings.public_chat_notion_max_documents]
        remaining = self.settings.public_chat_notion_max_characters
        results: list[dict[str, Any]] = []
        for score, document in selected:
            if remaining <= 0:
                break
            snippet = _best_snippet(
                document.content,
                query_tokens,
                maximum=min(2400, remaining),
            )
            if not snippet:
                continue
            results.append(
                {
                    "source_id": document.source_id,
                    "title": document.title,
                    "content": snippet,
                    "relative_path": document.relative_path,
                    "content_hash": document.content_hash,
                    "authority": "context_only",
                    "retrieval_score": round(min(1.0, score), 5),
                }
            )
            remaining -= len(snippet)
        return results


def _workspace_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    if not root.is_dir():
        return ()
    rows: list[tuple[str, int, int]] = []
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.suffix.casefold() not in _ALLOWED_SUFFIXES
        ):
            continue
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(root).as_posix()
            stat = resolved.stat()
        except (OSError, ValueError):
            continue
        rows.append((relative, stat.st_size, stat.st_mtime_ns))
    return tuple(rows)


@lru_cache(maxsize=8)
def _load_documents(
    root_value: str,
    signature: tuple[tuple[str, int, int], ...],
    maximum_file_bytes: int,
) -> tuple[NotionKnowledgeDocument, ...]:
    root = Path(root_value)
    documents: list[NotionKnowledgeDocument] = []
    for relative_path, size, _mtime in signature:
        if size <= 0 or size > maximum_file_bytes:
            continue
        path = (root / relative_path).resolve()
        try:
            path.relative_to(root)
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except (OSError, UnicodeError, ValueError):
            continue
        content = _sanitize(raw)
        if not content:
            continue
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        slug = re.sub(r"[^a-z0-9]+", "-", Path(relative_path).stem.casefold()).strip("-")
        source_id = f"notion:{slug[:48]}:{content_hash[:12]}"
        documents.append(
            NotionKnowledgeDocument(
                source_id=source_id,
                relative_path=relative_path,
                title=_title(relative_path, content),
                content=content,
                content_hash=content_hash,
                tokens=frozenset(_tokens(content)),
            )
        )
    return tuple(documents)


def _sanitize(value: str) -> str:
    cleaned = _CONTROL_RE.sub("", value)
    cleaned = _SECRET_LINE_RE.sub("[redacted secret assignment]", cleaned)
    return cleaned.strip()


def _title(relative_path: str, content: str) -> str:
    for line in content.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate:
            return candidate[:180]
    return Path(relative_path).stem.replace("_", " ")[:180]


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(value)
        if len(token) > 1 and token.casefold() not in _STOPWORDS
    }


def _document_score(
    question: str,
    query_tokens: set[str],
    document: NotionKnowledgeDocument,
) -> float:
    if not query_tokens:
        return 0.0
    overlap = query_tokens & document.tokens
    score = len(overlap) / max(1, min(len(query_tokens), 8))
    lowered = question.casefold()
    if document.title.casefold() in lowered:
        score += 0.35
    if any(token in document.relative_path.casefold() for token in query_tokens):
        score += 0.08
    return min(1.0, score)


def _best_snippet(content: str, query_tokens: set[str], *, maximum: int) -> str:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
    if not blocks:
        return content[:maximum]
    ranked = sorted(
        (
            (len(query_tokens & _tokens(block)), index, block)
            for index, block in enumerate(blocks)
        ),
        key=lambda item: (item[0], -item[1]),
        reverse=True,
    )
    chosen = sorted(ranked[:3], key=lambda item: item[1])
    snippet = "\n\n".join(item[2] for item in chosen)
    return snippet[:maximum].strip()
