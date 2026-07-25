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


def _run_alembic_downgrade(
    repo_root: Path,
    env: dict[str, str],
    revision: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", revision],
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
        repo_root
        / "alembic"
        / "versions"
        / "6f02832495ab_add_fasset_and_aggregate_methodologies.py",
        repo_root
        / "alembic"
        / "versions"
        / "70a1395b26cf_add_system_brain_user_controls.py",
        repo_root
        / "alembic"
        / "versions"
        / "81b24a6c37de_add_methodology_import_pack_metadata.py",
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
              AND status = 'active'
            """
        ).fetchone()
        additional_methodologies = connection.execute(
            """
            SELECT code, name, status, rules_json
            FROM sharia_methodologies
            WHERE code IN (
                'ALL_APPROVED_METHODOLOGIES',
                'FASSET_SHARIAH_REPORTS',
                'SHARIAH_REVIEW_BUREAU'
            )
              AND status = 'active'
            ORDER BY code
            """
        ).fetchall()
        development_methodology = connection.execute(
            """
            SELECT status
            FROM sharia_methodologies
            WHERE code = 'TRACEDGE_DEV_TEST_V1'
            """
        ).fetchone()
        external_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('external_assessments')"
            ).fetchall()
        }
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
        "account_bans",
        "account_admin_actions",
        "account_email_deliveries",
    }.issubset(table_names)
    assert methodology == (
        "SC_MALAYSIA_SAC_REFERENCE",
        "2026.07-pack.1",
        "active",
    )
    assert [(row[0], row[1], row[2]) for row in additional_methodologies] == [
        ("ALL_APPROVED_METHODOLOGIES", "All", "active"),
        (
            "FASSET_SHARIAH_REPORTS",
            "Fasset Shariah Reports",
            "active",
        ),
        (
            "SHARIAH_REVIEW_BUREAU",
            "Shariah Review Bureau",
            "active",
        ),
    ]
    assert '"aggregate_view": true' in additional_methodologies[0][3]
    assert '"source_adapter": "fasset"' in additional_methodologies[1][3]
    assert '"source_adapter": "srb"' in additional_methodologies[2][3]
    assert development_methodology == ("archived",)
    assert {
        "source_family",
        "source_reference",
        "structured_facts",
        "methodology_id",
        "source_row_id",
        "rights_state",
        "commercial_display_allowed",
        "source_detail_extraction_state",
        "source_detail_snapshot_id",
        "source_detail_fields",
        "passport_seed_snapshot",
        "enrichment_state",
    }.issubset(external_columns)
    assert canonical_assets == 0
    assert published_assets == 0


def test_methodology_import_pack_migration_round_trip_restores_prior_version(
    tmp_path,
):
    repo_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "methodology-pack-round-trip.sqlite3"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "APP_SECRET_KEY": (
                "test-secret-key-with-at-least-thirty-two-characters"
            ),
            "DATABASE_URL": (
                f"sqlite+aiosqlite:///{database_path.as_posix()}"
            ),
            "ALLOW_MOCK_PROVIDERS": "true",
        }
    )

    _run_alembic(repo_root, env, "head")
    _run_alembic_downgrade(repo_root, env, "70a1395b26cf")

    with sqlite3.connect(database_path) as connection:
        active_rows = connection.execute(
            """
            SELECT code, version
            FROM sharia_methodologies
            WHERE status = 'active'
              AND code IN (
                'SC_MALAYSIA_SAC_REFERENCE',
                'FASSET_SHARIAH_REPORTS',
                'SHARIAH_REVIEW_BUREAU'
              )
            ORDER BY code
            """
        ).fetchall()
        external_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('external_assessments')"
            ).fetchall()
        }

    assert active_rows == [
        ("FASSET_SHARIAH_REPORTS", "2026.07"),
        ("SC_MALAYSIA_SAC_REFERENCE", "2026.03"),
    ]
    assert "source_row_id" not in external_columns
    assert "enrichment_state" not in external_columns

    _run_alembic(repo_root, env, "head")


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
    assert development[0] == "archived"
    assert development[1].startswith("TRACEDGE_DEV_TEST_")
    assert '"executable": false' in development[2]
