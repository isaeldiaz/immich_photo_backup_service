#!/usr/bin/env bash
# sandbox/sandbox.sh — helper for the photo_server sandbox environment
#
# Commands:
#   start            bring up sandbox Docker Compose stack and wait for Immich to be ready
#   stop             bring down (preserves data)
#   reset            stop + wipe all sandbox data (fresh slate)
#   logs             tail sandbox server logs
#   status           show running containers, last sync state, and API key status
#   get-key          create admin user + API key, save to .sandbox_api_key, print it
#   add-user-key     create an API key for a non-admin user and add it to the sync pool
#                    Usage: ./sandbox.sh add-user-key <email> <password>
#   run              run immich_backup.py with sandbox config + state file
#                    Usage: ./sandbox.sh run [sync|status|organize] [extra args...]
#
# Examples:
#   ./sandbox.sh start
#   ./sandbox.sh get-key
#   ./sandbox.sh add-user-key isael@sandbox.local password123
#   ./sandbox.sh run status
#   ./sandbox.sh run sync --dry-run
#   ./sandbox.sh run sync
#   ./sandbox.sh reset

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.sandbox.yml"
ENV_FILE="${SCRIPT_DIR}/.env.sandbox"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.sandbox.example"
CONFIG_FILE="${SCRIPT_DIR}/config.sandbox.json"
CONFIG_EXAMPLE="${SCRIPT_DIR}/config.sandbox.json.example"
STATE_FILE="${SCRIPT_DIR}/sync_state.sandbox.json"
API_KEY_FILE="${SCRIPT_DIR}/.sandbox_api_key"
USER_KEYS_FILE="${SCRIPT_DIR}/.sandbox_user_keys"

# Ensure .env.sandbox exists (copy from example if not)
if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${ENV_EXAMPLE}" ]]; then
        cp "${ENV_EXAMPLE}" "${ENV_FILE}"
        echo "Created ${ENV_FILE} from example. Edit it if you want different credentials."
    else
        echo "ERROR: ${ENV_FILE} not found and no example to copy from." >&2
        exit 1
    fi
fi

# Source env for admin credentials
# shellcheck source=/dev/null
source "${ENV_FILE}"

SANDBOX_BASE_URL="${SANDBOX_BASE_URL:-http://localhost:2284}"

# ── helpers ──────────────────────────────────────────────────────────────────

_compose() {
    docker compose -f "${COMPOSE_FILE}" "$@"
}

_wait_for_server() {
    local timeout=120
    local deadline=$(( $(date +%s) + timeout ))
    echo "Waiting for Immich sandbox at ${SANDBOX_BASE_URL} ..."
    while [[ $(date +%s) -lt ${deadline} ]]; do
        local result
        result=$(curl -sf "${SANDBOX_BASE_URL}/api/server/ping" 2>/dev/null || true)
        if echo "${result}" | grep -q '"pong"'; then
            echo "Server is up."
            return 0
        fi
        sleep 2
    done
    echo "ERROR: Immich sandbox did not respond within ${timeout}s." >&2
    return 1
}

_ensure_dirs() {
    mkdir -p \
        "${SCRIPT_DIR}/immich_upload/upload" \
        "${SCRIPT_DIR}/nas_library" \
        "${SCRIPT_DIR}/logs" \
        "${SCRIPT_DIR}/pgdata"
}

# Generate config.sandbox.json from the example template, substituting __SANDBOX_DIR__
_ensure_config() {
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        if [[ ! -f "${CONFIG_EXAMPLE}" ]]; then
            echo "ERROR: ${CONFIG_EXAMPLE} not found." >&2
            exit 1
        fi
        sed "s|__SANDBOX_DIR__|${SCRIPT_DIR}|g" "${CONFIG_EXAMPLE}" > "${CONFIG_FILE}"
        echo "Generated ${CONFIG_FILE} with paths for this machine."
    fi
}

# ── commands ─────────────────────────────────────────────────────────────────

