"""Copy NIFs named in mesh_inventory.csv from a Morrowind Meshes folder."""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import struct
from collections import Counter
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parent
WINDOWS_MESHES = Path(r"G:\Steam\steamapps\common\Morrowind\Data Files\meshes")
WSL_MESHES = Path("/mnt/g/Steam/steamapps/common/Morrowind/Data Files/meshes")


def source_folder() -> Path:
    return WSL_MESHES if WSL_MESHES.is_dir() else WINDOWS_MESHES


def safe_parts(model: str) -> tuple[str, ...]:
    path = PureWindowsPath(model)
    parts = path.parts
    if path.drive or path.root or not parts or any(part in (".", "..") for part in parts):
        raise ValueError(f"unsafe model path: {model!r}")
    if path.suffix.casefold() != ".nif":
        raise ValueError(f"not a NIF path: {model!r}")
    return parts


def find_case_insensitive(root: Path, parts: tuple[str, ...]) -> Path | None:
    current = root
    for part in parts:
        direct = current / part
        if direct.exists():
            current = direct
            continue
        if not current.is_dir():
            return None
        matches = [entry for entry in current.iterdir() if entry.name.casefold() == part.casefold()]
        if len(matches) != 1:
            return None
        current = matches[0]
    return current if current.is_file() else None


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def archive_entries(archive: Path, needed: set[str]) -> dict[str, tuple[Path, int, int]]:
    """Index requested files in an uncompressed TES3 BSA archive."""
    found = {}
    with archive.open("rb") as stream:
        version, directory_size, count = struct.unpack("<III", stream.read(12))
        if version != 0x100 or directory_size < 12 * count:
            raise ValueError(f"unsupported or invalid TES3 BSA: {archive}")
        table = stream.read(12 * count)
        names = stream.read(directory_size - 12 * count)
        data_start = 12 + directory_size + 8 * count
        archive_size = archive.stat().st_size
        for index in range(count):
            size, offset = struct.unpack_from("<II", table, index * 8)
            (name_offset,) = struct.unpack_from("<I", table, 8 * count + 4 * index)
            if name_offset >= len(names):
                raise ValueError(f"invalid BSA name offset: {archive}")
            name_end = names.find(b"\0", name_offset)
            if name_end < 0 or data_start + offset + size > archive_size:
                raise ValueError(f"invalid BSA entry: {archive}")
            name = names[name_offset:name_end].decode("cp1252").replace("/", "\\").casefold()
            if name in needed:
                found[name] = (archive, data_start + offset, size)
    return found


def read_archive_entry(entry: tuple[Path, int, int]) -> bytes:
    archive, offset, size = entry
    with archive.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"truncated BSA entry in {archive}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=source_folder(), help="Morrowind Data Files/meshes folder")
    parser.add_argument("--inventory", type=Path, default=ROOT / "mesh_inventory.csv")
    parser.add_argument("--destination", type=Path, default=ROOT / "data" / "original" / "Meshes")
    parser.add_argument("--report", type=Path, default=ROOT / "copy_report.csv")
    parser.add_argument("--archive", type=Path, action="append", help="TES3 BSA fallback; repeat for multiple archives")
    parser.add_argument("--overwrite", action="store_true", help="replace existing copies that differ from the source")
    args = parser.parse_args()

    if not args.source.is_dir():
        parser.error(f"source meshes folder does not exist: {args.source}")
    with args.inventory.open(encoding="utf-8", newline="") as stream:
        inventory = list(csv.DictReader(stream))
    paths = {}
    for row in inventory:
        model = row["source_mesh"].strip()
        parts = safe_parts(model)
        key = tuple(part.casefold() for part in parts)
        paths.setdefault(key, (model, parts))

    archives = args.archive if args.archive is not None else [args.source.parent / "Morrowind.bsa"]
    needed = {"meshes\\" + "\\".join(parts).casefold() for _, parts in paths.values()}
    archived = {}
    for archive in archives:
        if archive.is_file():
            archived.update(archive_entries(archive, needed))

    report = []
    for model, parts in paths.values():
        source = find_case_insensitive(args.source, parts)
        target = args.destination.joinpath(*parts)
        archive_entry = archived.get("meshes\\" + "\\".join(parts).casefold()) if source is None else None
        source_label = str(source) if source else ""
        if source is None and archive_entry is None:
            status = "missing"
        else:
            archived_data = read_archive_entry(archive_entry) if archive_entry else None
            source_hash = digest(source) if source else hashlib.sha256(archived_data).hexdigest()
            if archive_entry:
                source_label = f"{archive_entry[0]}::meshes\\{'\\'.join(parts)}"
            if target.is_file() and source_hash == digest(target):
                status = "already_copied"
            elif target.exists() and not args.overwrite:
                status = "different_existing_file"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if source:
                    shutil.copy2(source, target)
                    status = "copied"
                else:
                    target.write_bytes(archived_data)
                    status = "copied_archive"
        report.append({"source_mesh": model, "status": status,
                       "source_file": source_label, "destination_file": str(target)})

    with args.report.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_mesh", "status", "source_file", "destination_file"])
        writer.writeheader()
        writer.writerows(report)
    counts = Counter(row["status"] for row in report)
    print(f"Source: {args.source}")
    print(f"Destination: {args.destination}")
    print(f"Unique NIFs: {len(report)}; " + "; ".join(f"{key}: {value}" for key, value in sorted(counts.items())))
    print(f"Report: {args.report}")
    for row in report:
        if row["status"] in ("missing", "different_existing_file"):
            print(f"{row['status']}: {row['source_mesh']}")
    return 0 if not (counts["missing"] or counts["different_existing_file"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
