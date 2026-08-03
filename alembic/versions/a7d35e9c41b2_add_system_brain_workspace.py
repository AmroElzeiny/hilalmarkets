"""Add persistent System Brain workspace and customer conversation event stream.

Revision ID: a7d35e9c41b2
Revises: f6c24d8a10b7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7d35e9c41b2"
down_revision: str | None = "f6c24d8a10b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_brain_conversations",
        sa.Column("admin_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_brain_conversation_admin_updated",
        "system_brain_conversations",
        ["admin_user_id", "updated_at"],
    )
    op.create_index(
        "ix_system_brain_conversation_admin_archived",
        "system_brain_conversations",
        ["admin_user_id", "archived_at"],
    )
    op.create_index(
        "ix_system_brain_conversations_admin_user_id",
        "system_brain_conversations",
        ["admin_user_id"],
    )

    op.create_table(
        "system_brain_messages",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("client_message_id", sa.String(length=120), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("metadata_redacted", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["system_brain_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_system_brain_message_sequence"),
        sa.UniqueConstraint(
            "conversation_id", "client_message_id", name="uq_system_brain_message_client_id"
        ),
    )
    op.create_index(
        "ix_system_brain_message_conversation_created",
        "system_brain_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_system_brain_message_status_created",
        "system_brain_messages",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_system_brain_messages_agent_run_id",
        "system_brain_messages",
        ["agent_run_id"],
    )

    op.create_table(
        "customer_conversation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_conversation_event_source_cursor",
        "customer_conversation_events",
        ["source_type", "id"],
    )
    op.create_index(
        "ix_customer_conversation_event_conversation_cursor",
        "customer_conversation_events",
        ["conversation_id", "id"],
    )

    op.create_table(
        "system_brain_artifacts",
        sa.Column("admin_user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("artifact_kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content_redacted", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["system_brain_conversations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_brain_artifact_admin_kind",
        "system_brain_artifacts",
        ["admin_user_id", "artifact_kind"],
    )
    op.create_index(
        "ix_system_brain_artifact_conversation_created",
        "system_brain_artifacts",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "system_brain_action_proposals",
        sa.Column("admin_user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("exact_changes", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("rollback_path", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("confirmation_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_result_redacted", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["system_brain_conversations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_system_brain_action_proposal_key"),
    )
    op.create_index(
        "ix_system_brain_action_proposal_admin_status",
        "system_brain_action_proposals",
        ["admin_user_id", "status"],
    )

    op.create_table(
        "repository_evidence_index",
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_commit", sa.String(length=64), nullable=True),
        sa.Column("symbol_names", sa.JSON(), nullable=False),
        sa.Column("searchable_text", sa.Text(), nullable=False),
        sa.Column("line_references", sa.JSON(), nullable=False),
        sa.Column("sensitivity_classification", sa.String(length=32), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path", name="uq_repository_evidence_path"),
    )
    op.create_index("ix_repository_evidence_hash", "repository_evidence_index", ["content_hash"])
    op.create_index(
        "ix_repository_evidence_sensitivity",
        "repository_evidence_index",
        ["sensitivity_classification"],
    )

    op.create_table(
        "public_chat_messages",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(length=40), nullable=False),
        sa.Column("telemetry_redacted", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["public_chat_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["turn_id"], ["public_chat_turns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_public_chat_message_sequence"),
    )
    op.create_index(
        "ix_public_chat_message_conversation_created",
        "public_chat_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_public_chat_message_retain_until",
        "public_chat_messages",
        ["retain_until"],
    )
    op.create_index("ix_public_chat_messages_turn_id", "public_chat_messages", ["turn_id"])

    _create_event_triggers()


def _create_event_triggers() -> None:
    statements = (
        """
        CREATE FUNCTION hm_emit_setup_session_event() RETURNS trigger AS $$
        DECLARE event_name text;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            event_name := 'conversation_created';
          ELSIF NEW.approved_at IS DISTINCT FROM OLD.approved_at AND NEW.approved_at IS NOT NULL THEN
            event_name := 'approval_occurred';
          ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
            event_name := 'lifecycle_changed';
          ELSE
            RETURN NEW;
          END IF;
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('authenticated_setup_chat', NEW.id, event_name, NULL, now());
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_setup_session_admin_event
        AFTER INSERT OR UPDATE ON ai_setup_chat_sessions
        FOR EACH ROW EXECUTE FUNCTION hm_emit_setup_session_event()
        """,
        """
        CREATE FUNCTION hm_emit_setup_message_event() RETURNS trigger AS $$
        BEGIN
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('authenticated_setup_chat', NEW.session_id, 'message_persisted', NEW.id, NEW.created_at);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_setup_message_admin_event
        AFTER INSERT ON ai_setup_chat_messages
        FOR EACH ROW EXECUTE FUNCTION hm_emit_setup_message_event()
        """,
        """
        CREATE FUNCTION hm_emit_setup_turn_event() RETURNS trigger AS $$
        DECLARE event_name text;
        BEGIN
          IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
          IF NEW.status IN ('FAILED', 'failed') THEN event_name := 'turn_failed';
          ELSIF NEW.status IN ('COMPLETED', 'completed') THEN event_name := 'turn_completed';
          ELSE RETURN NEW;
          END IF;
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('authenticated_setup_chat', NEW.chat_session_id, event_name,
                  NEW.assistant_message_id, now());
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_setup_turn_admin_event
        AFTER UPDATE ON setup_chat_turns
        FOR EACH ROW EXECUTE FUNCTION hm_emit_setup_turn_event()
        """,
        """
        CREATE FUNCTION hm_emit_public_conversation_event() RETURNS trigger AS $$
        DECLARE event_name text;
        BEGIN
          IF TG_OP = 'INSERT' THEN event_name := 'conversation_created';
          ELSIF NEW.stage IS DISTINCT FROM OLD.stage THEN event_name := 'lifecycle_changed';
          ELSE RETURN NEW;
          END IF;
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('public_site_chat', NEW.id, event_name, NULL, now());
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_public_conversation_admin_event
        AFTER INSERT OR UPDATE ON public_chat_conversations
        FOR EACH ROW EXECUTE FUNCTION hm_emit_public_conversation_event()
        """,
        """
        CREATE FUNCTION hm_emit_public_message_event() RETURNS trigger AS $$
        BEGIN
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('public_site_chat', NEW.conversation_id, 'message_persisted', NEW.id, NEW.created_at);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_public_message_admin_event
        AFTER INSERT ON public_chat_messages
        FOR EACH ROW EXECUTE FUNCTION hm_emit_public_message_event()
        """,
        """
        CREATE FUNCTION hm_emit_public_turn_event() RETURNS trigger AS $$
        DECLARE event_name text;
        BEGIN
          IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
          IF NEW.status = 'completed' THEN event_name := 'turn_completed';
          ELSIF NEW.status = 'failed' OR NEW.error_type IS NOT NULL THEN event_name := 'turn_failed';
          ELSE RETURN NEW;
          END IF;
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('public_site_chat', NEW.conversation_id, event_name, NULL, now());
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_public_turn_admin_event
        AFTER UPDATE ON public_chat_turns
        FOR EACH ROW EXECUTE FUNCTION hm_emit_public_turn_event()
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    for trigger, table in (
        ("tr_public_turn_admin_event", "public_chat_turns"),
        ("tr_public_message_admin_event", "public_chat_messages"),
        ("tr_public_conversation_admin_event", "public_chat_conversations"),
        ("tr_setup_turn_admin_event", "setup_chat_turns"),
        ("tr_setup_message_admin_event", "ai_setup_chat_messages"),
        ("tr_setup_session_admin_event", "ai_setup_chat_sessions"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in (
        "hm_emit_public_turn_event",
        "hm_emit_public_message_event",
        "hm_emit_public_conversation_event",
        "hm_emit_setup_turn_event",
        "hm_emit_setup_message_event",
        "hm_emit_setup_session_event",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")
    op.drop_index("ix_public_chat_messages_turn_id", table_name="public_chat_messages")
    op.drop_index("ix_public_chat_message_retain_until", table_name="public_chat_messages")
    op.drop_index("ix_public_chat_message_conversation_created", table_name="public_chat_messages")
    op.drop_table("public_chat_messages")
    op.drop_index("ix_repository_evidence_sensitivity", table_name="repository_evidence_index")
    op.drop_index("ix_repository_evidence_hash", table_name="repository_evidence_index")
    op.drop_table("repository_evidence_index")
    op.drop_index(
        "ix_system_brain_action_proposal_admin_status",
        table_name="system_brain_action_proposals",
    )
    op.drop_table("system_brain_action_proposals")
    op.drop_index(
        "ix_system_brain_artifact_conversation_created", table_name="system_brain_artifacts"
    )
    op.drop_index("ix_system_brain_artifact_admin_kind", table_name="system_brain_artifacts")
    op.drop_table("system_brain_artifacts")
    op.drop_index(
        "ix_customer_conversation_event_conversation_cursor",
        table_name="customer_conversation_events",
    )
    op.drop_index(
        "ix_customer_conversation_event_source_cursor",
        table_name="customer_conversation_events",
    )
    op.drop_table("customer_conversation_events")
    op.drop_index("ix_system_brain_messages_agent_run_id", table_name="system_brain_messages")
    op.drop_index("ix_system_brain_message_status_created", table_name="system_brain_messages")
    op.drop_index(
        "ix_system_brain_message_conversation_created", table_name="system_brain_messages"
    )
    op.drop_table("system_brain_messages")
    op.drop_index(
        "ix_system_brain_conversations_admin_user_id",
        table_name="system_brain_conversations",
    )
    op.drop_index(
        "ix_system_brain_conversation_admin_archived",
        table_name="system_brain_conversations",
    )
    op.drop_index(
        "ix_system_brain_conversation_admin_updated",
        table_name="system_brain_conversations",
    )
    op.drop_table("system_brain_conversations")
