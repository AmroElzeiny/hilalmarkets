"""add WhatsApp Cloud API channel state

Revision ID: 09bac1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-17 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "09bac1d2e3f4"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_IDENTITY_PROVIDERS = ("email", "telegram", "discord")
IDENTITY_PROVIDERS = ("email", "telegram", "whatsapp", "discord")
OLD_DELIVERY_CHANNELS = ("telegram", "discord", "web")
DELIVERY_CHANNELS = ("telegram", "whatsapp", "discord", "web")
CONNECTION_STATUSES = ("pending", "active", "revoked", "error")


def upgrade() -> None:
    with op.batch_alter_table("identity_link_tokens") as batch_op:
        batch_op.alter_column(
            "onboarding_session_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )
        batch_op.add_column(sa.Column("canceled_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column(
                "metadata_json",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )

    with op.batch_alter_table("user_identities") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.Enum(
                *OLD_IDENTITY_PROVIDERS,
                name="identity_provider",
                native_enum=False,
            ),
            type_=sa.Enum(
                *IDENTITY_PROVIDERS,
                name="identity_provider",
                native_enum=False,
            ),
            existing_nullable=False,
        )

    with op.batch_alter_table("alert_deliveries") as batch_op:
        batch_op.alter_column(
            "channel",
            existing_type=sa.Enum(
                *OLD_DELIVERY_CHANNELS,
                name="delivery_channel",
                native_enum=False,
            ),
            type_=sa.Enum(
                *DELIVERY_CHANNELS,
                name="delivery_channel",
                native_enum=False,
            ),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column("provider_status", sa.String(length=40)))
        batch_op.add_column(
            sa.Column(
                "provider_status_metadata",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("accepted_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("read_at", sa.DateTime(timezone=True)))

    with op.batch_alter_table("integration_test_results") as batch_op:
        batch_op.add_column(sa.Column("provider_message_id", sa.String(length=255)))
        batch_op.create_index(
            op.f("ix_integration_test_results_provider_message_id"),
            ["provider_message_id"],
            unique=False,
        )

    op.create_table(
        "whatsapp_connections",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("wa_id", sa.String(length=32), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("profile_name", sa.String(length=160)),
        sa.Column(
            "status",
            sa.Enum(
                *CONNECTION_STATUSES,
                name="whatsapp_connection_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("alerts_enabled", sa.Boolean(), nullable=False),
        sa.Column("preferred_locale", sa.String(length=16), nullable=False),
        sa.Column("opt_in_categories", sa.JSON(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True)),
        sa.Column("service_window_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(length=80)),
        sa.Column("opt_in_at", sa.DateTime(timezone=True)),
        sa.Column("opt_in_source", sa.String(length=80)),
        sa.Column("opt_in_version", sa.String(length=40)),
        sa.Column("opt_out_at", sa.DateTime(timezone=True)),
        sa.Column("opt_out_reason", sa.String(length=160)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_whatsapp_connections_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_whatsapp_connections")),
        sa.UniqueConstraint("phone_e164", name="uq_whatsapp_connection_phone"),
        sa.UniqueConstraint("user_id", name="uq_whatsapp_connection_user"),
        sa.UniqueConstraint("wa_id", name="uq_whatsapp_connection_wa_id"),
    )
    op.create_index(
        "ix_whatsapp_connection_status",
        "whatsapp_connections",
        ["status", "alerts_enabled"],
    )
    op.create_index(
        op.f("ix_whatsapp_connections_user_id"),
        "whatsapp_connections",
        ["user_id"],
    )

    op.create_table(
        "whatsapp_conversation_states",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("wa_id", sa.String(length=32), nullable=False),
        sa.Column("flow", sa.String(length=64), nullable=False),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("state_data", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("last_inbound_message_id", sa.String(length=255)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_whatsapp_conversation_states_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_whatsapp_conversation_states")),
        sa.UniqueConstraint("wa_id", name="uq_whatsapp_conversation_wa_id"),
    )
    op.create_index(
        "ix_whatsapp_conversation_user_flow",
        "whatsapp_conversation_states",
        ["user_id", "flow", "step"],
    )
    op.create_index(
        op.f("ix_whatsapp_conversation_states_user_id"),
        "whatsapp_conversation_states",
        ["user_id"],
    )

    op.create_table(
        "whatsapp_webhook_receipts",
        sa.Column("event_key", sa.String(length=320), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255)),
        sa.Column("provider_status", sa.String(length=40)),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_redacted", sa.JSON(), nullable=False),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("error_detail", sa.Text()),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("result_provider_message_id", sa.String(length=255)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_whatsapp_webhook_receipts")),
        sa.UniqueConstraint("event_key", name="uq_whatsapp_webhook_event_key"),
    )
    op.create_index(
        "ix_whatsapp_webhook_processing",
        "whatsapp_webhook_receipts",
        ["processing_status", "received_at"],
    )
    op.create_index(
        "ix_whatsapp_webhook_provider_message",
        "whatsapp_webhook_receipts",
        ["provider_message_id"],
    )
    op.create_index(
        "ix_whatsapp_webhook_retention",
        "whatsapp_webhook_receipts",
        ["retain_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_whatsapp_webhook_retention", table_name="whatsapp_webhook_receipts")
    op.drop_index(
        "ix_whatsapp_webhook_provider_message", table_name="whatsapp_webhook_receipts"
    )
    op.drop_index("ix_whatsapp_webhook_processing", table_name="whatsapp_webhook_receipts")
    op.drop_table("whatsapp_webhook_receipts")
    op.drop_index(
        op.f("ix_whatsapp_conversation_states_user_id"),
        table_name="whatsapp_conversation_states",
    )
    op.drop_index(
        "ix_whatsapp_conversation_user_flow", table_name="whatsapp_conversation_states"
    )
    op.drop_table("whatsapp_conversation_states")
    op.drop_index(
        op.f("ix_whatsapp_connections_user_id"), table_name="whatsapp_connections"
    )
    op.drop_index("ix_whatsapp_connection_status", table_name="whatsapp_connections")
    op.drop_table("whatsapp_connections")

    with op.batch_alter_table("integration_test_results") as batch_op:
        batch_op.drop_index(op.f("ix_integration_test_results_provider_message_id"))
        batch_op.drop_column("provider_message_id")

    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM alert_deliveries WHERE channel = 'whatsapp'"))
    connection.execute(sa.text("DELETE FROM user_identities WHERE provider = 'whatsapp'"))
    connection.execute(
        sa.text("DELETE FROM identity_link_tokens WHERE target_channel = 'whatsapp'")
    )

    with op.batch_alter_table("alert_deliveries") as batch_op:
        batch_op.drop_column("read_at")
        batch_op.drop_column("accepted_at")
        batch_op.drop_column("provider_status_metadata")
        batch_op.drop_column("provider_status")
        batch_op.alter_column(
            "channel",
            existing_type=sa.Enum(
                *DELIVERY_CHANNELS,
                name="delivery_channel",
                native_enum=False,
            ),
            type_=sa.Enum(
                *OLD_DELIVERY_CHANNELS,
                name="delivery_channel",
                native_enum=False,
            ),
            existing_nullable=False,
        )

    with op.batch_alter_table("user_identities") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.Enum(
                *IDENTITY_PROVIDERS,
                name="identity_provider",
                native_enum=False,
            ),
            type_=sa.Enum(
                *OLD_IDENTITY_PROVIDERS,
                name="identity_provider",
                native_enum=False,
            ),
            existing_nullable=False,
        )

    with op.batch_alter_table("identity_link_tokens") as batch_op:
        batch_op.drop_column("metadata_json")
        batch_op.drop_column("canceled_at")
        batch_op.alter_column(
            "onboarding_session_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
