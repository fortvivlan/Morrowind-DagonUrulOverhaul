"""Build a reviewable rock/tree inventory from TES3 text exports."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCES = ("mw_statics.txt", "tamriel_statics.txt", "tr_statics.txt")
CELL_HEADER = re.compile(r'^Record: CELL "([^"]+)"', re.IGNORECASE)
STAT_HEADER = re.compile(r'^Record: STAT "([^"]+)"', re.IGNORECASE)
MODEL = re.compile(r'^\s+MODL: Model:(.+)$')
STAT_ID = re.compile(r'^\s+NAME: ID:(.+)$')
REF_ID = re.compile(r'^\s+NAME: Name:(.+)$')
REGION = re.compile(r'^\s+RGNN: Region:(.+)$')
FRMR = re.compile(r'^\s+\*?FRMR: ObjIdx:(\d+)\s+MastIdx:(\d+)')


def read_export(path: Path) -> list[str]:
    return path.read_text(encoding="utf-16").splitlines()


def statics(path: Path) -> dict[str, tuple[str, str, str]]:
    result = {}
    record_id = model = None

    def commit() -> None:
        if record_id and model:
            result[record_id.casefold()] = (record_id, model, path.name)

    for line in read_export(path):
        match = STAT_HEADER.match(line)
        if match:
            commit()
            record_id, model = match.group(1), None
        elif line.startswith("Record:"):
            commit()
            record_id = model = None
        elif record_id:
            if match := STAT_ID.match(line):
                record_id = match.group(1).strip()
            elif match := MODEL.match(line):
                model = match.group(1).strip()
    commit()
    return result


def cells(path: Path) -> tuple[Counter[str], dict[str, set[str]], list[dict], int]:
    counts: Counter[str] = Counter()
    locations: dict[str, set[str]] = defaultdict(set)
    references = []
    cell = None
    in_region = False
    ref_number = None
    total_cells = 0
    for line in read_export(path):
        if match := CELL_HEADER.match(line):
            cell = match.group(1)
            in_region = False
            ref_number = None
            total_cells += 1
        elif line.startswith("Record:"):
            cell = None
            ref_number = None
        elif cell and (match := REGION.match(line)):
            in_region = match.group(1).strip().casefold() == "dagon urul region"
        elif cell and in_region and (match := FRMR.match(line)):
            ref_number = (int(match.group(1)), int(match.group(2)))
        elif cell and in_region and (match := REF_ID.match(line)):
            record_id = match.group(1).strip().casefold()
            if record_id and ref_number is not None:
                counts[record_id] += 1
                locations[record_id].add(cell)
                references.append({"cell": cell, "object_index": ref_number[0],
                                   "master_index": ref_number[1], "id": record_id})
                ref_number = None
    return counts, locations, references, total_cells


def kind(record_id: str, model: str) -> str | None:
    # The exported ID and model path are names, not an authoritative object taxonomy.
    # Keep this inventory reviewable before any plugin edits are made.
    text = f"{record_id} {model}".casefold()
    if "tree" in text:
        return "tree"
    if "rock" in text:
        return "rock"
    return None


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="directory containing the four text exports")
    args = parser.parse_args()
    root = args.root.resolve()
    existing = {}
    inventory_path = root / "mesh_inventory.csv"
    if inventory_path.exists():
        with inventory_path.open(encoding="utf-8", newline="") as stream:
            existing = {row["id"].casefold(): row for row in csv.DictReader(stream)}
    index = {}
    for name in SOURCES:
        index.update(statics(root / name))  # master load order; later definitions win

    counts, locations, references, cell_count = cells(root / "dagon_urul_cells.txt")
    inventory = []
    unresolved = []
    for key, count in sorted(counts.items()):
        match = index.get(key)
        if match is None:
            if "rock" in key or "tree" in key:
                unresolved.append({"id": key, "references": count, "cells": len(locations[key])})
            continue
        record_id, model, source = match
        category = kind(record_id, model)
        if category is None:
            continue
        previous = existing.get(key, {})
        inventory.append({
            "kind": category,
            "id": record_id,
            "source_mesh": model,
            "source_export": source,
            "references": count,
            "cells": len(locations[key]),
            "replacement_mesh": previous.get("replacement_mesh", ""),
            "include": previous.get("include", ""),
        })

    inventory.sort(key=lambda row: (row["kind"], -row["references"], row["id"].casefold()))
    write_csv(inventory_path, [
        "kind", "id", "source_mesh", "source_export", "references", "cells",
        "replacement_mesh", "include",
    ], inventory)
    write_csv(root / "unresolved_candidates.csv", ["id", "references", "cells"], unresolved)
    selected_ids = {row["id"].casefold() for row in inventory}
    write_csv(root / "target_references.csv", ["cell", "object_index", "master_index", "id"],
              [row for row in references if row["id"] in selected_ids])
    summary = {
        "cells": cell_count,
        "references": sum(counts.values()),
        "unique_reference_ids": len(counts),
        "inventory_records": len(inventory),
        "inventory_references": sum(row["references"] for row in inventory),
        "rocks": sum(row["kind"] == "rock" for row in inventory),
        "trees": sum(row["kind"] == "tree" for row in inventory),
        "unresolved_candidates": len(unresolved),
    }
    (root / "inventory_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
