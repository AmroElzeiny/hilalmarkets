import json, sys
from pathlib import Path

if len(sys.argv) != 3:
    print("usage: validate-report.py REPORT.json SCHEMA.json", file=sys.stderr)
    raise SystemExit(2)

report_path, schema_path = map(Path, sys.argv[1:])
try:
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
except Exception as e:
    print(f"invalid json: {e}", file=sys.stderr)
    raise SystemExit(3)

errors = []
for key in schema.get("required", []):
    if key not in report:
        errors.append(f"missing required field: {key}")

if report.get("final_verdict") not in schema["properties"]["final_verdict"]["enum"]:
    errors.append("invalid final_verdict")

if report.get("status") not in schema["properties"]["status"]["enum"]:
    errors.append("invalid status")

reqs = report.get("requirements")
if not isinstance(reqs, list) or not reqs:
    errors.append("requirements must be a non-empty array")
else:
    ids = []
    for i, r in enumerate(reqs):
        if not isinstance(r, dict):
            errors.append(f"requirements[{i}] must be object")
            continue
        rid = r.get("id")
        st = r.get("status")
        if not rid: errors.append(f"requirements[{i}] missing id")
        if st not in {"PASS","BLOCKED","ESCALATED"}:
            errors.append(f"requirements[{i}] invalid status")
        ids.append(rid)
    if len(ids) != len(set(ids)):
        errors.append("duplicate requirement IDs")

reviews = report.get("reviews")
if not isinstance(reviews, list) or not reviews:
    errors.append("reviews must be non-empty")

if errors:
    for e in errors:
        print("ERROR:", e, file=sys.stderr)
    raise SystemExit(4)

print("VALID")
