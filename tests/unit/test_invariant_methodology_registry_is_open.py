"""Adding a Shariah authority must be a data change, never a code change.

The product's coverage grows by adding authorities. Before the methodology registry
existed, adding one meant editing fourteen places in ``sharia_import_pack.py`` — five
parallel dictionaries keyed by the same identifiers, two more inside
``_methodology_rules``, and seven hand-written ``if`` chains. Missing a dictionary
raised ``KeyError`` mid-import; missing an ``if`` was worse, because the new authority
silently inherited Fasset's rights state, publication gate and source reference.

These tests assert the rule rather than the shipped case: a pack carrying an authority
that **no module in this repository has ever heard of** must load, and must carry that
authority's own rights rule, publication gate and source reference — not a neighbour's.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from ai_market_monitor.services.sharia_import_pack import (
    ShariaImportPackError,
    load_import_pack,
)
from ai_market_monitor.services.sharia_methodology_registry import (
    ShariaMethodologyDefinitionError,
    load_methodology_specs,
)

PACK_ROOT = (
    Path(__file__).resolve().parents[2]
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
)
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: An authority that exists nowhere in this repository. If any module has to learn its
#: name for the pack to import, the registry has failed its purpose.
UNKNOWN_ID = "TEST_ONLY_UNKNOWN_AUTHORITY"
UNKNOWN_CODE = "TEST_ONLY_UNKNOWN_AUTHORITY_REFERENCE"


def _definition() -> dict[str, Any]:
    return {
        "methodology_id": UNKNOWN_ID,
        "display_name": "Test Only Unknown Authority",
        "authority": "Test Only Unknown Authority Board",
        "type": "EXTERNAL_PRELIMINARY_RESEARCH_REFERENCE",
        "source_url": "https://example.invalid/rulings",
        "status_language": "Compliant",
        "scope": "Only assets this fixture explicitly lists as compliant.",
        "default_publication_gate": "ADMIN_APPROVAL_AND_FIXTURE_GATE_REQUIRED",
        "records_count": 2,
        "import_rules": {
            "system_code": UNKNOWN_CODE,
            "short_label": "Unknown Authority",
            "source_adapter": "unknown_authority",
            "source_family": "unknown_authority_rulings",
            "dataset_file": "unknown_authority_compliant_assets.json",
            "manifest_count_key": "unknown_authority_compliant",
            "records_count": 2,
            "source_reference_template": "Ruling {ruling_number} ({decision_date})",
            "rights_clearance_required": True,
            "rights": {
                "state_field": "rights_state",
                "display_field": "commercial_display_allowed",
                "display_default": False,
            },
        },
    }


def _row(index: int, symbol: str, *, display: bool) -> dict[str, Any]:
    return {
        "source_row_id": f"UNK-{index:02d}-{symbol}",
        "methodology_id": UNKNOWN_ID,
        "authority_name": "Test Only Unknown Authority Board",
        "asset_name_source": f"Fixture {symbol}",
        "symbol_source": symbol,
        "canonical_symbol_candidate": symbol,
        "external_status_source": "Compliant",
        "normalized_status": "ELIGIBLE_EXTERNAL_REFERENCE",
        "ruling_number": f"R-{index:03d}",
        "decision_date": "2026-05-01",
        "rights_state": "RIGHTS_CLEARANCE_REQUIRED_BEFORE_COMMERCIAL_PUBLICATION",
        "commercial_display_allowed": display,
        "jurisdiction_scope": "Fixture scope only",
        "source_url": "https://example.invalid/rulings",
        "retrieved_at": "2026-08-30",
        "publication_state": "PENDING_ADMIN_REVIEW",
        "auto_publish": False,
        "coin_specific_reasoning_published": False,
        "passport_source_statement": "Fixture statement.",
        "manual_verification_required": True,
    }


def _seed(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "passport_seed_id": f"PASSPORT-{row['source_row_id']}",
        "asset_symbol_candidate": row["canonical_symbol_candidate"],
        "methodology_id": UNKNOWN_ID,
        "external_assessment": row,
        "source_authority_section": {
            "status": "Compliant",
            "authority": row["authority_name"],
            "decision_date": row["decision_date"],
            "scope": "Fixture scope only",
            "detailed_reasoning": "Not publicly provided.",
        },
        "hilalmarkets_factual_profile": None,
        "profile_state": "AI_ENRICHMENT_REQUIRED",
        "admin_review_required": True,
    }


def _task(row: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    task = dict(template)
    task["task_id"] = f"ENRICH-PASSPORT-{row['source_row_id']}"
    task["passport_seed_id"] = f"PASSPORT-{row['source_row_id']}"
    task["asset_symbol_candidate"] = row["canonical_symbol_candidate"]
    task["methodology_id"] = UNKNOWN_ID
    return task


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def pack_with_unknown_authority(tmp_path: Path) -> Path:
    """A copy of the shipped pack plus one authority nothing in ``src`` knows."""

    root = tmp_path / "pack"
    shutil.copytree(PACK_ROOT, root)
    data = root / "data"

    rows = [_row(1, "AAA", display=False), _row(2, "BBB", display=True)]
    (data / "unknown_authority_compliant_assets.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    definitions = json.loads((data / "methodologies.json").read_text(encoding="utf-8"))
    definitions.append(_definition())
    (data / "methodologies.json").write_text(
        json.dumps(definitions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    seeds = _read_jsonl(data / "passport_seed_records.jsonl")
    seeds.extend(_seed(row) for row in rows)
    _write_jsonl(data / "passport_seed_records.jsonl", seeds)

    tasks = _read_jsonl(data / "ai_enrichment_queue.jsonl")
    tasks.extend(_task(row, tasks[0]) for row in rows)
    _write_jsonl(data / "ai_enrichment_queue.jsonl", tasks)

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["counts"]["methodologies"] = 4
    manifest["counts"]["unknown_authority_compliant"] = 2
    manifest["counts"]["passport_seeds"] = 236
    manifest["counts"]["ai_enrichment_tasks"] = 236
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return root


def test_a_new_authority_imports_without_any_code_change(
    pack_with_unknown_authority: Path,
):
    bundle = load_import_pack(str(pack_with_unknown_authority))

    assert UNKNOWN_ID in bundle.specs
    assert len(bundle.specs) == 4
    assert len(bundle.rows[UNKNOWN_ID]) == 2

    spec = bundle.specs[UNKNOWN_ID]
    assert spec.system_code == UNKNOWN_CODE
    assert spec.publication_gate == "ADMIN_APPROVAL_AND_FIXTURE_GATE_REQUIRED"
    assert spec.source_family == "unknown_authority_rulings"
    assert spec.guard_file is None


def test_a_new_authority_never_inherits_another_authority_s_rules(
    pack_with_unknown_authority: Path,
):
    """The failure the ``if`` chains produced: falling through to Fasset's branch."""

    bundle = load_import_pack(str(pack_with_unknown_authority))
    spec = bundle.specs[UNKNOWN_ID]
    fasset = bundle.specs["FASSET_SHARIAH_REPORTS"]
    first, second = bundle.rows[UNKNOWN_ID]

    # Its own source reference, built from its own template — not a source row id.
    assert spec.source_reference(first) == "Ruling R-001 (2026-05-01)"

    # Its own rights answer, read per row, not Fasset's fixed state.
    assert spec.rights.state_for(first) == (
        "RIGHTS_CLEARANCE_REQUIRED_BEFORE_COMMERCIAL_PUBLICATION"
    )
    assert spec.rights.state_for(first) != fasset.rights.state_for(
        bundle.rows["FASSET_SHARIAH_REPORTS"][0]
    )
    assert spec.rights.display_allowed_for(first) is False
    assert spec.rights.display_allowed_for(second) is True

    # And its gate is its own.
    assert spec.publication_gate != fasset.publication_gate


