from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ConditionType,
    LogicalOperator,
    MarketType,
    ShariaUniverseMode,
    StrategyStatus,
    StrategyVersionStatus,
    TriggerMode,
)


class Strategy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategies"
    __table_args__ = (Index("ix_strategy_user_status", "user_id", "status"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[StrategyStatus] = mapped_column(
        enum_type(StrategyStatus, name="strategy_status"),
        default=StrategyStatus.DRAFT,
        nullable=False,
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", use_alter=True, ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list["StrategyVersion"]] = relationship(
        back_populates="strategy",
        foreign_keys="StrategyVersion.strategy_id",
        cascade="all, delete-orphan",
        order_by="StrategyVersion.version_number",
    )


class StrategyVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version_number", name="uq_strategy_version_number"),
        Index("ix_strategy_version_status", "strategy_id", "status"),
        Index("ix_strategy_schema_hash", "schema_hash"),
    )

    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    parent_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    restored_from_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StrategyVersionStatus] = mapped_column(
        enum_type(StrategyVersionStatus, name="strategy_version_status"),
        default=StrategyVersionStatus.DRAFT,
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0", nullable=False)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    interpretation_provider: Mapped[str | None] = mapped_column(String(80))
    interpretation_model: Mapped[str | None] = mapped_column(String(100))
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ambiguities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    unsupported_conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_schema_hash: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: What the user was shown when they approved this version: the screening evidence
    #: and the market-data check that went with it.
    #:
    #: The runtime needs the market-data *contract* from here. Without it the worker had
    #: no way to know whether every market had been checked before approval or only a
    #: sample, so it could not tell which markets still needed checking each cycle. Empty
    #: means "nothing was promised", which the runtime reads as "check everything".
    approval_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    preview_status: Mapped[str] = mapped_column(String(32), default="not_run", nullable=False)
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preview_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    semantic_diff: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    strategy: Mapped[Strategy] = relationship(back_populates="versions", foreign_keys=[strategy_id])
    conditions: Mapped[list["StrategyCondition"]] = relationship(
        back_populates="strategy_version", cascade="all, delete-orphan"
    )
    universe: Mapped["StrategyUniverse | None"] = relationship(
        back_populates="strategy_version", cascade="all, delete-orphan", uselist=False
    )


class StrategyCondition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_conditions"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", "condition_key", name="uq_version_condition_key"),
        Index("ix_condition_version_parent", "strategy_version_id", "parent_condition_id"),
    )

    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    parent_condition_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_conditions.id", ondelete="CASCADE")
    )
    condition_key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    condition_type: Mapped[ConditionType | None] = mapped_column(
        enum_type(ConditionType, name="condition_type")
    )
    logical_operator: Mapped[LogicalOperator | None] = mapped_column(
        enum_type(LogicalOperator, name="logical_operator")
    )
    timeframe: Mapped[str | None] = mapped_column(String(16))
    comparator: Mapped[str | None] = mapped_column(String(32))
    left_operand: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    right_operand: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    required_value: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    weight: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=1, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    strategy_version: Mapped[StrategyVersion] = relationship(back_populates="conditions")


class StrategyUniverse(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_universes"

    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    market_type: Mapped[MarketType] = mapped_column(
        enum_type(MarketType, name="market_type"), default=MarketType.SPOT, nullable=False
    )
    quote_currencies: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    include_symbols: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    exclude_symbols: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    timeframes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    trigger_mode: Mapped[TriggerMode] = mapped_column(
        enum_type(TriggerMode, name="trigger_mode"), nullable=False
    )
    min_quote_volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    min_listing_age_days: Mapped[int | None] = mapped_column(Integer)
    max_spread_bps: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    min_order_book_depth: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    max_symbols: Mapped[int | None] = mapped_column(Integer)
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    universe_mode: Mapped[ShariaUniverseMode | None] = mapped_column(
        enum_type(ShariaUniverseMode, name="strategy_sharia_universe_mode")
    )
    methodology_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT")
    )
    allowed_sharia_statuses: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    qualification_policy: Mapped[str | None] = mapped_column(String(40))
    disputed_asset_policy: Mapped[str | None] = mapped_column(String(40))
    compliance_change_behavior: Mapped[ComplianceChangeBehavior | None] = mapped_column(
        enum_type(ComplianceChangeBehavior, name="strategy_compliance_change_behavior")
    )
    approved_watchlist_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approved_watchlists.id", ondelete="SET NULL")
    )
    universe_snapshot_version: Mapped[int | None] = mapped_column(Integer)
    universe_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    universe_last_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    sharia_policy_ready: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    strategy_version: Mapped[StrategyVersion] = relationship(back_populates="universe")
