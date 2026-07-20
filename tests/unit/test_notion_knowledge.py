from pathlib import Path

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.notion_knowledge import NotionKnowledgeService


def _settings(root: Path) -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        public_chat_notion_enabled=True,
        public_chat_notion_root=str(root),
        public_chat_notion_max_documents=3,
        public_chat_notion_max_characters=2000,
        public_chat_notion_max_file_bytes=4096,
    )


def test_notion_context_is_bounded_read_only_and_redacts_secret_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "Notion"
    workspace.mkdir()
    source = workspace / "Evidence Passports.md"
    source.write_text(
        "# Evidence Passports\n\n"
        "A Passport records reviewed methodology evidence and scope.\n\n"
        "API_KEY=must-not-reach-the-model\n"
        "OPENAI_API_KEY=must-also-stay-private\n"
        '"client_secret": "never-send-this-either"\n',
        encoding="utf-8",
    )
    (workspace / "ignored.py").write_text("SECRET = 'ignored'", encoding="utf-8")

    service = NotionKnowledgeService(_settings(workspace))
    result = service.retrieve("What does an Evidence Passport record?")

    assert service.available is True
    assert len(result) == 1
    assert result[0]["authority"] == "context_only"
    assert result[0]["relative_path"] == "Evidence Passports.md"
    assert "must-not-reach-the-model" not in result[0]["content"]
    assert "must-also-stay-private" not in result[0]["content"]
    assert "never-send-this-either" not in result[0]["content"]
    assert "[redacted secret assignment]" in result[0]["content"]
    assert not hasattr(service, "write")


def test_notion_context_rejects_symlinks_and_oversized_files(tmp_path: Path) -> None:
    workspace = tmp_path / "Notion"
    workspace.mkdir()
    (workspace / "oversized.md").write_text("x" * 5000, encoding="utf-8")

    service = NotionKnowledgeService(_settings(workspace))

    assert service.available is False
    assert service.retrieve("oversized") == []
