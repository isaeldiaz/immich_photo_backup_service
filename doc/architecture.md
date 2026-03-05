# Architecture

## System overview

```
Immich server (Docker)
  immich_server  -- REST API on localhost:2283
  immich_postgres -- PostgreSQL

NAS mount: /path/to/your/external/library  (external Immich library)
Local disk: /path/to/immich/upload         (Immich upload volume)
```

## Sync data flow

```
ImmichAPI.get_all_unarchived_assets()
  -> paginated POST /api/search/metadata (isArchived=false)
  -> returns list of asset dicts with {id, originalPath, ownerId, fileCreatedAt}

For each asset:
  1. map_container_path(originalPath, docker_mappings)  -> host Path
  2. FileHasher.calculate_hash(host_path)               -> 12-hex string
  3. FileHasher.is_duplicate(hash)                      -> skip if already on NAS
  4. FileOrganizer.extract_date(host_path, exif_date)   -> datetime
  5. FileOrganizer.generate_target_path(...)            -> NAS path
  6. shutil.copy2(source, target) + hash verify
  7. Collect asset_id for archiving

Archiver.archive_assets(ids)
  -> batched PUT /api/assets {ids, visibility:"archive"}

save sync_state.json
```

## Path mapping

Immich stores `originalPath` as Docker container paths. The mapping is defined in `config.json`:

```json
"docker_path_mappings": {
  "/mnt/media/photos": "/path/to/your/external/library",
  "/usr/src/app/upload/upload": "/path/to/immich/upload/upload"
}
```

Longest-prefix match wins. `map_container_path()` is in `immich_backup.py`.

## NAS directory structure

```
/path/to/your/external/library/
  {user_name}/
    {year}/
      {MM}-{MonthName}/
        {original_stem}_{sha256_12hex}.{ext}
```

Example: `alice/2023/06-June/IMG_20230615_abc123def456.jpg`

## Deduplication

`FileHasher` builds an in-memory index `{hash12 -> Path}` by scanning the NAS before sync starts.

Fast path: if a source file already has a `_hash12` suffix in its name, the index lookup skips reading the file — critical for large NAS libraries.

## Date extraction priority (FileOrganizer.extract_date)

1. `fileCreatedAt` from Immich API (ISO string)
2. EXIF tags: `DateTimeOriginal`, `DateTime`, `DateTimeDigitized` (requires Pillow)
3. Filename patterns: `IMG_YYYYMMDD_`, `YYYY-MM-DD`, `YYYYMMDD`, `DD.MM.YYYY`
4. Folder name patterns: `YYYY-MM-DD`, `DD.Month.YYYY`, `DD Month YYYY`, bare `YYYY`
5. File modification time (fallback)

## Legacy modules

`database.py` and `database_archiver.py` parse SQL dumps and run `psql` inside the `immich_postgres` container directly. These are not used in the main sync flow — the REST API path is preferred.
