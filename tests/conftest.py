"""Shared fixtures for the API-level tests.

Every path the application writes to is already environment-overridable, so a
test client can be pointed entirely at a tmp_path and never touch the host's
/etc. Authentication is bypassed by injecting a session token directly rather
than going through PAM.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.core import auth
from app.main import app

# Kept in sync with the env vars documented in the README.
PATH_ENV_VARS = (
    "PSG_STATE_DIR",
    "PSG_SMB_CONF",
    "PSG_GEN_CONF",
    "PSG_FSTAB",
    "PSG_SYSTEMD_DIR",
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every file the application writes into tmp_path."""
    paths = {
        "PSG_STATE_DIR": tmp_path / "state",
        "PSG_SMB_CONF": tmp_path / "samba" / "smb.conf",
        "PSG_GEN_CONF": tmp_path / "samba" / "generated.conf",
        "PSG_FSTAB": tmp_path / "fstab",
        "PSG_SYSTEMD_DIR": tmp_path / "systemd",
    }
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))
    paths["PSG_STATE_DIR"].mkdir(parents=True)
    paths["PSG_SMB_CONF"].parent.mkdir(parents=True)
    paths["PSG_SYSTEMD_DIR"].mkdir(parents=True)
    paths["PSG_SMB_CONF"].write_text("[global]\n")
    return paths


@pytest.fixture
def client():
    """Unauthenticated client."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    """Client carrying a valid session, without involving PAM."""
    token = "test-session-token"
    auth._sessions[token] = {"user": "root", "expires": time.time() + 3600}
    client.cookies.set(auth.SESSION_COOKIE, token)
    yield client
    auth._sessions.pop(token, None)
