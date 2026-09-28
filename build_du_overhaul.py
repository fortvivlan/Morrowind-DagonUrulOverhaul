"""Build region-scoped TES3 JSON for DU meshes and ground textures.

The output is intended for conversion to ESP with tes3conv. It adds new STAT,
LTEX, and LAND records and points selected plant references at Tamriel Data.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import json
import struct
from collections import Counter
from compression import zstd
from pathlib import Path, PureWindowsPath

from build_environment_inventory import DEFAULT_TAMRIEL_DATA, tamriel_containers


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
GAME_MESHES = Path("/mnt/g/Steam/steamapps/common/Morrowind/Data Files/meshes")
DU_MESHES = GAME_MESHES / "DU"
DU_TEXTURES = GAME_MESHES.parent / "textures" / "du"
REGION = "dagon urul region"


def load_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def source_key(path: str) -> tuple[str, str]:
    parts = PureWindowsPath(path).parts
    if len(parts) != 2:
        raise ValueError(f"Expected a base-game f/x mesh: {path}")
    return parts[0].casefold(), PureWindowsPath(parts[1]).stem.casefold()


def du_key(path: Path) -> tuple[str, str]:
    relative = path.relative_to(DU_MESHES)
    parts = relative.parts
    if len(parts) != 2 or parts[0].casefold() not in {"f", "x"}:
        raise ValueError(f"Unexpected DU mesh path: {path}")
    stem = path.stem.casefold()
    # This supplied treestump file shortens Flora to F and omits the underscore.
    if stem.endswith("_du"):
        stem = stem[:-3]
    elif stem == "f_treestump_wg_02du":
        stem = "flora_treestump_wg_02"
    else:
        raise ValueError(f"DU mesh does not have a DU suffix: {path}")
    if stem in {"rock_coastal_01", "rock_coastal_02", "rock_coastal_03"}:
        stem = "ex_t_" + stem
    return parts[0].casefold(), stem


def build(tamriel_data_path: Path = DEFAULT_TAMRIEL_DATA) -> tuple[list[dict], Counter]:
    template = load_json(DATA / "DU_template.json")
    if len(template) != 1 or template[0]["type"] != "Header":
        raise ValueError("DU_template.json must contain only a TES3 Header")
    header = copy.deepcopy(template[0])
    masters = [name.casefold() for name, _size in header["masters"]]
    if "tr_mainland.esm" not in masters:
        raise ValueError("TR_Mainland.esm is absent from template masters")
    tr_master_index = masters.index("tr_mainland.esm") + 1
    tamriel_master_size = next(
        size for name, size in header["masters"]
        if name.casefold() == "tamriel_data.esm"
    )
    if tamriel_data_path.stat().st_size != tamriel_master_size:
        raise ValueError("Tamriel_Data.esm does not match the template master header")

    du_by_key = {}
    for path in DU_MESHES.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.casefold() != ".nif":
            raise ValueError(f"Unexpected DU file: {path}")
        key = du_key(path)
        if key in du_by_key:
            raise ValueError(f"Multiple DU meshes match {key}")
        du_by_key[key] = path
    if not du_by_key:
        raise ValueError(f"No DU meshes found under {DU_MESHES}")

    with (ROOT / "mesh_inventory.csv").open(newline="", encoding="utf-8") as stream:
        inventory = list(csv.DictReader(stream))
    selected = {}
    for row in inventory:
        path = row["source_mesh"]
        if len(PureWindowsPath(path).parts) != 2:
            continue  # TR meshes have no supplied replacement.
        key = source_key(path)
        if key in du_by_key:
            selected[row["id"].casefold()] = (row, du_by_key[key])
    if len(selected) != len(du_by_key):
        used = {source_key(row["source_mesh"]) for row, _path in selected.values()}
        raise ValueError(f"Unmatched DU meshes: {set(du_by_key) - used}")

    statics = {}
    containers = {}
    landscapes = {}
    land_textures = {}
    for filename in ("Morrowind.json", "TR_Mainland.json"):
        records = load_json(DATA / filename)
        for record in records:
            if record["type"] == "Static":
                statics[record["id"].casefold()] = record
            elif record["type"] == "Container":
                containers[record["id"].casefold()] = record
            elif filename == "TR_Mainland.json" and record["type"] == "Landscape":
                landscapes[tuple(record["grid"])] = record
            elif filename == "TR_Mainland.json" and record["type"] == "LandscapeTexture":
                if record["index"] in land_textures:
                    raise ValueError(f"Duplicate TR land texture index {record['index']}")
                land_textures[record["index"]] = record
        if filename == "TR_Mainland.json":
            cells = [r for r in records if r["type"] == "Cell"
                     and r.get("region", "").casefold() == REGION]

    new_statics = []
    replacement_ids = {}
    for original_id, (row, du_path) in selected.items():
        original = statics.get(original_id)
        if original is None or original["mesh"].casefold() != row["source_mesh"].casefold():
            raise ValueError(f"Static definition does not match inventory: {row['id']}")
        new_id = original["id"] + "_du"
        if new_id.casefold() in statics:
            raise ValueError(f"Static ID already exists in a master: {new_id}")
        new_static = copy.deepcopy(original)
        new_static["id"] = new_id
        new_static["mesh"] = str(du_path.relative_to(GAME_MESHES)).replace("/", "\\")
        new_statics.append(new_static)
        replacement_ids[original_id] = new_id

    with (ROOT / "plant_replacements.csv").open(newline="", encoding="utf-8") as stream:
        plant_rows = list(csv.DictReader(stream))
    if not plant_rows:
        raise ValueError("plant_replacements.csv has no mappings")
    target_ids = {row["with_what_to_replace"].casefold() for row in plant_rows}
    target_containers = {
        record["id"].casefold(): record
        for record in tamriel_containers(tamriel_data_path)
        if record["id"].casefold() in target_ids
    }
    if target_ids != target_containers.keys():
        raise ValueError(f"Tamriel Data containers not found: {target_ids - target_containers.keys()}")

    plant_replacement_ids = {}
    for row in plant_rows:
        original_id = row["what_to_replace"].casefold()
        if original_id in plant_replacement_ids:
            raise ValueError(f"Duplicate source plant: {row['what_to_replace']}")
        if original_id not in containers:
            raise ValueError(f"Container source not found: {row['what_to_replace']}")
        target = target_containers[row["with_what_to_replace"].casefold()]
        plant_replacement_ids[original_id] = target["id"]

    output_cells = []
    replaced = Counter()
    plants_replaced = Counter()
    actual_refs = Counter()
    for cell in cells:
        grid = tuple(cell["data"]["grid"])
        refs = []
        for original_ref in cell["references"]:
            original_id = original_ref["id"].casefold()
            if original_id in replacement_ids:
                new_id = replacement_ids[original_id]
            elif original_id in plant_replacement_ids:
                new_id = plant_replacement_ids[original_id]
            else:
                continue
            if original_ref["mast_index"] != 0:
                raise ValueError(f"Unexpected original master index at {grid}: {original_ref}")
            ref = copy.deepcopy(original_ref)
            ref["mast_index"] = tr_master_index
            ref["id"] = new_id
            refs.append(ref)
            if original_id in replacement_ids:
                replaced[original_id] += 1
                actual_refs[(grid, original_ref["refr_index"], original_id)] += 1
            else:
                plants_replaced[original_id] += 1
        if refs:
            new_cell = {key: copy.deepcopy(value) for key, value in cell.items()
                        if key != "references"}
            new_cell["references"] = refs
            output_cells.append(new_cell)

    with (ROOT / "target_references.csv").open(newline="", encoding="utf-8") as stream:
        inventory_refs = Counter()
        for row in csv.DictReader(stream):
            if row["id"].casefold() not in replacement_ids:
                continue
            grid_text = row["cell"].rsplit("(", 1)[1].rstrip(")")
            grid = tuple(int(value.strip()) for value in grid_text.split(","))
            if int(row["master_index"]) != 0:
                raise ValueError(f"Unexpected target master index: {row}")
            inventory_refs[(grid, int(row["object_index"]), row["id"].casefold())] += 1
    if actual_refs != inventory_refs:
        raise ValueError(f"Reference mismatch: {actual_refs - inventory_refs}, "
                         f"{inventory_refs - actual_refs}")
    expected = {key: int(row["references"]) for key, (row, _path) in selected.items()}
    if dict(replaced) != expected:
        raise ValueError("Replacement counts do not match mesh_inventory.csv")
    with (ROOT / "container_plants.csv").open(newline="", encoding="utf-8") as stream:
        plant_inventory = {row["id"].casefold(): int(row["references"])
                           for row in csv.DictReader(stream)}
    expected_plants = {key: plant_inventory[key] for key in plant_replacement_ids}
    if dict(plants_replaced) != expected_plants:
        raise ValueError("Replacement counts do not match container_plants.csv")

    with (ROOT / "land_texture_replacements.csv").open(newline="", encoding="utf-8") as stream:
        texture_rows = list(csv.DictReader(stream))
    texture_by_id = {record["id"].casefold(): record for record in land_textures.values()}
    texture_targets = {}
    for row in texture_rows:
        source_id = row["what_to_replace"].casefold()
        target = row["with_what_to_replace"]
        if source_id in texture_targets:
            raise ValueError(f"Duplicate source land texture: {row['what_to_replace']}")
        if source_id not in texture_by_id:
            raise ValueError(f"Land texture not found in TR_Mainland.esm: {row['what_to_replace']}")
        if not target or target.casefold().endswith(".dds"):
            raise ValueError(f"Expected a DDS filename stem: {target}")
        if not (DU_TEXTURES / f"{target}.dds").is_file():
            raise ValueError(f"Replacement texture not found: {DU_TEXTURES / (target + '.dds')}")
        texture_targets[source_id] = target
    if not texture_targets:
        raise ValueError("land_texture_replacements.csv has no mappings")

    new_landscapes = []
    used_ltex_indices = set()
    swapped_tiles = Counter()
    for cell in cells:
        grid = tuple(cell["data"]["grid"])
        land = landscapes.get(grid)
        if land is None:
            raise ValueError(f"Missing landscape for Dagon Urul cell {grid}")
        raw = zstd.decompress(base64.b64decode(land["texture_indices"]["data"]))
        if len(raw) != 512:
            raise ValueError(f"Unexpected LAND VTEX length at {grid}: {len(raw)}")
        vtex_values = struct.unpack("<256H", raw)
        counts = Counter(vtex_values)
        selected_in_cell = False
        for vtex_index, count in counts.items():
            if vtex_index == 0:
                continue  # The game's default ground texture has no LTEX record.
            source = land_textures.get(vtex_index - 1)
            if source is None:
                raise ValueError(f"Missing TR LTEX index {vtex_index - 1} at {grid}")
            if source["id"].casefold() in texture_targets:
                swapped_tiles[source["id"].casefold()] += count
                selected_in_cell = True
        if selected_in_cell:
            # LAND VTEX values are looked up in the LAND record's owning plugin.
            # Keep the original indices and provide matching LTEX entries below.
            new_landscapes.append(copy.deepcopy(land))
            used_ltex_indices.update(index - 1 for index in vtex_values if index)

    with (ROOT / "ground_textures.csv").open(newline="", encoding="utf-8") as stream:
        texture_inventory = {row["id"].casefold(): int(row["tiles"])
                             for row in csv.DictReader(stream)}
    expected_tiles = {key: texture_inventory[key] for key in texture_targets}
    if dict(swapped_tiles) != expected_tiles:
        raise ValueError("Swapped tile counts do not match ground_textures.csv")

    new_land_textures = []
    for index in sorted(used_ltex_indices):
        original = land_textures[index]
        new_id = original["id"] + "_du"
        if new_id.casefold() in texture_by_id:
            raise ValueError(f"Land texture ID already exists in TR: {new_id}")
        new_texture = copy.deepcopy(original)
        new_texture["id"] = new_id
        if replacement := texture_targets.get(original["id"].casefold()):
            new_texture["file_name"] = f"du\\{replacement}.dds"
        new_land_textures.append(new_texture)

    header["author"] = "Dagon Urul Overhaul"
    header["description"] = "Region-scoped Dagon Urul meshes and ground textures"
    header["num_objects"] = (len(new_statics) + len(new_land_textures) + len(output_cells)
                             + len(new_landscapes))
    return [header, *new_land_textures, *new_statics,
            *output_cells, *new_landscapes], Counter({
        "statics": len(new_statics), "containers": 0,
        "cells": len(output_cells), "static_references": sum(replaced.values()),
        "plant_references": sum(plants_replaced.values()), "region_cells": len(cells),
        "land_textures": len(new_land_textures),
        "landscapes": len(new_landscapes), "swapped_texture_tiles": sum(swapped_tiles.values()),
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tamriel-data-esm", type=Path, default=DEFAULT_TAMRIEL_DATA)
    args = parser.parse_args()
    records, summary = build(args.tamriel_data_esm)
    destination = DATA / "DU_overhaul.json"
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(records, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Wrote {destination}: {dict(summary)}")