cmd_start() {
    echo "==> Creating sandbox directories..."
    _ensure_dirs
    _ensure_config
    echo "==> Starting sandbox Docker Compose stack..."
    _compose up -d
    _wait_for_server
    echo ""
    echo "Sandbox Immich is running at ${SANDBOX_BASE_URL}"
    if [[ -f "${API_KEY_FILE}" ]]; then
        echo "API key already set. Run './sandbox.sh run status' to verify."
    else
        echo "Next step: run './sandbox.sh get-key' to create an admin user and API key."
    fi
}

cmd_stop() {
    echo "==> Stopping sandbox (data preserved)..."
    _compose down
    echo "Done. Run './sandbox.sh start' to restart."
}

cmd_reset() {
    echo "==> Stopping sandbox..."
    _compose down 2>/dev/null || true
    echo "==> Removing sandbox data directories (using container for root-owned files)..."
    docker run --rm -v "${SCRIPT_DIR}:/sandbox" alpine \
        sh -c "rm -rf /sandbox/pgdata /sandbox/immich_upload /sandbox/nas_library /sandbox/logs"
    rm -f \
        "${SCRIPT_DIR}/sync_state.sandbox.json" \
        "${SCRIPT_DIR}/.sandbox_api_key" \
        "${SCRIPT_DIR}/.sandbox_user_keys" \
        "${SCRIPT_DIR}/config.sandbox.json"
    echo "Sandbox reset complete. Run './sandbox.sh start' to begin fresh."
}

cmd_logs() {
    _compose logs -f immich-server
}

cmd_ls_upload() {
    echo "=== Immich upload library (original files) ==="
    docker exec sandbox_immich_server find /usr/src/app/upload/upload -type f | sort
}

cmd_ls_nas() {
    echo "=== NAS external library (synced files) ==="
    find "${SCRIPT_DIR}/nas_library" -type f | sort
}

cmd_status() {
    echo "=== Sandbox Docker Status ==="
    _compose ps
    echo ""
    echo "=== Sandbox State File ==="
    if [[ -f "${STATE_FILE}" ]]; then
        python3 -c "
import json
state = json.load(open('${STATE_FILE}'))
print('  Last sync:   ', state.get('last_sync_time', 'Never'))
print('  Total synced:', state.get('total_synced', 0))
"
    else
        echo "  No state file yet (run sync first)."
    fi
    echo ""
    echo "=== API Keys ==="
    if [[ -f "${API_KEY_FILE}" ]]; then
        echo "  Admin key: $(cat "${API_KEY_FILE}")"
    else
        echo "  Admin key: not set. Run './sandbox.sh get-key' first."
    fi
    if [[ -f "${USER_KEYS_FILE}" ]]; then
        local count
        count=$(python3 -c "import json; print(len(json.load(open('${USER_KEYS_FILE}'))))")
        echo "  User keys: ${count} stored in ${USER_KEYS_FILE}"
    else
        echo "  User keys: none (use './sandbox.sh add-user-key <email> <password>' to add)"
    fi
}

