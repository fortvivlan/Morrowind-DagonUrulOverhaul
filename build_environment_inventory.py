"""List container flora and painted ground textures in Dagon Urul."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import struct
from collections import Counter, defaultdict
from compression import zstd
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REGION = "dagon urul region"
DEFAULT_TAMRIEL_DATA = Path(
    "/mnt/g/Steam/steamapps/common/Morrowind/Data Files/Tamriel_Data.esm"
)


def records(path: Path, wanted: set[str]):
    """Read selected records from tes3conv's pretty-printed top-level JSON array."""
    with path.open(encoding="utf-8") as stream:
        lines = []
        keep = False
        for line in stream:
            if line == "  {\n":
                lines = [line]
                keep = False
            elif lines:
                if line.startswith('    "type": '):
                    record_type = json.loads(line.split(": ", 1)[1].rstrip(",\n"))
                    keep = record_type in wanted
                if keep:
                    lines.append(line)
                if line in ("  },\n", "  }\n"):
                    if keep:
                        yield json.loads("".join(lines).rstrip().rstrip(","))
                    lines = []


def tamriel_containers(path: Path):
    """Read CONT definitions directly from the matching Tamriel_Data.esm."""
    with path.open("rb") as stream:
        while header := stream.read(16):
            if len(header) != 16:
                raise ValueError(f"Truncated record header in {path}")
            record_type, size, _unknown, _flags = struct.unpack("<4sIII", header)
            if record_type != b"CONT":
                stream.seek(size, 1)
                continue
            data = stream.read(size)
            if len(data) != size:
                raise ValueError(f"Truncated CONT record in {path}")
            fields = {}
            offset = 0
            while offset < size:
                tag, field_size = struct.unpack_from("<4sI", data, offset)
                offset += 8
                value = data[offset:offset + field_size]
                offset += field_size
                if tag in (b"NAME", b"FNAM", b"MODL"):
                    fields[tag] = value.rstrip(b"\0").decode("cp1252", "replace")
            if b"NAME" in fields:
                yield {
                    "id": fields[b"NAME"],
                    "name": fields.get(b"FNAM", ""),
                    "mesh": fields.get(b"MODL", ""),
                }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def cell_label(grid: tuple[int, int]) -> str:
    return f"({grid[0]}, {grid[1]})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tamriel-data-esm", type=Path, default=DEFAULT_TAMRIEL_DATA)
    args = parser.parse_args()
    root = args.root.resolve()
    tamriel_path = args.tamriel_data_esm.resolve()
    if not tamriel_path.is_file():
        parser.error(f"Tamriel Data master not found: {tamriel_path}")

    containers = {}
    for record in records(root / "data/Morrowind.json", {"Container"}):
        containers[record["id"].casefold()] = (record, "Morrowind.esm")
    for record in tamriel_containers(tamriel_path):
        containers[record["id"].casefold()] = (record, "Tamriel_Data.esm")

    cells = {}
    landscapes = {}
    textures = {}
    tamriel_expected_size = None
    for record in records(root / "data/TR_Mainland.json",
                          {"Header", "Cell", "Landscape", "LandscapeTexture", "Container"}):
        record_type = record["type"]
        if record_type == "Header":
            tamriel_expected_size = next(
                (size for name, size in record["masters"]
                 if name.casefold() == "tamriel_data.esm"), None
            )
        elif record_type == "Cell" and record.get("region", "").casefold() == REGION:
            grid = tuple(record["data"]["grid"])
            if grid in cells:
                raise ValueError(f"Duplicate region cell {grid}")
            cells[grid] = record
        elif record_type == "Landscape":
            landscapes[tuple(record["grid"])] = record
        elif record_type == "LandscapeTexture":
            textures[record["index"]] = record
        elif record_type == "Container":
            containers[record["id"].casefold()] = (record, "TR_Mainland.esm")

    if tamriel_expected_size is None or tamriel_path.stat().st_size != tamriel_expected_size:
        raise ValueError("Tamriel_Data.esm does not match the TR_Mainland.json master header")

    plant_counts = Counter()
    plant_cells = defaultdict(set)
    for grid, cell in cells.items():
        for reference in cell["references"]:
            if reference.get("deleted"):
                continue
            key = reference["id"].casefold()
            match = containers.get(key)
            if not match:
                continue
            # Use the flora record family; a flora mesh alone also matches
            # harvestable red coral, which is not a plant.
            if "flora" not in key:
                continue
            plant_counts[key] += 1
            plant_cells[key].add(grid)

    plants = []
    for key, count in plant_counts.items():
        record, source = containers[key]
        plants.append({
            "id": record["id"], "name": record.get("name", ""),
            "mesh": record.get("mesh", ""), "source_master": source,
            "references": count, "cells": len(plant_cells[key]),
            "cell_coordinates": "; ".join(map(cell_label, sorted(plant_cells[key]))),
        })
    plants.sort(key=lambda row: (-row["references"], row["id"].casefold()))
    write_csv(root / "container_plants.csv",
              ["id", "name", "mesh", "source_master", "references", "cells",
               "cell_coordinates"], plants)

    texture_counts = Counter()
    texture_cells = defaultdict(set)
    for grid in sorted(cells):
        landscape = landscapes.get(grid)
        if landscape is None or "texture_indices" not in landscape:
            raise ValueError(f"Missing painted landscape in Dagon Urul cell {grid}")
        raw = zstd.decompress(base64.b64decode(landscape["texture_indices"]["data"]))
        if len(raw) != 512:
            raise ValueError(f"Unexpected VTEX length at {grid}: {len(raw)}")
        counts = Counter(struct.unpack("<256H", raw))
        for vtex_index, count in counts.items():
            texture_counts[vtex_index] += count
            texture_cells[vtex_index].add(grid)

    ground = []
    for vtex_index, count in texture_counts.items():
        # VTEX is one-based; zero denotes the game's default ground texture.
        texture = textures.get(vtex_index - 1) if vtex_index else None
        if vtex_index and texture is None:
            raise ValueError(f"Missing TR_Mainland LTEX index {vtex_index - 1}")
        ground.append({
            "id": texture["id"] if texture else "(default)",
            "file_name": texture["file_name"] if texture else "_land_default.dds",
            "source_master": "TR_Mainland.esm" if texture else "game default",
            "land_texture_index": vtex_index - 1 if texture else "",
            "vtex_index": vtex_index,
            "tiles": count, "cells": len(texture_cells[vtex_index]),
            "cell_coordinates": "; ".join(map(cell_label, sorted(texture_cells[vtex_index]))),
        })
    ground.sort(key=lambda row: (-row["tiles"], row["id"].casefold()))
    write_csv(root / "ground_textures.csv",
              ["id", "file_name", "source_master", "land_texture_index",
               "vtex_index", "tiles", "cells", "cell_coordinates"], ground)

    summary = {
        "region_cells": len(cells),
        "container_plant_ids": len(plants),
        "container_plant_references": sum(plant_counts.values()),
        "ground_texture_ids": len(ground),
        "ground_texture_files": len({row["file_name"].casefold() for row in ground}),
        "ground_texture_tiles": sum(texture_counts.values()),
    }
    (root / "environment_inventory_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
