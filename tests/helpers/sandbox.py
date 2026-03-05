import time
import subprocess
import requests
from pathlib import Path

COMPOSE_FILE = Path(__file__).parent.parent / "docker-compose.test.yml"
BASE_URL = "http://localhost:2285"
ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "admin123456"
ADMIN_NAME = "Admin"

def start_sandbox() -> dict:
    """Start sandbox Immich instance, wait for healthy, create admin user.

    Returns dict with: base_url, admin_api_key, admin_user_id
    """
    # docker compose up -d
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        check=True, capture_output=True
    )

    # Poll until server responds to ping (timeout 120s)
    _wait_for_server(timeout=120)

    # Create admin user via first-time onboarding
    # POST /api/auth/admin-sign-up
    resp = requests.post(f"{BASE_URL}/api/auth/admin-sign-up", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "name": ADMIN_NAME,
    })
    resp.raise_for_status()

    # Login to get access token
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
    })
    resp.raise_for_status()
    access_token = resp.json()["accessToken"]

    # Create API key
    resp = requests.post(
        f"{BASE_URL}/api/api-keys",
        json={"name": "test-admin"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    api_key = resp.json()["secret"]

    # Get admin user ID
    resp = requests.get(
        f"{BASE_URL}/api/users/me",
        headers={"x-api-key": api_key},
    )
    resp.raise_for_status()
    admin_user_id = resp.json()["id"]

    return {
        "base_url": BASE_URL,
        "admin_api_key": api_key,
        "admin_user_id": admin_user_id,
    }

def stop_sandbox():
    """Stop and remove sandbox Immich instance with volumes."""
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        check=True, capture_output=True
    )

def _wait_for_server(timeout=120):
    """Poll GET /api/server/ping until {"res": "pong"}."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{BASE_URL}/api/server/ping", timeout=2)
            if resp.status_code == 200 and resp.json().get("res") == "pong":
                return
        except (requests.ConnectionError, requests.Timeout):
            pass
        time.sleep(1)
    raise TimeoutError(f"Immich sandbox did not start within {timeout}s")
