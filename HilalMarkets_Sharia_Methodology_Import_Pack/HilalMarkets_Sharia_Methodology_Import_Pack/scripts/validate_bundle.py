#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


errors = []
methodologies = load_json("data/methodologies.json")
ids = {m["methodology_id"] for m in methodologies}
if len(ids) != len(methodologies):
    errors.append("Duplicate methodology_id")

datasets = [
    "data/sc_malaysia_compliant_assets.json",
    "data/shariah_review_bureau_compliant_assets.json",
    "data/fasset_compliant_assets.json",
]
source_ids = set()
for filename in datasets:
    for row in load_json(filename):
        if row.get("methodology_id") not in ids:
            errors.append(f"{filename}: unknown methodology {row.get('methodology_id')}")
        if row.get("auto_publish") is not False:
            errors.append(f"{filename}: {row.get('source_row_id')} permits auto publication")
        if row.get("publication_state") != "PENDING_ADMIN_REVIEW":
            errors.append(f"{filename}: {row.get('source_row_id')} not gated")
        rid = row.get("source_row_id")
        if rid in source_ids:
            errors.append(f"Duplicate source_row_id {rid}")
        source_ids.add(rid)

sc = load_json("data/sc_malaysia_compliant_assets.json")
srb = load_json("data/shariah_review_bureau_compliant_assets.json")
fas = load_json("data/fasset_compliant_assets.json")
guards = load_json("data/fasset_noncompliant_guard.json")
if len(sc) != 15:
    errors.append(f"Expected 15 SC records; found {len(sc)}")
if len(srb) != 31:
    errors.append(f"Expected 31 SRB records; found {len(srb)}")
if len(fas) != 188:
    errors.append(f"Expected 188 Fasset compliant rows; found {len(fas)}")
if len(guards) != 52:
    errors.append(f"Expected 52 Fasset guard rows; found {len(guards)}")

if errors:
    print("VALIDATION FAILED")
    for error in errors:
        print("-", error)
    sys.exit(1)
print("VALIDATION PASSED")
print(f"Methodologies: {len(methodologies)}")
print(f"SC Malaysia compliant: {len(sc)}")
print(f"Shariah Review Bureau compliant: {len(srb)}")
print(f"Fasset compliant: {len(fas)}")
print(f"Fasset non-compliant guard rows: {len(guards)}")
