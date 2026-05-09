# User Management

## Adding a new user to the backup workflow

### Step 1 — Create the user in Immich

Use the Immich web UI: **Administration → Users → Create User**.

Alternatively, use the admin API key programmatically:

```python
from lib.immich_api import ImmichAPI
api = ImmichAPI("http://your-immich-host:2283", api_key="<admin-key>")
api.create_user(email="user@example.com", password="...", name="User Name")
```

### Step 2 — Extract the user's API key

The user can generate their own key via the Immich web UI: **Account Settings → API Keys → New API Key**.

If you have their credentials, you can do it programmatically:

```python
access_token = api.login("user@example.com", "their-password")
user_api_key = api.create_api_key(access_token, name="backup-script")
print(user_api_key)
```

`ImmichAPI.login()` returns a bearer token; `ImmichAPI.create_api_key()` exchanges it for a permanent API key scoped to that user.

### Step 3 — Add the key to `config.json`

Append the new key to the `immich.api_keys` list:

```json
{
  "immich": {
    "api_url": "http://your-immich-host:2283",
    "api_keys": [
      "existing-user-key",
      "new-user-key"
    ]
  }
}
```

The legacy single-string `api_key` field is still accepted as a fallback, but `api_keys` is the canonical form for multi-user setups.

### What happens automatically

- The sync loop iterates over each key independently.
- Each user's assets are fetched, copied to NAS under `{base_dir}/{user_name}/{year}/{MM-Month}/`, and archived **using that user's own API key** — Immich rejects archive requests for assets the caller does not own, so there is no admin override.
- `sync_state.json` is shared safely across users because Immich asset IDs are globally unique.

No other changes are needed — the workflow is fully driven by the `api_keys` list.
