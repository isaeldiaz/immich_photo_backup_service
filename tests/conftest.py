import sys
import pytest
from pathlib import Path

# Add photo_server to path so `lib.` imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.helpers.sandbox import start_sandbox, stop_sandbox
from lib.immich_api import ImmichAPI


# ---------------------------------------------------------------------------
# Pytest markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line("markers", "sandbox: requires a running Immich sandbox (slow)")
    config.addinivalue_line("markers", "unit: fast unit tests with no external deps")


# ---------------------------------------------------------------------------
# Sandbox fixtures (session-scoped, only created when a test needs them)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sandbox():
    """Start sandbox Immich, yield connection info, tear down."""
    info = start_sandbox()
    yield info
    stop_sandbox()


@pytest.fixture(scope="session")
def admin_api(sandbox):
    """Admin API client for the sandbox."""
    return ImmichAPI(sandbox["base_url"], sandbox["admin_api_key"])


# ---------------------------------------------------------------------------
# Lightweight directory fixtures (function-scoped)
# ---------------------------------------------------------------------------

@pytest.fixture
def nas_dir(tmp_path):
    """Temporary directory simulating the NAS."""
    d = tmp_path / "nas"
    d.mkdir()
    return d


@pytest.fixture
def upload_dir(tmp_path):
    """Temporary directory simulating Immich's upload volume."""
    d = tmp_path / "upload"
    d.mkdir()
    return d
