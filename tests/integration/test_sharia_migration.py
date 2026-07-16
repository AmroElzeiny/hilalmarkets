import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def _run_alembic(repo_root: Path, env: dict[str, str], revision: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sharia_migration_identifiers_fit_postgresql_limit():
    repo_root = Path(__file__).resolve().parents[2]
    migration_paths = [
        repo_root
        / "alembic"
        / "versions"
        / "c5d6e7f8a9b0_add_sharia_first_product_layer.py",
        repo_root
        / "alembic"
        / "versions"
        / "e7f8a9b0c1d2_add_passport_governance_checkout_email.py",
    ]
    explicit_names = [
        name
        for migration_path in migration_paths
        for name in re.findall(
            r'"((?:fk|ix|uq|ck)_[A-Za-z0-9_]+)"',
            migration_path.read_text(encoding="utf-8"),
        )
    ]

    over_limit = sorted({name for name in explicit_names if len(name) > 63})
    assert over_limit == []


def test_sc_governance_migration_reaches_head_and_seeds_no_assets(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "sc-governance.sqlite3"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "APP_SECRET_KEY": "test-secret-key-with-at-least-thirty-two-characters",
            "DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}",
            "ALLOW_MOCK_PROVIDERS": "true",
        }
    )

    _run_alembic(repo_root, env, "head")

    with sqlite3.connect(database_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        methodology = connection.execute(
            """
            SELECT code, version, status
            FROM sharia_methodologies
            WHERE code = 'SC_MALAYSIA_SAC_REFERENCE'
            """
        ).fetchone()
        canonical_assets = connection.execute(
            "SELECT COUNT(*) FROM canonical_assets"
        ).fetchone()[0]
        published_assets = connection.execute(
            "SELECT COUNT(*) FROM published_asset_assessments"
        ).fetchone()[0]

    assert {
        "canonical_assets",
        "external_assessments",
        "asset_research_dossiers",
        "sharia_review_cases",
        "published_asset_assessments",
        "source_change_events",
        "sharia_telegram_notification_attempts",
        "sharia_governance_role_grants",
        "sharia_reviewer_profiles",
        "sharia_review_assignment_events",
        "sharia_passport_problem_reports",
        "billing_checkout_attempts",
        "payment_email_deliveries",
    }.issubset(table_names)
    assert methodology == ("SC_MALAYSIA_SAC_REFERENCE", "2026.03", "active")
    assert canonical_assets == 0
    assert published_assets == 0


def test_sharia_migration_pauses_existing_active_monitors(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "sharia-migration.sqlite3"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "APP_SECRET_KEY": "test-secret-key-with-at-least-thirty-two-characters",
            "DATABASE_URL": f"sqlite+aiosqlite:///{database_path.as_posix()}",
            "ALLOW_MOCK_PROVIDERS": "true",
        }
    )
    _run_alembic(repo_root, env, "b4c5d6e7f8a9")

    user_id = uuid4().hex
    strategy_id = uuid4().hex
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO users (
                status, role, display_name, locale, timezone, id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("active", "user", "Migration user", "en", "UTC", user_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO strategies (
                user_id, name, status, activated_at, id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, "Existing active monitor", "active", now, strategy_id, now, now),
        )
        connection.commit()

    _run_alembic(repo_root, env, "head")

    with sqlite3.connect(database_path) as connection:
        status, paused_at = connection.execute(
            "SELECT status, paused_at FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()
        migration = connection.execute(
            """
            SELECT prior_status, action, reason
            FROM sharia_monitor_migration_records
            WHERE strategy_id = ?
            """,
            (strategy_id,),
        ).fetchone()
        development = connection.execute(
            """
            SELECT status, code, rules_json
            FROM sharia_methodologies
            WHERE code = 'TRACEDGE_DEV_TEST_V1'
            """
        ).fetchone()

    assert status == "paused"
    assert paused_at is not None
    assert migration is not None
    assert migration[0] == "active"
    assert migration[1] == "paused_pending_approved_methodology"
    assert "approved active methodology" in migration[2]
    assert development is not None
    assert development[0] == "draft"
    assert development[1].startswith("TRACEDGE_DEV_TEST_")
    assert '"executable": false' in development[2]