cmd_get_key() {
    echo "==> Creating admin user and API key..."

    # 1. Try admin sign-up (idempotent — if admin already exists, this may 400/409)
    local signup_result
    signup_result=$(curl -sf -X POST \
        "${SANDBOX_BASE_URL}/api/auth/admin-sign-up" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${SANDBOX_ADMIN_EMAIL}\",\"password\":\"${SANDBOX_ADMIN_PASSWORD}\",\"name\":\"${SANDBOX_ADMIN_NAME}\"}" \
        2>/dev/null || true)
    if [[ -n "${signup_result}" ]]; then
        echo "  Sign-up: ${signup_result}"
    else
        echo "  Sign-up: skipped (admin likely already exists)"
    fi

    # 2. Login
    local login_result
    login_result=$(curl -sf -X POST \
        "${SANDBOX_BASE_URL}/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${SANDBOX_ADMIN_EMAIL}\",\"password\":\"${SANDBOX_ADMIN_PASSWORD}\"}")
    local access_token
    access_token=$(echo "${login_result}" | python3 -c "import json,sys; print(json.load(sys.stdin)['accessToken'])")

    # 3. Create API key
    local key_result
    key_result=$(curl -sf -X POST \
        "${SANDBOX_BASE_URL}/api/api-keys" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${access_token}" \
        -d '{"name":"sandbox-key","permissions":["all"]}')
    local api_key
    api_key=$(echo "${key_result}" | python3 -c "import json,sys; print(json.load(sys.stdin)['secret'])")

    # 4. Save key
    echo "${api_key}" > "${API_KEY_FILE}"
    echo ""
    echo "API key saved to ${API_KEY_FILE}"
    echo "Key: ${api_key}"
    echo ""
    echo "You can now run: ./sandbox.sh run status"
}

cmd_add_user_key() {
    local email="${1:-}"
    local password="${2:-}"
    if [[ -z "${email}" || -z "${password}" ]]; then
        echo "Usage: ./sandbox.sh add-user-key <email> <password>" >&2
        exit 1
    fi

    echo "==> Creating API key for ${email}..."

    local login_result
    login_result=$(curl -sf -X POST \
        "${SANDBOX_BASE_URL}/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${email}\",\"password\":\"${password}\"}")
    local access_token
    access_token=$(echo "${login_result}" | python3 -c "import json,sys; print(json.load(sys.stdin)['accessToken'])")

    local key_result
    key_result=$(curl -sf -X POST \
        "${SANDBOX_BASE_URL}/api/api-keys" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${access_token}" \
        -d '{"name":"sandbox-key","permissions":["all"]}')
    local api_key
    api_key=$(echo "${key_result}" | python3 -c "import json,sys; print(json.load(sys.stdin)['secret'])")

    python3 -c "
import json, os
f = '${USER_KEYS_FILE}'
keys = json.load(open(f)) if os.path.exists(f) else []
if '${api_key}' not in keys:
    keys.append('${api_key}')
json.dump(keys, open(f, 'w'), indent=2)
"
    echo "API key for ${email} added to ${USER_KEYS_FILE}"
}

cmd_run() {
    _ensure_config
    if [[ ! -f "${API_KEY_FILE}" ]]; then
        echo "ERROR: No API key found. Run './sandbox.sh get-key' first." >&2
        exit 1
    fi
    local api_key
    api_key=$(cat "${API_KEY_FILE}")

    # Build a temporary config with api_keys array injected (admin key + any user keys)
    local tmp_config
    tmp_config=$(mktemp /tmp/config.sandbox.XXXXXX.json)
    # shellcheck disable=SC2064
    trap "rm -f '${tmp_config}'" EXIT

    python3 -c "
import json, os
cfg = json.load(open('${CONFIG_FILE}'))
keys = ['${api_key}']
user_keys_file = '${USER_KEYS_FILE}'
if os.path.exists(user_keys_file):
    keys.extend(json.load(open(user_keys_file)))
cfg['immich'].pop('api_key', None)
cfg['immich']['api_keys'] = keys
json.dump(cfg, open('${tmp_config}', 'w'), indent=2)
"

    echo "==> python3 ${REPO_ROOT}/immich_backup.py --config <sandbox_config> --state-file ${STATE_FILE} $*"
    echo ""
    python3 "${REPO_ROOT}/immich_backup.py" \
        --config "${tmp_config}" \
        --state-file "${STATE_FILE}" \
        "$@"
}

# ── dispatch ─────────────────────────────────────────────────────────────────

case "${1:-}" in
    start)        cmd_start ;;
    stop)         cmd_stop ;;
    reset)        cmd_reset ;;
    logs)         cmd_logs ;;
    status)       cmd_status ;;
    get-key)      cmd_get_key ;;
    add-user-key) shift; cmd_add_user_key "$@" ;;
    run)          shift; cmd_run "$@" ;;
    ls-upload)    cmd_ls_upload ;;
    ls-nas)       cmd_ls_nas ;;
    *)
        echo "Usage: sandbox.sh {start|stop|reset|logs|status|get-key|add-user-key|run|ls-upload|ls-nas} [args...]"
        exit 1
        ;;
esac
