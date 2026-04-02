#!/usr/bin/env python3
"""
Immich Backup System

CLI entry point for syncing photos from Immich to a NAS-backed external library.

Commands:
  sync      - List unarchived assets, copy to NAS, archive in Immich
  status    - Show configuration, last sync time, and statistics
  organize  - Rename existing NAS files with hash suffixes
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.config import Config
from lib.logger import setup_logging, get_logger
from lib.hasher import FileHasher
from lib.immich_api import ImmichAPI
from lib.organizer import FileOrganizer
from lib.archiver import Archiver


# ------------------------------------------------------------------
# State management
# ------------------------------------------------------------------

DEFAULT_STATE_FILE = Path(__file__).parent / "sync_state.json"


def load_state(path: Path = None) -> dict:
    """Load sync state from disk, returning defaults if missing or corrupt."""
    target = path if path is not None else DEFAULT_STATE_FILE
    if target.exists():
        try:
            with open(target, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_sync_time": None,
        "synced_files": {},
        "total_synced": 0,
    }


def save_state(state: dict, path: Path = None) -> None:
    """Persist sync state to disk."""
    target = path if path is not None else DEFAULT_STATE_FILE
    with open(target, "w") as f:
        json.dump(state, f, indent=2)


# ------------------------------------------------------------------
# Docker path mapping
# ------------------------------------------------------------------

def map_container_path(container_path: str, mappings: dict) -> Path:
    """Translate a container path to a host path using the configured mappings.

    Tries longest-prefix match first so that more specific mappings win.
    Returns None if no mapping matches.
    """
    # Sort by prefix length descending so longest match wins
    sorted_prefixes = sorted(mappings.keys(), key=len, reverse=True)
    for prefix in sorted_prefixes:
        if container_path.startswith(prefix):
            host_prefix = mappings[prefix]
            relative = container_path[len(prefix):]
            return Path(host_prefix + relative)
    return None


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------

def cmd_sync(config: Config, dry_run: bool = False, force_resync: bool = False, state_file: Path = None) -> bool:
    """Main sync workflow: list unarchived -> hash -> copy to NAS -> archive."""
    logger = get_logger("immich_backup")
    logger.info("Starting sync operation (dry_run=%s, force_resync=%s)", dry_run, force_resync)

    base_url = config.get("immich.api_url")
    docker_mappings = config.get("immich.docker_path_mappings", {})
    target_base_dir = Path(config.get("external_library.base_dir"))

    # Support both api_keys (list) and legacy api_key (single string)
    api_keys: list[str] = config.get("immich.api_keys") or []
    if not api_keys:
        single_key = config.get("immich.api_key")
        if single_key:
            api_keys = [single_key]
    if not api_keys:
        logger.error("No API keys configured. Set 'immich.api_keys' in config.")
        return False

    # Build shared components
    hasher = FileHasher(
        hash_algorithm=config.get("sync.hash_algorithm", "sha256"),
        hash_length=config.get("sync.hash_length", 12),
    )
    organizer = FileOrganizer(
        hasher=hasher,
        target_base_dir=target_base_dir,
        verify_copies=config.get("sync.verify_copies", True),
    )

    # 1. Reachability check
    if not ImmichAPI(base_url=base_url, api_key=api_keys[0]).ping():
        logger.error("Cannot reach Immich server at %s", base_url)
        return False

    # 2. Build hash index of existing NAS files once (shared across all users)
    logger.info("Building hash index of NAS library...")
    hasher.build_hash_index(target_base_dir)

    # 3. Load state
    state = load_state(state_file)
    synced_files = state.get("synced_files", {})

    stats = {
        "total_assets": 0,
        "new_files": 0,
        "duplicates": 0,
        "skipped": 0,
        "errors": 0,
        "archived": 0,
    }

    logger.info("Syncing %d user(s)...", len(api_keys))

    for api_key in api_keys:
        api = ImmichAPI(base_url=base_url, api_key=api_key)
        archiver = Archiver(api=api)

        # 4. Fetch unarchived assets for this user
        logger.info("Fetching unarchived assets...")
        assets = api.get_all_unarchived_assets()

        # Only process upload library assets (libraryId is None).
        # External library assets are already on the NAS — syncing them would
        # archive the external library record, removing it from the timeline.
        upload_assets = [a for a in assets if a.get("libraryId") is None]
        logger.info(
            "Found %d unarchived assets (%d upload library, %d external library skipped)",
            len(assets), len(upload_assets), len(assets) - len(upload_assets),
        )
        assets = upload_assets
        stats["total_assets"] += len(assets)

        if not assets:
            logger.info("No unarchived assets for this user. Skipping.")
            continue

        # User lookup cache (per API key, scoped to this user's session)
        user_cache: dict[str, str] = {}

        def get_user_name(owner_id: str, _api: ImmichAPI = api) -> str:
            if owner_id not in user_cache:
                try:
                    user = _api.get_user(owner_id)
                    user_cache[owner_id] = user.get("name", owner_id)
                except Exception as exc:
                    logger.warning("Could not look up user %s: %s", owner_id, exc)
                    user_cache[owner_id] = owner_id
            return user_cache[owner_id]

        ids_to_archive: list[str] = []

        for i, asset in enumerate(assets, 1):
            asset_id = asset.get("id")
            original_path = asset.get("originalPath", "")

            # Skip already-synced unless forced
            if not force_resync and asset_id in synced_files:
                stats["skipped"] += 1
                continue

            # 4a. Map container path to host path
            host_path = map_container_path(original_path, docker_mappings)
            if host_path is None:
                logger.warning(
                    "[%d/%d] No path mapping for container path: %s",
                    i, len(assets), original_path,
                )
                stats["errors"] += 1
                continue

            # 4b. Verify file exists on disk
            if not host_path.exists():
                logger.warning(
                    "[%d/%d] File not found on disk: %s (mapped from %s)",
                    i, len(assets), host_path, original_path,
                )
                stats["errors"] += 1
                continue

            # 4c. Get user name
            owner_id = asset.get("ownerId", "unknown")
            user_name = get_user_name(owner_id)

            # 4d. Organize file
            exif_date = asset.get("fileCreatedAt")

            if dry_run:
                try:
                    file_hash = hasher.calculate_hash(host_path)
                    is_dup = hasher.is_duplicate(file_hash)
                except Exception as exc:
                    logger.error("[%d/%d] Hash error for %s: %s", i, len(assets), host_path, exc)
                    stats["errors"] += 1
                    continue

                if is_dup:
                    stats["duplicates"] += 1
                    logger.info("[%d/%d] Would skip (duplicate): %s", i, len(assets), host_path)
                else:
                    stats["new_files"] += 1
                    logger.info("[%d/%d] Would copy: %s -> user=%s", i, len(assets), host_path, user_name)
            else:
                try:
                    result = organizer.organize_file(host_path, user_name, exif_date=exif_date)
                except Exception as exc:
                    logger.error("[%d/%d] Failed to organize %s: %s", i, len(assets), host_path, exc)
                    stats["errors"] += 1
                    continue

                if result["is_duplicate"]:
                    stats["duplicates"] += 1
                    logger.info("[%d/%d] Duplicate: %s (hash %s)", i, len(assets), host_path, result["hash"])
                else:
                    stats["new_files"] += 1
                    logger.info("[%d/%d] Copied: %s -> %s", i, len(assets), host_path, result["target_path"])

                # 4e. Collect for archiving
                ids_to_archive.append(asset_id)

                synced_files[asset_id] = {
                    "original_path": original_path,
                    "target_path": str(result["target_path"]),
                    "sync_time": datetime.now().isoformat(),
                }

        # 5. Archive this user's assets using their own API key
        if ids_to_archive and not dry_run:
            logger.info("Archiving %d assets in Immich...", len(ids_to_archive))
            archive_stats = archiver.archive_assets(ids_to_archive)
            stats["errors"] += archive_stats["errors"]
            stats["archived"] += archive_stats["archived"]

    # 6. Save state
    if not dry_run:
        state["synced_files"] = synced_files
        state["total_synced"] = len(synced_files)
        state["last_sync_time"] = datetime.now().isoformat()
        save_state(state, state_file)

    # Summary
    logger.info("--- Sync Summary ---")
    logger.info("Total assets:  %d", stats["total_assets"])
    logger.info("New files:     %d", stats["new_files"])
    logger.info("Duplicates:    %d", stats["duplicates"])
    logger.info("Skipped:       %d", stats["skipped"])
    logger.info("Errors:        %d", stats["errors"])
    if stats["archived"] and not dry_run:
        logger.info("Archived:      %d", stats["archived"])
    if dry_run:
        logger.info("(dry run - no changes made)")

    return stats["errors"] == 0


def cmd_status(config: Config, state_file: Path = None) -> bool:
    """Show configuration, last sync time, and statistics."""
    logger = get_logger("immich_backup")

    logger.info("=== Immich Backup Status ===")

    # Config paths
    logger.info("API URL:          %s", config.get("immich.api_url"))
    logger.info("NAS base dir:     %s", config.get("external_library.base_dir"))

    # Last sync
    state = load_state(state_file)
    last_sync = state.get("last_sync_time")
    logger.info("Last sync:        %s", last_sync if last_sync else "Never")
    logger.info("Total synced:     %d", state.get("total_synced", 0))

    # Unarchived count from API (sum across all configured users)
    api_keys: list[str] = config.get("immich.api_keys") or []
    if not api_keys:
        single_key = config.get("immich.api_key")
        if single_key:
            api_keys = [single_key]
    try:
        total_unarchived = 0
        reachable = False
        for api_key in api_keys:
            api = ImmichAPI(base_url=config.get("immich.api_url"), api_key=api_key)
            if api.ping():
                reachable = True
                total_unarchived += len(api.get_all_unarchived_assets())
        if reachable:
            logger.info("Unarchived assets: %d (across %d user(s))", total_unarchived, len(api_keys))
        else:
            logger.info("Unarchived assets: (server unreachable)")
    except Exception as exc:
        logger.info("Unarchived assets: (error: %s)", exc)

    # Hash index stats
    target_base_dir = Path(config.get("external_library.base_dir"))
    if target_base_dir.exists():
        hasher = FileHasher(
            hash_algorithm=config.get("sync.hash_algorithm", "sha256"),
            hash_length=config.get("sync.hash_length", 12),
        )
        index = hasher.build_hash_index(target_base_dir)
        logger.info("NAS hash index:   %d unique files", len(index))
    else:
        logger.info("NAS hash index:   (directory not found: %s)", target_base_dir)

    return True


def cmd_organize(
    config: Config,
    source: str = None,
    target: str = None,
    in_place: bool = False,
    recursive: bool = True,
    dry_run: bool = False,
) -> bool:
    """Rename existing NAS files with hash suffixes.

    Walks the directory tree and renames files that don't already have a hash
    suffix from ``name.ext`` to ``name_<hash>.ext``.
    """
    logger = get_logger("immich_backup")

    hasher = FileHasher(
        hash_algorithm=config.get("sync.hash_algorithm", "sha256"),
        hash_length=config.get("sync.hash_length", 12),
    )

    source_dir = Path(source) if source else Path(config.get("external_library.base_dir"))

    if in_place:
        target_dir = source_dir
    elif target:
        target_dir = Path(target)
    else:
        target_dir = source_dir  # default to in-place when no target given

    if not source_dir.exists():
        logger.error("Source directory does not exist: %s", source_dir)
        return False

    logger.info(
        "%sRenaming files with hash suffixes in %s",
        "DRY RUN: " if dry_run else "",
        source_dir,
    )

    from lib.hasher import MEDIA_EXTENSIONS

    stats = {"total": 0, "renamed": 0, "skipped": 0, "errors": 0}

    if recursive:
        file_iter = source_dir.rglob("*")
    else:
        file_iter = source_dir.glob("*")

    for file_path in file_iter:
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        stats["total"] += 1

        # Skip files that already have a hash suffix
        existing_hash = hasher.get_hash_from_filename(file_path.name)
        if existing_hash is not None:
            stats["skipped"] += 1
            continue

        # Calculate hash and build new name
        try:
            file_hash = hasher.calculate_hash(file_path)
        except Exception as exc:
            logger.error("Hash error for %s: %s", file_path, exc)
            stats["errors"] += 1
            continue

        new_name = f"{file_path.stem}_{file_hash}{file_path.suffix}"

        if in_place or target_dir == source_dir:
            new_path = file_path.parent / new_name
        else:
            # Preserve relative directory structure under target
            relative = file_path.parent.relative_to(source_dir)
            new_path = target_dir / relative / new_name

        if dry_run:
            logger.info("Would rename: %s -> %s", file_path, new_path)
        else:
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.rename(new_path)
                logger.info("Renamed: %s -> %s", file_path.name, new_name)
            except Exception as exc:
                logger.error("Failed to rename %s: %s", file_path, exc)
                stats["errors"] += 1
                continue

        stats["renamed"] += 1

    logger.info("--- Organize Summary ---")
    logger.info("Total media files: %d", stats["total"])
    logger.info("Renamed:           %d", stats["renamed"])
    logger.info("Already hashed:    %d", stats["skipped"])
    logger.info("Errors:            %d", stats["errors"])
    if dry_run:
        logger.info("(dry run - no changes made)")

    return stats["errors"] == 0


def cmd_scan_library(
    config: Config,
    library_id: str | None = None,
    refresh_modified: bool = False,
    refresh_all: bool = False,
) -> bool:
    """Trigger a rescan of one or all external libraries in Immich."""
    logger = get_logger("immich_backup")

    api_keys: list[str] = config.get("immich.api_keys") or []
    if not api_keys:
        single_key = config.get("immich.api_key")
        if single_key:
            api_keys = [single_key]
    if not api_keys:
        logger.error("No API keys configured.")
        return False

    api = ImmichAPI(base_url=config.get("immich.api_url"), api_key=api_keys[0])

    if not api.ping():
        logger.error("Cannot reach Immich server at %s", config.get("immich.api_url"))
        return False

    libraries = api.list_libraries()
    if not libraries:
        logger.info("No libraries found.")
        return True

    targets = [lib for lib in libraries if library_id is None or lib["id"] == library_id]
    if not targets:
        logger.error("Library not found: %s", library_id)
        logger.info("Available libraries:")
        for lib in libraries:
            logger.info("  %s  %s", lib["id"], lib.get("name", "(unnamed)"))
        return False

    success = True
    for lib in targets:
        lid = lib["id"]
        name = lib.get("name", lid)
        logger.info("Scanning library: %s (%s)", name, lid)
        try:
            api.scan_library(lid, refresh_modified_files=refresh_modified, refresh_all_files=refresh_all)
            logger.info("Scan triggered for: %s", name)
        except Exception as exc:
            logger.error("Failed to trigger scan for %s: %s", name, exc)
            success = False

    return success


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Immich Backup System - sync photos to NAS and archive in Immich",
    )
    parser.add_argument("--config", default="config.json", help="Configuration file path")
    parser.add_argument("--state-file", default=None, metavar="PATH",
        help="Sync state JSON file (default: sync_state.json next to this script)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # sync
    sync_parser = subparsers.add_parser("sync", help="List unarchived assets, copy to NAS, archive in Immich")
    sync_parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    sync_parser.add_argument("--force-resync", action="store_true", help="Re-process already-synced assets")

    # status
    subparsers.add_parser("status", help="Show config, last sync time, and statistics")

    # scan-library
    scan_parser = subparsers.add_parser("scan-library", help="Trigger an Immich external library rescan")
    scan_parser.add_argument("--library-id", default=None, metavar="ID",
        help="Library ID to scan (default: scan all libraries)")
    scan_parser.add_argument("--refresh-modified", action="store_true",
        help="Re-import files whose modification time changed")
    scan_parser.add_argument("--refresh-all", action="store_true",
        help="Force re-import of every file in the library")

    # organize
    organize_parser = subparsers.add_parser("organize", help="Rename existing NAS files with hash suffixes")
    organize_parser.add_argument("--source", help="Source directory (default: external_library.base_dir)")
    organize_parser.add_argument("--target", help="Target directory (default: same as source)")
    organize_parser.add_argument("--in-place", action="store_true", help="Rename files in place")
    organize_parser.add_argument("--recursive", action="store_true", default=True, help="Process directories recursively (default)")
    organize_parser.add_argument("--no-recursive", action="store_false", dest="recursive", help="Only process top-level directory")
    organize_parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        config = Config(args.config)
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    # Setup logging
    log_level = "DEBUG" if args.verbose else config.get("logging.level", "INFO")
    setup_logging(
        log_file=config.get("logging.file", "logs/immich_backup.log"),
        level=log_level,
        max_size=config.get("logging.max_size", "10MB"),
        backup_count=config.get("logging.backup_count", 5),
    )

    # Resolve --dry-run from either the top-level or subcommand flag
    dry_run = args.dry_run
    state_file = Path(args.state_file) if args.state_file else None

    try:
        if args.command == "sync":
            force_resync = getattr(args, "force_resync", False)
            success = cmd_sync(config, dry_run=dry_run, force_resync=force_resync, state_file=state_file)
        elif args.command == "scan-library":
            success = cmd_scan_library(
                config,
                library_id=getattr(args, "library_id", None),
                refresh_modified=getattr(args, "refresh_modified", False),
                refresh_all=getattr(args, "refresh_all", False),
            )
        elif args.command == "status":
            success = cmd_status(config, state_file=state_file)
        elif args.command == "organize":
            success = cmd_organize(
                config,
                source=getattr(args, "source", None),
                target=getattr(args, "target", None),
                in_place=getattr(args, "in_place", False),
                recursive=getattr(args, "recursive", True),
                dry_run=dry_run,
            )
        else:
            parser.print_help()
            return 1

        return 0 if success else 1

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        logger = get_logger("immich_backup")
        logger.error("Fatal error: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
