# Dagon Urul rock, tree, plant, and ground replacement

This project builds a region-scoped inventory from TES3 text exports, prepares replacement NIF meshes, and generates a TES3 JSON plugin that changes only placed objects in Dagon Urul cells. The supplied text exports are UTF-16.

## Current inputs

- `dagon_urul_cells.txt`: cell references exported from `TR_Mainland.esm`.
- `mw_statics.txt`, `tamriel_statics.txt`, `tr_statics.txt`: static IDs and model paths. Definitions are applied in that master load order.

Run `python3 build_inventory.py` to regenerate `mesh_inventory.csv`, `target_references.csv`, `unresolved_candidates.csv`, and `inventory_summary.json`. The inventory has each matching static ID, its current game-relative NIF path, occurrence count, and number of cells. The target list has cell names and `FRMR` object/master indexes for each matching placement. `rock` and `tree` in an ID or model path are discovery hints: mark `include` as `yes` only for the IDs to replace. Leave incidental rock structures or tree stumps blank. `unresolved_candidates.csv` lists names that look relevant but have no `STAT` definition in the three supplied exports.

Regenerating the inventory keeps existing `include` and `replacement_mesh` entries by static ID.

## Container plants and ground textures

Run `python3 build_environment_inventory.py` to regenerate `container_plants.csv`, `ground_textures.csv`, and `environment_inventory_summary.json`. This requires Python 3.14 for built-in Zstandard decoding and the matching `Tamriel_Data.esm`. The script uses the game installation at `/mnt/g/Steam/steamapps/common/Morrowind/Data Files/Tamriel_Data.esm` by default; pass `--tamriel-data-esm PATH` for another location. It checks the file size against the master listed in `TR_Mainland.json`.

The lists cover all **103** exterior `TR_Mainland.esm` cells assigned to Dagon Urul Region. `container_plants.csv` contains **40** `CONT` flora IDs with **1,278** placed references, including harvestable fungi and lichens. It matches IDs against Morrowind, Tamriel Data, and TR container definitions in load order. Red coral is excluded because it is not a plant. `ground_textures.csv` contains **42** painted `LTEX` IDs using **40** distinct texture files over **26,368** landscape texture tiles (256 per cell). Each row gives the ID, asset path, number of placements or tiles, number of cells, and cell coordinates. The 85 cells mentioned below are only those containing rock or tree replacements.

Ground texture values come from each region cell's `LAND.texture_indices`; the stored `VTEX` value is one greater than its `LTEX` index, as described in [OpenMW's terrain storage source](https://github.com/OpenMW/openmw/blob/master/components/esmterrain/storage.cpp). A zero value would denote the default ground texture; none occurs in these cells. Tile counts are painted texture slots, not surface area or visual blend percentages.

`plant_replacements.csv` contains the 17 selected container plants and their Tamriel Data replacement container IDs. The overhaul builder verifies those IDs against the matching `Tamriel_Data.esm` and changes only their placed references in Dagon Urul to point directly to the existing Tamriel Data containers. It creates no new container records. The replacement plants use their own names, scripts, flags, and harvest inventory. Other container plants are left alone.

`land_texture_replacements.csv` contains the 34 selected `LTEX` IDs and the replacement DDS filename stems. The files must exist under the game's `Data Files/textures/du/` folder. The builder copies the 89 Dagon Urul `LAND` records that use those textures and adds 42 plugin-local `LTEX` records at the original indices: 34 point to the selected DU DDS files and eight retain their original paths. It leaves the `LAND` texture index bytes, heights, normals, and vertex colors intact. This changes **15,900** painted texture tiles and leaves 14 region landscapes and all landscapes outside the region untouched. The matching LTEX entries are necessary because [OpenMW resolves each LAND texture index using the LAND record's owning plugin](https://github.com/OpenMW/openmw/blob/master/components/esmterrain/storage.cpp).

## Asset layout

Replacement meshes are installed under the game's `Data Files/meshes/DU/f` and `DU/x` folders. Copies of original game assets are local working files under `data/original/`; `.gitignore` excludes that folder from commits. The plugin builder matches installed DU meshes to the source static records by their original mesh paths.

Run `python3 copy_original_nifs.py` to copy every unique NIF in the inventory from the local Morrowind `Data Files/meshes` folder into `data/original/Meshes/`, preserving the game-relative path. The script uses the WSL `G:` mount when available; on Windows it defaults to `G:\Steam\steamapps\common\Morrowind\Data Files\meshes`. It falls back to `Morrowind.bsa` for missing loose meshes. You can pass `--source` to use another installation and repeat `--archive` for additional TES3 BSA archives. It writes `copy_report.csv`, does not replace differing existing files unless `--overwrite` is specified, and exits with an error when any requested NIF is still missing. The BSA reader follows [OpenMW's TES3 BSA layout](https://github.com/OpenMW/openmw/blob/master/components/bsa/bsafile.cpp).

PowerShell example: `py .\copy_original_nifs.py --source 'G:\Steam\steamapps\common\Morrowind\Data Files\meshes'`

## Overhaul plugin

The edited static meshes are installed under `G:\Steam\steamapps\common\Morrowind\Data Files\meshes\DU\f` and `DU\x`. Run `python3 build_du_overhaul.py` to regenerate `data/DU_overhaul.json` from those meshes, the inventories, both replacement CSVs, `data/DU_template.json`, `data/Morrowind.json`, `data/TR_Mainland.json`, and the matching `Tamriel_Data.esm`. Pass `--tamriel-data-esm PATH` if it is installed elsewhere.

The generated plugin adds 76 static IDs with `_du` suffixes and changes 3,197 static and 933 plant references in 85 Dagon Urul Region cells. It adds no container records. It also adds 42 landscape texture records and overrides 89 Dagon Urul landscape records. It includes no cell or landscape records outside the region and makes no changes to the original IDs. Ten inventory statics have no DU mesh; their 19 placements remain untouched. The script checks static definitions and mesh paths against the inputs, verifies static references against `target_references.csv`, and verifies plant and texture counts against their inventories.

The current `DU_overhaul.esp` was converted from `data/DU_overhaul.json`. After changing a mapping and rebuilding the JSON, regenerate the ESP with `'/mnt/g/Steam/steamapps/common/Morrowind/Data Files/tes3conv.exe' data/DU_overhaul.json DU_overhaul.esp --overwrite`. Load it after `TR_Mainland.esm` with the same master versions listed in the JSON header.

## Grass plugin without Dagon Urul cells

Run `python3 build_fgm_tr_no_dagon_urul.py` to create `data/FGM_TR_no_Dagon_Urul.json` from `data/FGM_TR.json`. The builder uses the region assignments in `data/TR_Mainland.json` to identify Dagon Urul exterior grids, because the grass plugin's cell records do not contain region names. It removes those cell records, keeps all other records, and updates the header object count. Convert the result to `FGM_TR_no_Dagon_Urul.esp` with `tes3conv.exe`, and use it in place of the original `FGM_TR.esp` for MGE XE grass generation.
