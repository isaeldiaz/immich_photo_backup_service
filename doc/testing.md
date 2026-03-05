# Testing

## Run tests

```bash
# Unit tests only (no Docker required)
pytest -m unit

# All tests including sandbox (requires Docker)
pytest

# Single file
pytest tests/test_hasher.py -v
```

## Test structure

```
tests/
  conftest.py          -- fixtures: sandbox, admin_api, nas_dir, upload_dir
  helpers/
    sandbox.py         -- start/stop Immich via docker-compose.test.yml
    image_generator.py -- create synthetic JPEG/PNG test images
  test_hasher.py       -- FileHasher: hash calculation, index building, dedup
  test_organizer.py    -- FileOrganizer: date extraction, path generation, copy+verify
  test_immich_api.py   -- ImmichAPI: ping, asset search, archive (sandbox)
  test_archiver.py     -- Archiver: batch archiving (sandbox)
  test_sync.py         -- end-to-end sync workflow (sandbox)
```

## Markers

| Marker | Meaning |
|---|---|
| `@pytest.mark.unit` | No external deps; fast |
| `@pytest.mark.sandbox` | Requires running Immich Docker stack |

## Sandbox

`tests/docker-compose.test.yml` spins up a minimal Immich stack (server + postgres + redis) on ephemeral ports. `start_sandbox()` waits for the API to respond, creates an admin user, and returns `{base_url, admin_api_key}`.

Sandbox tests are session-scoped — the container starts once and is torn down after all tests finish.

## Fixtures

| Fixture | Scope | Description |
|---|---|---|
| `sandbox` | session | Running Immich instance info dict |
| `admin_api` | session | `ImmichAPI` client with admin key |
| `nas_dir` | function | `tmp_path/nas` — simulated NAS root |
| `upload_dir` | function | `tmp_path/upload` — simulated Immich upload dir |
