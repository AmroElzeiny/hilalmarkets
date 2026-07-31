"""One cached market-data check, stored whole.

The cache used to hold only the provider status rows. The manifest — the record of *what
was actually checked, and under which promise* — lived in memory on the service instance
and was never cached. So a cache hit produced:

* status rows saying every market is available
* approval eligibility, because those rows are what eligibility reads
* **no manifest at all**, or worse, the manifest left behind by the previous call

which is an availability verdict for one universe displayed next to a promise made about
another, with nothing to bind an approval to.

The entry below is the fix: identity, statuses and manifest are one validated object.
Either the whole thing is restored, or none of it is and the check runs again.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ai_market_monitor.schemas.screening_execution import PreflightManifest
from ai_market_monitor.schemas.strategy_draft_v2 import ProviderRuntimeStatusV2


class PreflightCacheEntry(BaseModel):
    """One data check: its identity, its statuses and its manifest, together."""

    model_config = ConfigDict(extra="forbid")

    #: A hash of everything that changes what a data check means. Recomputed on read and
    #: compared, so a key collision or a reused Redis database cannot cross universes.
    definition_identity: str = Field(min_length=16, max_length=128)
    statuses: list[ProviderRuntimeStatusV2] = Field(default_factory=list, max_length=100000)
    #: Never written as ``None``: an entry with no manifest is not cached at all. The
    #: field is optional only so a corrupt or older payload validates and is then rejected
    #: by :meth:`manifest_is_intact` rather than raising.
    manifest: PreflightManifest | None = None
    cached_at: datetime
    expires_at: datetime

    def is_fresh(self, now: datetime) -> bool:
        return now < self.expires_at

    def matches(self, identity: str) -> bool:
        return self.definition_identity == identity

    def statuses_are_fresh(self, now: datetime, *, ttl_seconds: int) -> bool:
        """Every status row must carry a timestamp inside the window.

        A row with no ``checked_at`` is unusable evidence, not a passing one.
        """
        for item in self.statuses:
            if item.checked_at is None:
                return False
            if (now - item.checked_at).total_seconds() > ttl_seconds:
                return False
        return True

    def manifest_is_intact(self) -> bool:
        """The manifest must exist and still describe a real check."""
        if self.manifest is None:
            return False
        if self.manifest.contract == "not_required":
            return True
        return bool(self.manifest.verified_pairs or self.manifest.unverified_symbols)
