"""Remove Dagon Urul Region cells from the FGM_TR grass plugin export.

Both inputs are tes3conv's indented JSON format. Records are read one at a
time because the grass export is too large to load into memory as a list.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REGION = "dagon urul region"
HEADER_COUNT = re.compile(rb'("num_objects": )([0-9]+)')
CELL_PREFIX = b'  {\n    "type": "Cell",'


def records(path: Path) -> Iterator[bytes]:
    """Yield complete top-level objects, retaining their original formatting."""
    with path.open("rb") as source:
        if source.readline() != b"[\n":
            raise ValueError(f"Unexpected JSON opening in {path}")
        parts: list[bytes] = []
        found_end = False
        for line in source:
            if not parts:
                if line == b"]\n" or line == b"]":
                    found_end = True
                    break
                if line != b"  {\n":
                    raise ValueError(f"Unexpected record opening in {path}: {line[:80]!r}")
            parts.append(line)
            if line == b"  },\n" or line == b"  }\n":
                parts[-1] = b"  }\n"
                yield b"".join(parts)
                parts = []
        if parts or not found_end or source.read(1):
            raise ValueError(f"Incomplete JSON array in {path}")


def region_grids(path: Path) -> set[tuple[int, int]]:
    grids = set()
    for raw in records(path):
        if not raw.startswith(CELL_PREFIX):
            continue
        cell = json.loads(raw)
        if cell.get("region", "").casefold() == REGION:
            grid = tuple(cell["data"]["grid"])
            if grid in grids:
                raise ValueError(f"Duplicate Dagon Urul cell: {grid}")
            grids.add(grid)
    if not grids:
        raise ValueError(f"No {REGION} cells found in {path}")
    return grids


def build(source: Path, mainland: Path, destination: Path) -> tuple[int, int, int]:
    grids = region_grids(mainland)
    removed = 0
    total = 0
    kept = 0
    header_count = None
    count_offset = None
    count_width = None
    temporary = destination.with_name(destination.name + ".tmp")

    with temporary.open("wb") as target:
        target.write(b"[\n")
        for raw in records(source):
            total += 1
            if total == 1:
                header = json.loads(raw)
                if header["type"] != "Header":
                    raise ValueError("FGM_TR.json does not start with a Header")
                header_count = header["num_objects"]
                match = HEADER_COUNT.search(raw)
                if match is None:
                    raise ValueError("Header has no num_objects field")
                count_width = len(match.group(2))
                count_offset = target.tell() + match.start(2)
            elif raw.startswith(CELL_PREFIX):
                cell = json.loads(raw)
                if tuple(cell["data"]["grid"]) in grids:
                    removed += 1
                    continue
            if kept:
                target.write(b",\n")
            target.write(raw[:-1])
            kept += 1
        target.write(b"\n]\n")
        if header_count != total - 1:
            raise ValueError(f"Header claims {header_count} objects, found {total - 1}")
        if removed == 0:
            raise ValueError("No Dagon Urul grass cells found")
        new_count = kept - 1
        count_bytes = str(new_count).encode("ascii")
        if len(count_bytes) != count_width:
            raise ValueError("Updated object count needs a different header width")
        target.seek(count_offset)
        target.write(count_bytes)
    temporary.replace(destination)
    return len(grids), removed, kept - 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DATA / "FGM_TR.json")
    parser.add_argument("--mainland", type=Path, default=DATA / "TR_Mainland.json")
    parser.add_argument("--output", type=Path, default=DATA / "FGM_TR_no_Dagon_Urul.json")
    args = parser.parse_args()
    grids, removed, objects = build(args.source, args.mainland, args.output)
    print(f"Wrote {args.output}: {removed} grass cells removed from {grids} "
          f"Dagon Urul grids; {objects} objects remain")
