#!/usr/bin/env python3
"""
find_nas_duplicates.py — Find and move duplicate photos in the NAS library
based on the 12-char SHA256 hash embedded in filenames (or computed from content
for files that don't have one).

Usage:
    python3 find_nas_duplicates.py [OPTIONS] [SOURCE_DIR]

Options:
    SOURCE_DIR      Directory to scan (required)
    --dest DIR      Where to move duplicates (required)
    --execute       Actually move files; without this flag only a dry-run is printed

How duplicates are resolved:
    Among files sharing the same hash, the one with the shallowest directory
    depth is kept (ties broken alphabetically). All others are moved to --dest,
    preserving their relative path under SOURCE_DIR to avoid name collisions.
"""

import argparse
import hashlib
import sys
from pathlib import Path

MEDIA_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".raw",
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v", ".mpg", ".mpeg", ".flv",
})

HASH_LENGTH = 12


def get_hash_from_filename(filename: str) -> str | None:
    """Extract the 12-char hex hash from a filename like `stem_abc123def456.ext`."""
    stem = Path(filename).stem
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    candidate = parts[-1]
    if len(candidate) == HASH_LENGTH and all(c in "0123456789abcdef" for c in candidate.lower()):
        return candidate.lower()
    return None


def calculate_hash(file_path: Path) -> str:
    """SHA256 of file content, first 12 hex chars."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_LENGTH]


def scan(source_dir: Path) -> dict[str, list[Path]]:
    """Walk source_dir and return hash -> [paths] for every hash seen more than once."""
    hash_map: dict[str, list[Path]] = {}
    total = 0
    computed = 0

    print(f"Scanning {source_dir} ...")
    for file_path in sorted(source_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        total += 1
        file_hash = get_hash_from_filename(file_path.name)
        if file_hash is None:
            file_hash = calculate_hash(file_path)
            computed += 1

        hash_map.setdefault(file_hash, []).append(file_path)

        if total % 500 == 0:
            print(f"  ... {total} files scanned")

    unique = sum(1 for v in hash_map.values() if len(v) == 1)
    dup_groups = {h: paths for h, paths in hash_map.items() if len(paths) > 1}
    dup_files = sum(len(v) - 1 for v in dup_groups.values())

    print(
        f"Done. {total} media files scanned "
        f"({computed} hashes computed from content, {total - computed} from filename)."
    )
    print(f"  {unique} unique files, {len(dup_groups)} duplicate groups, {dup_files} files to move.")
    return dup_groups


def choose_keeper(paths: list[Path]) -> Path:
    """Keep the shallowest (most canonical) path; tie-break alphabetically."""
    return min(paths, key=lambda p: (len(p.parts), str(p)))


def report_and_move(
    duplicates: dict[str, list[Path]],
    source_dir: Path,
    dest_dir: Path,
    execute: bool,
) -> None:
    if not duplicates:
        print("\nNo duplicates found.")
        return

    if execute:
        dest_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for file_hash, paths in sorted(duplicates.items()):
        keeper = choose_keeper(paths)
        to_move = [p for p in paths if p != keeper]

        print(f"\nhash={file_hash}")
        print(f"  KEEP : {keeper}")
        for dup in to_move:
            rel = dup.relative_to(source_dir)
            target = dest_dir / rel
            print(f"  MOVE : {dup}")
            print(f"    ->   {target}")
            if execute:
                target.parent.mkdir(parents=True, exist_ok=True)
                dup.rename(target)
                moved += 1

    if not execute:
        total = sum(len(v) - 1 for v in duplicates.values())
        print(f"\n--- DRY RUN: {total} file(s) would be moved to {dest_dir} ---")
        print("Re-run with --execute to apply.")
    else:
        print(f"\nMoved {moved} duplicate(s) to {dest_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find and move duplicate NAS photos by SHA256 filename hash.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "source", nargs="?", default=None,
        help="Directory to scan",
    )
    parser.add_argument(
        "--dest", default=None,
        help="Destination for duplicate files",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually move files (default: dry-run only)",
    )
    args = parser.parse_args()

    if args.source is None:
        parser.error("SOURCE_DIR is required")
    if args.dest is None:
        parser.error("--dest is required")

    source_dir = Path(args.source).resolve()
    dest_dir = Path(args.dest).resolve()

    if not source_dir.exists():
        print(f"Error: source directory does not exist: {source_dir}", file=sys.stderr)
        sys.exit(1)

    # Guard against accidentally nesting dest inside source
    try:
        dest_dir.relative_to(source_dir)
        print(
            f"Error: --dest ({dest_dir}) must be outside the source directory ({source_dir}).",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError:
        pass  # dest is not under source — good

    duplicates = scan(source_dir)
    report_and_move(duplicates, source_dir, dest_dir, execute=args.execute)


if __name__ == "__main__":
    main()