def test_no_module_names_the_new_authority():
    """The proof that nothing in ``src`` had to learn about it."""

    hits = [
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if UNKNOWN_ID in path.read_text(encoding="utf-8")
    ]
    assert hits == []


@pytest.mark.parametrize(
    "missing_field",
    [
        "system_code",
        "short_label",
        "source_adapter",
        "source_family",
        "dataset_file",
        "manifest_count_key",
        "source_reference_template",
    ],
)
def test_a_half_declared_authority_is_refused(missing_field: str):
    """Fail closed. A missing field must stop the pack, never take a default.

    A default here would mean one authority quietly carrying another's identity, which
    is the exact failure the parallel dictionaries used to produce.
    """

    definition = _definition()
    del definition["import_rules"][missing_field]

    with pytest.raises(ShariaMethodologyDefinitionError) as error:
        load_methodology_specs({UNKNOWN_ID: definition})

    assert error.value.code == "methodology_field_missing"


def test_two_authorities_may_not_share_an_identity():
    """Two definitions pointing at one system code would merge two authorities."""

    first = _definition()
    second = _definition()
    second["methodology_id"] = f"{UNKNOWN_ID}_SECOND"
    second["import_rules"]["dataset_file"] = "second.json"
    second["import_rules"]["source_adapter"] = "second_adapter"

    with pytest.raises(ShariaMethodologyDefinitionError) as error:
        load_methodology_specs(
            {UNKNOWN_ID: first, f"{UNKNOWN_ID}_SECOND": second}
        )

    assert error.value.code == "methodology_definition_conflict"


def test_a_declared_dataset_file_that_is_absent_stops_the_pack(tmp_path: Path):
    """A definition without its data must fail loudly, not import zero rows."""

    root = tmp_path / "pack"
    shutil.copytree(PACK_ROOT, root)
    data = root / "data"
    definitions = json.loads((data / "methodologies.json").read_text(encoding="utf-8"))
    definitions.append(_definition())
    (data / "methodologies.json").write_text(
        json.dumps(definitions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ShariaImportPackError) as error:
        load_import_pack(str(root))

    assert error.value.code == "pack_files_missing"
    assert "unknown_authority_compliant_assets.json" in str(error.value)
