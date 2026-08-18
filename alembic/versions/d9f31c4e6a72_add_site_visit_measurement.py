"""count public-site visits, and make a bulk review decision undoable

The product could not answer "how many people looked at the site", "how long did they
stay" or "what did they do next". The only measurement was Google Tag Manager, which is
somebody else's system, needs cookie permission, and cannot be read from the System
Brain.

Two tables. ``site_visits`` holds one row per page instance: who (as a one-way daily
hash), which page, how long the page was really in front of them, and what they did
next. ``site_signup_attributions`` holds one row per account created, so the sign-up
count is the real number of accounts rather than the number of clicks on a button.

A third table, ``review_action_batches``, is what makes the Cases page's quick decision
safe: a decision taken over several cases at once writes down the state each case came
from, so Undo puts every one of them back exactly where it was. Undo never removes a
recorded decision — the history stays whole.

Revision ID: d9f31c4e6a72
Revises: c8e5a2f14b70
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9f31c4e6a72"
down_revision: str | None = "c8e5a2f14b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_visits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("visitor_key", sa.String(length=64), nullable=False),
        sa.Column("session_key", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("is_landing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("referrer_host", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="direct"),
        sa.Column("campaign", sa.String(length=120), nullable=True),
        sa.Column("device", sa.String(length=16), nullable=False, server_default="desktop"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_action", sa.String(length=24), nullable=True),
        sa.Column("next_action_detail", sa.String(length=200), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # A retried beacon must find the row it already wrote rather than write a second one.
    op.create_index("ix_site_visit_session", "site_visits", ["session_key"], unique=True)
    op.create_index("ix_site_visit_started", "site_visits", ["started_at"])
    op.create_index("ix_site_visit_visitor", "site_visits", ["visitor_key", "started_at"])

    op.create_table(
        "site_signup_attributions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_key", sa.String(length=64), nullable=True),
        sa.Column("entry_path", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="direct"),
        sa.Column("campaign", sa.String(length=120), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_site_signup_user", "site_signup_attributions", ["user_id"], unique=True)
    op.create_index("ix_site_signup_created", "site_signup_attributions", ["created_at"])

    # One quick decision taken over several cases, with the state each case came from.
    # Undo reads that state; without it an undo would have to guess where to put a case
    # back, and guessing is how a governed record becomes wrong quietly.
    op.create_table(
        "review_action_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("applied_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undo_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_batch_actor_created",
        "review_action_batches",
        ["actor_user_id", "created_at"],
    )


    _create_hilal_conversation_triggers()


def _is_postgresql() -> bool:
    """Only PostgreSQL is given the convenience triggers.

    The offline test suites run on SQLite, which has no ``LANGUAGE plpgsql``. An
    unguarded trigger block stops the migration with a syntax error and every SQLite
    suite then fails before its first test.
    """

    return op.get_bind().dialect.name == "postgresql"


def _create_hilal_conversation_triggers() -> None:
    """Put Hilal's conversations on the same live feed as the other assistant.

    The System Brain conversation log streams new customer messages as they arrive. Two
    assistants already fed it; Hilal — the assistant inside the dashboard — did not, so
    its conversations appeared only after a manual refresh. Same table, same event names,
    one new source type.
    """

    if not _is_postgresql():
        return
    statements = (
        """
        CREATE FUNCTION hm_emit_hilal_conversation_event() RETURNS trigger AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN RETURN NEW; END IF;
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('dashboard_hilal_agent', NEW.id, 'conversation_created', NULL, now());
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_hilal_conversation_admin_event
        AFTER INSERT ON hilal_chat_conversations
        FOR EACH ROW EXECUTE FUNCTION hm_emit_hilal_conversation_event()
        """,
        """
        CREATE FUNCTION hm_emit_hilal_message_event() RETURNS trigger AS $$
        BEGIN
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('dashboard_hilal_agent', NEW.conversation_id, 'message_persisted',
                  NEW.id, NEW.created_at);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_hilal_message_admin_event
        AFTER INSERT ON hilal_chat_messages
        FOR EACH ROW EXECUTE FUNCTION hm_emit_hilal_message_event()
        """,
        """
        CREATE FUNCTION hm_emit_hilal_report_event() RETURNS trigger AS $$
        BEGIN
          INSERT INTO customer_conversation_events
            (source_type, conversation_id, event_type, message_id, occurred_at)
          VALUES ('dashboard_hilal_agent', NEW.conversation_id, 'turn_failed',
                  NEW.message_id, NEW.created_at);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        """
        CREATE TRIGGER tr_hilal_report_admin_event
        AFTER INSERT ON hilal_chat_message_reports
        FOR EACH ROW EXECUTE FUNCTION hm_emit_hilal_report_event()
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    if _is_postgresql():
        for statement in (
            "DROP TRIGGER IF EXISTS tr_hilal_report_admin_event ON hilal_chat_message_reports",
            "DROP TRIGGER IF EXISTS tr_hilal_message_admin_event ON hilal_chat_messages",
            "DROP TRIGGER IF EXISTS tr_hilal_conversation_admin_event"
            " ON hilal_chat_conversations",
            "DROP FUNCTION IF EXISTS hm_emit_hilal_report_event()",
            "DROP FUNCTION IF EXISTS hm_emit_hilal_message_event()",
            "DROP FUNCTION IF EXISTS hm_emit_hilal_conversation_event()",
        ):
            op.execute(statement)
    op.drop_index("ix_review_batch_actor_created", table_name="review_action_batches")
    op.drop_table("review_action_batches")
    op.drop_index("ix_site_signup_created", table_name="site_signup_attributions")
    op.drop_index("ix_site_signup_user", table_name="site_signup_attributions")
    op.drop_table("site_signup_attributions")
    op.drop_index("ix_site_visit_visitor", table_name="site_visits")
    op.drop_index("ix_site_visit_started", table_name="site_visits")
    op.drop_index("ix_site_visit_session", table_name="site_visits")
    op.drop_table("site_visits")
