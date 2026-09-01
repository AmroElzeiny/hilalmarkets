#!/usr/bin/env python3
"""Validate the bundle against what its own methodology definitions declare.

This script used to name the three shipped authorities and their three row counts as
literals. That made it a fourth place holding the same facts — after the application's
importer, this pack's manifest, and each definition's own ``records_count`` — so adding
an authority meant editing all four and any one of them could silently disagree.

Now every authority declares its dataset file, its guard file and its expected counts in
``data/methodologies.json``, and this script only checks that the data agrees.
"""

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

REQUIRED_RULES = (
    "system_code",
    "short_label",
    "source_adapter",
    "source_family",
    "dataset_file",
    "manifest_count_key",
    "records_count",
    "source_reference_template",
    "rights",
)

source_ids = set()
summary = []
total_rows = 0
for methodology in methodologies:
    package_id = methodology["methodology_id"]
    rules = methodology.get("import_rules")
    if not isinstance(rules, dict):
        errors.append(f"{package_id}: no import_rules block")
        continue
    for field in REQUIRED_RULES:
        if field not in rules:
            errors.append(f"{package_id}: import_rules is missing {field}")
    if errors:
        continue

    filename = f"data/{rules['dataset_file']}"
    if not (ROOT / filename).is_file():
        errors.append(f"{package_id}: declared dataset {filename} does not exist")
        continue
    rows = load_json(filename)
    if len(rows) != rules["records_count"]:
        errors.append(
            f"{package_id}: expected {rules['records_count']} rows; found {len(rows)}"
        )
    total_rows += len(rows)
    for row in rows:
        if row.get("methodology_id") != package_id:
            errors.append(f"{filename}: row belongs to {row.get('methodology_id')}")
        if row.get("auto_publish") is not False:
            errors.append(
                f"{filename}: {row.get('source_row_id')} permits auto publication"
            )
        if row.get("publication_state") != "PENDING_ADMIN_REVIEW":
            errors.append(f"{filename}: {row.get('source_row_id')} not gated")
        rid = row.get("source_row_id")
        if rid in source_ids:
            errors.append(f"Duplicate source_row_id {rid}")
        source_ids.add(rid)
    summary.append((methodology["display_name"], len(rows)))

    guard_file = rules.get("guard_file")
    if guard_file:
        guard_path = f"data/{guard_file}"
        if not (ROOT / guard_path).is_file():
            errors.append(f"{package_id}: declared guard {guard_path} does not exist")
            continue
        guards = load_json(guard_path)
        expected_guard = rules.get("guard_records_count")
        if len(guards) != expected_guard:
            errors.append(
                f"{package_id}: expected {expected_guard} guard rows; "
                f"found {len(guards)}"
            )
        for row in guards:
            if row.get("publication_state") != "GUARD_ONLY":
                errors.append(
                    f"{guard_path}: {row.get('source_row_id')} is not guard-only"
                )
        summary.append((f"{methodology['display_name']} guard rows", len(guards)))

manifest_counts = (load_json("manifest.json").get("counts") or {})
if manifest_counts.get("methodologies") != len(methodologies):
    errors.append("manifest methodologies count disagrees with the definitions")
for methodology in methodologies:
    rules = methodology.get("import_rules") or {}
    key = rules.get("manifest_count_key")
    if key and manifest_counts.get(key) != rules.get("records_count"):
        errors.append(f"manifest {key} disagrees with the definition")
    guard_key = rules.get("manifest_guard_count_key")
    if guard_key and manifest_counts.get(guard_key) != rules.get("guard_records_count"):
        errors.append(f"manifest {guard_key} disagrees with the definition")
for key in ("passport_seeds", "ai_enrichment_tasks"):
    if manifest_counts.get(key) != total_rows:
        errors.append(f"manifest {key} is not one per source row")

if errors:
    print("VALIDATION FAILED")
    for error in errors:
        print("-", error)
    sys.exit(1)
print("VALIDATION PASSED")
print(f"Methodologies: {len(methodologies)}")
for name, count in summary:
    print(f"{name}: {count}")
