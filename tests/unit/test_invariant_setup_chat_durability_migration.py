"""The durability migration applies, enforces its constraint, and reverses.

The repository's full ``alembic upgrade head`` cannot run on SQLite: an older migration
creates a PostgreSQL ``plpgsql`` function, which SQLite rejects, so every migration
after it is unreachable in the test environment. That is a pre-existing condition, and
it would silently hide whether *this* migration is sound.

So the pre-migration shape of the tables is built directly, the migration is run against
it, and the result is inspected — including the constraint that stops two mutating turns
owning one chat session. That constraint is the whole concurrency guarantee, so it is
proved against the database rather than against the code that respects it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path("alembic/versions/b1c93d7e5a26_add_setup_chat_durability.py")

#: Every column the durable turn record gained. Named here so a column dropped from the
#: migration but kept on the model — or the reverse — fails loudly.
NEW_TURN_COLUMNS = frozenset(
    {
        "request_fingerprint",
        "session_claim",
        "is_mutating",
        "executable_hash_before",
        "workflow_state_hash_before",
        "prompt_version",
        "schema_version",
        "planner_usage_json",
        "provider_request_id",
        "stage_timestamps_json",
        "lease_owner",
        "lease_expires_at",
        "recovery_attempts",
        "recovery_disposition",
        "recovery_usage_json",
    }
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("setup_chat_durability", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pre_migration_schema(engine: sa.Engine) -> None:
    """The tables exactly as they were before this migration ran."""

    metadata = sa.MetaData()
    for name in ("users", "ai_setup_chat_sessions", "ai_setup_chat_messages"):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table(
        "setup_chat_turns",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_session_id", sa.Uuid(), sa.ForeignKey("ai_setup_chat_sessions.id")),
        sa.Column("client_message_id", sa.String(80), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), sa.ForeignKey("ai_setup_chat_messages.id")),
        sa.Column("assistant_message_id", sa.Uuid(), sa.ForeignKey("ai_setup_chat_messages.id")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("planner_model", sa.String(120)),
        sa.Column("plan_json", sa.JSON()),
        sa.Column("execution_result_json", sa.JSON()),
        sa.Column("reply_json", sa.JSON()),
        sa.Column("telemetry_json", sa.JSON()),
        sa.Column("mutation_committed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("executable_version_before", sa.Integer()),
        sa.Column("executable_version_after", sa.Integer()),
        sa.Column("workflow_revision_before", sa.Integer()),
        sa.Column("workflow_revision_after", sa.Integer()),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("failure_stage", sa.String(80)),
        sa.Column("failure_retryable", sa.Boolean()),
        sa.Column("failure_details_json", sa.JSON()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "chat_session_id",
            "client_message_id",
            name="uq_setup_chat_turn_session_client_message",
        ),
    )
    metadata.create_all(engine)


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(engine).get_columns(table)}


@pytest.fixture
def migrated() -> Any:
    engine = sa.create_engine("sqlite://")
    _pre_migration_schema(engine)
    before = _columns(engine, "setup_chat_turns")
    module = _load_migration()
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        module.upgrade()
    return engine, before, module


def test_the_migration_adds_every_durable_checkpoint_column(migrated) -> None:
    engine, before, _ = migrated
    assert not (NEW_TURN_COLUMNS & before), "the pre-migration shape was built wrong"
    missing = NEW_TURN_COLUMNS - _columns(engine, "setup_chat_turns")
    assert not missing, f"the migration did not add: {sorted(missing)}"


def test_the_migration_matches_the_model(migrated) -> None:
    """A column on one side and not the other is a schema that cannot be deployed."""

    from ai_market_monitor.db.models import SetupChatTurn

    engine, _, _ = migrated
    model_columns = {item.name for item in SetupChatTurn.__table__.columns}
    assert model_columns >= NEW_TURN_COLUMNS, "the model is missing a migrated column"
    assert _columns(engine, "setup_chat_turns") >= NEW_TURN_COLUMNS


def test_the_proposal_table_is_created(migrated) -> None:
    engine, _, _ = migrated
    assert "setup_chat_pending_changes" in set(sa.inspect(engine).get_table_names())


def test_the_claim_constraint_and_lease_index_exist(migrated) -> None:
    engine, _, _ = migrated
    uniques = {
        item["name"] for item in sa.inspect(engine).get_unique_constraints("setup_chat_turns")
    }
    assert "uq_setup_chat_turn_active_claim" in uniques
    indexes = {item["name"] for item in sa.inspect(engine).get_indexes("setup_chat_turns")}
    assert "ix_setup_chat_turn_lease" in indexes


def test_the_database_itself_refuses_two_live_claims_on_one_session(migrated) -> None:
    """This constraint is the concurrency guarantee. It is proved, not assumed."""

    engine, _, _ = migrated
    session_id = "11111111-1111-1111-1111-111111111111"
    insert = sa.text(
        "INSERT INTO setup_chat_turns "
        "(id, chat_session_id, client_message_id, status, session_claim, is_mutating, "
        " mutation_committed, retry_count, recovery_attempts, created_at, updated_at) "
        "VALUES (:id, :sid, :cmid, 'RECEIVED', :claim, 1, 0, 0, 0, "
        " '2026-01-01', '2026-01-01')"
    )
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO ai_setup_chat_sessions (id) VALUES (:sid)").bindparams(
                sid=session_id
            )
        )
        connection.execute(
            insert.bindparams(
                id="22222221-1111-1111-1111-111111111111",
                sid=session_id,
                cmid="cm-claim-one",
                claim=session_id,
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                insert.bindparams(
                    id="22222222-1111-1111-1111-111111111111",
                    sid=session_id,
                    cmid="cm-claim-two",
                    claim=session_id,
                )
            )


def test_a_settled_turn_releases_its_claim_so_the_next_one_can_start(migrated) -> None:
    """NULL is not unique, so any number of finished turns can sit beside each other."""

    engine, _, _ = migrated
    session_id = "33333333-1111-1111-1111-111111111111"
    insert = sa.text(
        "INSERT INTO setup_chat_turns "
        "(id, chat_session_id, client_message_id, status, session_claim, is_mutating, "
        " mutation_committed, retry_count, recovery_attempts, created_at, updated_at) "
        "VALUES (:id, :sid, :cmid, 'COMPLETED', NULL, 1, 0, 0, 0, "
        " '2026-01-01', '2026-01-01')"
    )
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO ai_setup_chat_sessions (id) VALUES (:sid)").bindparams(
                sid=session_id
            )
        )
        for index in range(3):
            connection.execute(
                insert.bindparams(
                    id=f"4444444{index}-1111-1111-1111-111111111111",
                    sid=session_id,
                    cmid=f"cm-settled-{index}",
                )
            )
        total = connection.scalar(
            sa.text("SELECT COUNT(*) FROM setup_chat_turns WHERE chat_session_id = :sid")
            .bindparams(sid=session_id)
        )
        assert total == 3


def test_the_migration_reverses_cleanly(migrated) -> None:
    """A migration that cannot be undone cannot be safely deployed."""

    engine, before, module = migrated
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        module.downgrade()
    assert _columns(engine, "setup_chat_turns") == before
    assert "setup_chat_pending_changes" not in set(sa.inspect(engine).get_table_names())


def test_existing_rows_survive_the_migration_without_a_claim() -> None:
    """A turn in flight when this deploys must not be given an invented claim.

    Inventing one would block its own session until a recovery cycle cleared it. It is
    left unclaimed, and the recovery worker settles it on the next pass.
    """

    engine = sa.create_engine("sqlite://")
    _pre_migration_schema(engine)
    session_id = "55555555-1111-1111-1111-111111111111"
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO ai_setup_chat_sessions (id) VALUES (:sid)").bindparams(
                sid=session_id
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO setup_chat_turns "
                "(id, chat_session_id, client_message_id, status, mutation_committed, "
                " retry_count, created_at, updated_at) "
                "VALUES (:id, :sid, 'cm-legacy-turn', 'EXECUTING', 0, 0, "
                " '2026-01-01', '2026-01-01')"
            ).bindparams(id="66666666-1111-1111-1111-111111111111", sid=session_id)
        )

    module = _load_migration()
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        module.upgrade()

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT status, session_claim, recovery_attempts, is_mutating "
                "FROM setup_chat_turns WHERE client_message_id = 'cm-legacy-turn'"
            )
        ).one()
    assert row.status == "EXECUTING", "the old row keeps its state"
    assert row.session_claim is None, "no claim is invented for it"
    assert row.recovery_attempts == 0
    assert row.is_mutating == 1, "existing turns default to mutating, which fails closed"
