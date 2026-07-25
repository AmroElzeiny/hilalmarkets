from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    CanonicalAsset,
    ExternalAssessment,
    OfficialSource,
)
from ai_market_monitor.services.sharia_research import (
    OfficialEvidenceFetcher,
    PassportEnrichmentProfile,
    ShariaFactualAnalysis,
    ShariaResearchError,
    _database_safe_text,
    _evidence_package,
    _extract_document,
    _initial_research_run_key,
    _passport_enrichment_profile,
)


def test_database_safe_source_text_removes_postgres_nul_bytes() -> None:
    assert _database_safe_text("official\x00evidence") == "officialevidence"


async def test_official_fetcher_records_transport_failure_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("TLS hostname mismatch", request=request)

    settings = Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        sharia_scraper_obey_robots=False,
        sharia_scraper_download_delay_seconds=0.2,
    )
    source = OfficialSource(
        canonical_asset_id=uuid4(),
        category="official_website",
        title="Official project website",
        source_url="https://official.example/",
        normalized_url="https://official.example/",
        priority=10,
        verification_state="verified",
        is_active=True,
    )
    fetcher = OfficialEvidenceFetcher(
        settings,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ShariaResearchError) as captured:
        await fetcher.fetch(source)

    assert captured.value.code == "official_source_unavailable"
    assert captured.value.retryable is True


def test_initial_research_key_fits_persisted_limit_and_changes_with_evidence() -> None:
    external_id = uuid4()

    first = _initial_research_run_key(external_id, "a" * 64, [])
    replay = _initial_research_run_key(external_id, "a" * 64, [])
    changed = _initial_research_run_key(external_id, "b" * 64, [])

    assert first == replay
    assert first != changed
    assert first.startswith(f"initial-research:{external_id}:")
    assert len(first) <= 128


def test_official_pdf_is_extracted_as_text_without_binary_nul_bytes() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_reference}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 72 720 Td "
        b"(Official project evidence describing identity utility governance "
        b"and token mechanics for factual review.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)

    title, headings, text = _extract_document(
        output.getvalue(),
        "https://example.test/whitepaper.pdf",
        content_type="application/pdf",
    )

    assert title == "https://example.test/whitepaper.pdf"
    assert headings == []
    assert "Official project evidence" in text
    assert "\x00" not in text


def test_package_enrichment_profile_cannot_write_external_authority_fields() -> None:
    asset = CanonicalAsset(
        id=uuid4(),
        name="Bitcoin",
        symbol="BTC",
        asset_type="native_coin",
        native_chain="Bitcoin",
        contract_addresses={},
        official_website="https://bitcoin.org/",
        official_documentation="https://developer.bitcoin.org/",
        provider_ids={"coingecko": "bitcoin"},
        identity_hash="a" * 64,
        mapping_state="verified",
        mapping_evidence={},
    )
    analysis = ShariaFactualAnalysis.model_validate(
        {
            "canonical_identity_conclusion": "confirmed",
            "profile": {
                "project_identity": "Bitcoin native network asset.",
                "primary_activity": "Peer-to-peer value transfer.",
                "token_role": "Native network asset.",
                "staking": "No native proof-of-stake mechanism.",
                "lending_and_yield": "No native lending product.",
                "derivatives": "No native derivatives product.",
                "treasury_and_governance": "No protocol treasury.",
                "tokenomics_and_backing": "Programmatic issuance.",
            },
            "relevant_activity_categories": ["payments"],
            "evidence_references": [],
            "missing_evidence": ["Current treasury disclosure unavailable."],
            "contradictions": [],
            "change_type": "initial_research",
            "potential_impact_severity": "none",
            "potentially_affected_methodology_areas": [],
            "human_review_required": True,
            "human_review_reason": "A reviewer must verify every factual field.",
            "recommended_next_action": "human_review",
            "confidence": 0.8,
            "explicit_limitations": ["Factual research only."],
        }
    )

    profile = _passport_enrichment_profile(asset, analysis, [])
    payload = profile.model_dump(mode="json")

    assert payload["provenance"] == (
        "HILALMARKETS_AI_ENRICHMENT_UNVERIFIED"
    )
    assert payload["manual_verification_required"] is True
    assert payload["official_source_registry"] == []
    assert not {
        "external_status",
        "external_methodology",
        "authority",
        "decision_date",
        "sac_meeting",
        "source_rationale",
        "admin_decision",
        "publication_state",
    } & set(payload)

    with pytest.raises(ValidationError):
        PassportEnrichmentProfile.model_validate(
            {
                **payload,
                "external_status": "Shariah-compliant",
            }
        )


def test_package_authority_content_is_withheld_from_ai_enrichment() -> None:
    asset = CanonicalAsset(
        id=uuid4(),
        name="Bitcoin",
        symbol="BTC",
        asset_type="native_coin",
        native_chain="Bitcoin",
        contract_addresses={},
        official_website="https://bitcoin.org/",
        official_documentation="https://developer.bitcoin.org/",
        provider_ids={"coingecko": "bitcoin"},
        identity_hash="b" * 64,
        mapping_state="verified",
        mapping_evidence={},
    )
    external = ExternalAssessment(
        source_snapshot_id=uuid4(),
        source_family="shariah_review_bureau",
        source_authority="Shariyah Review Bureau W.L.L.",
        source_url="https://example.test/restricted-index",
        source_reference="2025 preliminary reference",
        asset_name="Bitcoin",
        asset_symbol="BTC",
        exact_status_wording="Preliminary Sharia assessment completed",
        regulatory_scope="Internal preliminary-research reference.",
        retrieval_date=datetime.now(UTC),
        exact_row_text="retained internally",
        structured_facts={
            "source_fields": {
                "reason_summary_paraphrased": (
                    "Restricted authority summary must not enter AI input."
                )
            }
        },
        source_row_id="SRB-001-bitcoin",
        rights_state=(
            "INTERNAL_PRELIMINARY_RESEARCH_ONLY_NO_PUBLIC_REPORT_CONTENT"
        ),
        commercial_display_allowed=False,
        import_hash="c" * 64,
        mapping_state="mapped",
        mapping_notes=[],
    )

    payload = _evidence_package(asset, external, [])

    authority = payload["external_authority_reference"]
    assert authority["published_profile_facts"] == {}
    assert authority["package_authority_content_withheld_from_ai"] is True
    assert "Restricted authority summary" not in str(payload)
