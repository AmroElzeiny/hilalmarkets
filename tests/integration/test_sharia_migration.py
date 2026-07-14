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
    migration_path = (
        repo_root
        / "alembic"
        / "versions"
        / "c5d6e7f8a9b0_add_sharia_first_product_layer.py"
    )
    migration_source = migration_path.read_text(encoding="utf-8")
    explicit_names = re.findall(
        r'"((?:fk|ix|uq|ck)_[A-Za-z0-9_]+)"',
        migration_source,
    )

    over_limit = sorted({name for name in explicit_names if len(name) > 63})
    assert over_limit == []


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
