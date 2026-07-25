#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
matrix = json.loads(
    (ROOT / "data/methodology_union_matrix.json").read_text(encoding="utf-8")
)
for row in sorted(
    matrix,
    key=lambda item: (
        -item["methodologies_count"],
        item["canonical_symbol_candidate"],
    ),
):
    print(
        f"{row['canonical_symbol_candidate']:15} "
        f"SC={str(row['sc_malaysia_compliant']):5} "
        f"SRB={str(row['shariah_review_bureau_compliant']):5} "
        f"Fasset={str(row['fasset_compliant']):5}"
    )
