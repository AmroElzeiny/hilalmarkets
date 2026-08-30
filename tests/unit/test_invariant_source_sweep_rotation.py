"""One sweep must carry on where the last one stopped.

The link resolver may only touch ``sharia_source_resolution_batch_size`` assets per run,
because each asset costs real fetches against somebody else's website. A small batch is
only safe if the next run moves on. Until 30 August 2026 the queue was ordered by
``created_at``, which is not a cursor -- so every run re-read the same oldest 25 assets
and no asset past position 25 was ever checked. The scan reported success every time and
the review queue never moved, which is exactly what it looked like from the outside.

These tests assert the rule rather than that one batch size: for any number of assets and
any batch, repeated sweeps must reach every asset, and must not re-read one while another
has never been read at all.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import CanonicalAsset, OfficialSource
from ai_market_monitor.services.sharia_source_resolution import SourceResolutionService

GONE: tuple[str, dict, int] = ("", {"content-type": "text/html"}, 404)
ASSET_IN_URL = re.compile(r"coin(\d{2})")


class _Recorder:
    """Answers 404 for everything and remembers every address it was asked for."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def fetch(self, source):
        self.requested.append(source.source_url)
        return GONE


async def _assets(session, count: int) -> list[CanonicalAsset]:
    """Assets a sweep is allowed to pick up, each with its own recognisable address."""

    made = []
    for index in range(count):
        asset = CanonicalAsset(
            symbol=f"CO{index:02d}",
            name=f"Coin {index:02d}",
            asset_type="coin",
            official_website=f"https://coin{index:02d}.example/",
            identity_hash=f"hash-co{index:02d}",
            mapping_state="verified",
        )
        session.add(asset)
        made.append(asset)
    await session.flush()
    return made


async def _sweep(session, settings, batch: int) -> set[str]:
    """Run one sweep. Return the symbols it actually looked at."""

    recorder = _Recorder()
    service = SourceResolutionService(session, settings, fetcher=recorder)
    await service.resolve_pending(limit=batch)
    await session.flush()
    # The convention layer also tries blog./forum./gov. in front of the same host, so the
    # asset is the number inside the address, not the first label of it.
    found = (ASSET_IN_URL.search(url) for url in recorder.requested)
    return {f"CO{match.group(1)}" for match in found if match}


@pytest.mark.parametrize(("total", "batch"), [(7, 3), (9, 2), (5, 5), (6, 1)])
async def test_repeated_sweeps_reach_every_asset(test_context, total: int, batch: int) -> None:
    """Enough runs must cover the whole list, whatever the batch size.

    With ``created_at`` ordering this fails for every case where ``batch < total``: the
    same first ``batch`` assets come back every time and the rest are never touched.
    """

    async with test_context["session_factory"]() as session:
        await _assets(session, total)
        seen: set[str] = set()
        # Enough runs to cover the list twice over, so a sweep that advances has no
        # excuse left, and one that does not is caught.
        for _ in range(2 * -(-total // batch)):
            seen |= await _sweep(session, test_context["settings"], batch)
        await session.commit()

    expected = {f"CO{index:02d}" for index in range(total)}
    assert seen == expected, f"never looked at: {sorted(expected - seen)}"


async def test_a_sweep_does_not_repeat_itself_while_an_asset_is_still_untouched(
    test_context,
) -> None:
    """The second run must move on, not re-read the first run's work."""

    async with test_context["session_factory"]() as session:
        await _assets(session, 6)
        first = await _sweep(session, test_context["settings"], 3)
        second = await _sweep(session, test_context["settings"], 3)
        await session.commit()

    assert len(first) == 3
    assert not (first & second), f"re-read while others were never read: {sorted(first & second)}"
    assert first | second == {f"CO{index:02d}" for index in range(6)}


async def test_every_asset_a_sweep_touches_records_when_it_was_looked_at(
    test_context,
) -> None:
    """The ordering is only a cursor if looking at an asset actually marks it.

    An asset the sweep touched but left with no ``last_checked_at`` anywhere would sort
    first again next time and hold the queue still -- the same starvation in a new place.
    """

    async with test_context["session_factory"]() as session:
        made = await _assets(session, 3)
        await _sweep(session, test_context["settings"], 3)
        await session.commit()

        for asset in made:
            stamps = list(
                (
                    await session.scalars(
                        select(OfficialSource.last_checked_at).where(
                            OfficialSource.canonical_asset_id == asset.id
                        )
                    )
                ).all()
            )
            assert stamps, f"{asset.symbol} was looked at but recorded no link at all"
            assert any(value is not None for value in stamps), (
                f"{asset.symbol} recorded links but none carries a checked time, "
                "so the next sweep will pick it first again"
            )
