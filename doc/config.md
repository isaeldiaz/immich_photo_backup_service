# Configuration

Config file: `config.json` (default). Override with `--config path`.

Access via `Config.get("section.key", default)`.

## `immich`

| Key | Example | Notes |
|---|---|---|
| `api_url` | `http://localhost:2283` | Host-side port (NOT container port 3001) |
| `api_key` | `YOUR_IMMICH_API_KEY` | Immich API key |
| `data_dir` | `/path/to/immich/upload` | Immich upload volume root |
| `backup_dir` | `/path/to/immich/upload/backups` | DB backup location (legacy) |
| `backup_source` | `native` | Unused in main flow |
| `docker_path_mappings` | `{"/mnt/media/photos": "/path/to/your/external/library"}` | Container -> host path translation |

## `external_library`

| Key | Example | Notes |
|---|---|---|
| `base_dir` | `/path/to/your/external/library` | NAS root; also the organize source default |
| `photos_subdir` | `…/immich` | Unused in main flow |
| `duplicates_subdir` | `…/immich/duplicates` | Unused in main flow |
| `metadata_subdir` | `…/immich/metadata` | Unused in main flow |

## `sync`

| Key | Default | Notes |
|---|---|---|
| `hash_algorithm` | `sha256` | Any `hashlib`-supported algorithm |
| `hash_length` | `12` | Hex characters embedded in filenames |
| `verify_copies` | `true` | Re-hash after copy to detect corruption |
| `archive_originals` | `true` | Unused flag (archiving always runs) |

## `archive`

Used only by `DatabaseArchiver` (legacy).

| Key | Default |
|---|---|
| `retention_days` | `7` |
| `archive_subdir` | `archived` |
| `cleanup_empty_dirs` | `true` |

## `logging`

| Key | Default | Notes |
|---|---|---|
| `level` | `INFO` | Override with `--verbose` for DEBUG |
| `file` | `logs/immich_backup.log` | Rotating file handler |
| `max_size` | `10MB` | Per log file |
| `backup_count` | `5` | Number of rotated files to keep |

## `features`

All boolean flags, currently informational only (date extraction is always attempted):

- `exif_date_extraction`
- `filename_date_extraction`
- `folder_date_extraction`
