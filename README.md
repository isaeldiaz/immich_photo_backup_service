# photo_server

Syncs photos from an [Immich](https://immich.app) instance to long-term NAS storage, then marks the originals as archived in Immich so they no longer clutter the main timeline.

## What it does

1. Queries Immich's REST API for all unarchived assets.
2. Copies each file to the NAS under a structured directory tree, embedding a SHA256 hash in the filename for deduplication.
3. Archives the originals in Immich (they remain searchable but are hidden from the main timeline).

Files on the NAS are organized as:

```
{base_dir}/{user}/{year}/{MM}-{MonthName}/{original_stem}_{hash12}.{ext}
```

Example: `alice/2023/06-June/IMG_20230615_abc123def456.jpg`

## Requirements

- Python 3.11+
- Immich running (Docker recommended)
- NAS mounted on the host running this script
- `pip install requests Pillow`

## Setup

1. Copy `config.example.json` to `config.json` and fill in your values:

```json
{
  "immich": {
    "api_url": "http://localhost:2283",
    "api_key": "YOUR_IMMICH_API_KEY",
    "data_dir": "/path/to/immich/upload",
    "docker_path_mappings": {
      "/mnt/media/photos": "/path/to/your/nas/library",
      "/usr/src/app/upload/upload": "/path/to/immich/upload/upload"
    }
  },
  "external_library": {
    "base_dir": "/path/to/your/nas/library"
  }
}
```

`docker_path_mappings` translates the container-side paths that Immich stores internally into host-side paths where this script can actually read the files.

Your Immich API key can be generated at **Account Settings > API Keys** in the Immich web UI.

## Usage

```bash
# Copy new assets to NAS and archive them in Immich
python immich_backup.py sync

# Preview what would be synced without making any changes
python immich_backup.py sync --dry-run

# Re-process all assets, ignoring previous sync state
python immich_backup.py sync --force-resync

# Show stats: last sync time, asset counts, NAS file count
python immich_backup.py status

# Rename existing NAS files to add hash suffixes (one-time migration)
python immich_backup.py organize

# Use a non-default config file
python immich_backup.py --config /path/to/config.json sync

# Verbose output (DEBUG logging)
python immich_backup.py --verbose sync
```

## Deduplication

Before copying, the script builds an index of all files already on the NAS by extracting the hash suffix from their filenames. A file is skipped if its hash is already present — even if it was uploaded by a different user or lives in a different folder. No file is ever stored twice.

## NAS duplicate cleanup

If you already have duplicate files on the NAS (from before hash-based naming was in use), use the included helper:

```bash
# Dry run: show what would be moved
python find_nas_duplicates.py /path/to/nas/library --dest /path/to/nas/duplicates

# Actually move duplicates
python find_nas_duplicates.py /path/to/nas/library --dest /path/to/nas/duplicates --execute
```

## Running tests

```bash
# Fast unit tests (no Docker required)
pytest -m unit

# Full suite including integration tests (requires Docker)
pytest
```

See `doc/testing.md` for details on the sandbox setup.
