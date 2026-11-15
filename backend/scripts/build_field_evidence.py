#!/usr/bin/env python3
"""Index where each Investigation's oxygen bounds and run count come from.

The oxygen range shown in the UI (Investigation.oxygenMin / oxygenMax) is computed by
app/catalog.py from the PSI Experiment Table CSVs. Those CSVs are not chunked, so no
Chunk can serve as evidence for the range. This script writes the missing trace:

  backend/var/viz_field_evidence.json
    investigations.{PSI-id}.oxygenMin / oxygenMax / runCount   (same rules as catalog.py)
    investigations.{PSI-id}.tables[].file / rows
    investigations.{PSI-id}.tables[].oxygenColumns[].column / min / max / values
        / minRow / maxRow  (data-row number, the raw cell, and the run id when the table has one)

Reads only data/psi and data/profiles.json. No Neo4j, no LLM. Safe to re-run: the output is overwritten.

Usage:
  ~/venvs/nasa-api/bin/python backend/scripts/build_field_evidence.py
  ~/venvs/nasa-api/bin/python backend/scripts/build_field_evidence.py --check   # compare with backend/var/catalog.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.catalog import (  # noqa: E402
    PROFILES,
    as_percent,
    experiment_tables,
    find_dir,
    is_id_column,
    is_oxygen_column,
    numbers_in,
    read_table,
)

OUT_PATH = ROOT / "backend" / "var" / "viz_field_evidence.json"
CATALOG_PATH = ROOT / "backend" / "var" / "catalog.json"
METHOD = (
    "Same rules as app/catalog.py stats_from_tables: oxygen columns are picked by is_oxygen_column, "
    "cell numbers are read by numbers_in and turned into percent by as_percent, "
    "min/max are rounded to 0.1. Rows are data rows (header excluded, blank rows skipped)."
)


def _row_ref(index: int, cell: str, row: dict, id_column: str | None) -> dict:
    ref = {"row": index, "cell": cell}
    if id_column and row.get(id_column):
        ref["id"] = row[id_column]
        ref["idColumn"] = id_column
    return ref


def table_entry(path: Path) -> dict:
    fieldnames, rows = read_table(path)
    id_column = next((name for name in fieldnames if is_id_column(name)), None)
    columns = []
    for name in fieldnames:
        if not is_oxygen_column(name):
            continue
        low = high = None
        low_ref = high_ref = None
        count = 0
        for index, row in enumerate(rows, start=1):
            cell = row.get(name, "")
            for value in as_percent(name, numbers_in(cell)):
                count += 1
                if low is None or value < low:
                    low, low_ref = value, _row_ref(index, cell, row, id_column)
                if high is None or value > high:
                    high, high_ref = value, _row_ref(index, cell, row, id_column)
        if count == 0:
            continue
        columns.append(
            {
                "column": name,
                "min": round(low, 1),
                "max": round(high, 1),
                "values": count,
                "minRow": low_ref,
                "maxRow": high_ref,
            }
        )
    return {"file": str(path.relative_to(ROOT)), "rows": len(rows), "oxygenColumns": columns}


def build() -> dict:
    profiles = json.loads(PROFILES.read_text(encoding="utf-8"))
    investigations: dict[str, dict] = {}
    for psi_id in sorted(profiles, key=lambda item: (len(item), item)):
        tables = [table_entry(path) for path in experiment_tables(find_dir(psi_id))]
        lows = [column["min"] for table in tables for column in table["oxygenColumns"]]
        highs = [column["max"] for table in tables for column in table["oxygenColumns"]]
        runs = sum(table["rows"] for table in tables)
        investigations[psi_id] = {
            "oxygenMin": round(min(lows), 1) if lows else None,
            "oxygenMax": round(max(highs), 1) if highs else None,
            "runCount": runs or None,
            "tables": tables,
        }
    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": METHOD,
        "investigations": investigations,
    }


def _catalog_rows() -> dict[str, dict]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    rows = payload.get("investigations") if isinstance(payload, dict) else payload
    if isinstance(rows, dict):
        rows = [{"id": key, **value} for key, value in rows.items() if isinstance(value, dict)]
    found = {}
    for row in rows or []:
        psi = str(row.get("psiId") or row.get("id") or "").removeprefix("inv:")
        if psi:
            found[psi] = row
    return found


def check(index: dict) -> int:
    """Compare with backend/var/catalog.json. Returns the number of mismatches."""
    catalog = _catalog_rows()
    mismatches = 0
    for psi_id, entry in index["investigations"].items():
        row = catalog.get(psi_id)
        if row is None:
            print(f"{psi_id}: not in catalog.json")
            mismatches += 1
            continue
        for field in ("oxygenMin", "oxygenMax", "runCount"):
            if row.get(field) != entry.get(field):
                print(f"{psi_id}.{field}: catalog={row.get(field)} tables={entry.get(field)}")
                mismatches += 1
    print(f"checked {len(index['investigations'])} investigations, {mismatches} mismatches")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare with backend/var/catalog.json")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    index = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(args.out)
    with_oxygen = sum(1 for entry in index["investigations"].values() if entry["oxygenMin"] is not None)
    print(f"wrote {args.out.relative_to(ROOT)}: {len(index['investigations'])} investigations, {with_oxygen} with oxygen bounds")
    if args.check:
        return 1 if check(index) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
