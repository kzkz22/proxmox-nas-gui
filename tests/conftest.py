"""Shared fixtures for the API-level tests.

Every path the application writes to is already environment-overridable, so a
test client can be pointed entirely at a tmp_path and never touch the host's
/etc. Authentication is bypassed by injecting a session token directly rather
than going through PAM.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from app.core import auth, fsops
from app.main import app

# Kept in sync with the env vars documented in the README.
PATH_ENV_VARS = (
    "PNAS_STATE_DIR",
    "PNAS_SMB_CONF",
    "PNAS_GEN_CONF",
    "PNAS_FSTAB",
    "PNAS_SYSTEMD_DIR",
    "PNAS_LOG_DB",
    "PNAS_SMARTD_CONF",
    "PNAS_HD_IDLE_CONF",
    "PNAS_PVE_STORAGE",
    "PNAS_UPDATEDB_CONF",
    "PNAS_CRON_DIR",
)


@pytest.fixture(autouse=True)
def no_monitor(monkeypatch):
    """Keep the background loop out of the tests.

    TestClient's context manager runs the app's lifespan, so without this
    every API test would start the monitor and it would shell out to lsblk
    and hdparm against the machine running the suite.
    """
    monkeypatch.setenv("PNAS_DISABLE_MONITOR", "1")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every file the application writes into tmp_path."""
    paths = {
        "PNAS_STATE_DIR": tmp_path / "state",
        "PNAS_SMB_CONF": tmp_path / "samba" / "smb.conf",
        "PNAS_GEN_CONF": tmp_path / "samba" / "generated.conf",
        "PNAS_FSTAB": tmp_path / "fstab",
        "PNAS_SYSTEMD_DIR": tmp_path / "systemd",
        "PNAS_LOG_DB": tmp_path / "log" / "disk-events.db",
        "PNAS_SMARTD_CONF": tmp_path / "smartd.conf",
        "PNAS_HD_IDLE_CONF": tmp_path / "hd-idle",
        "PNAS_PVE_STORAGE": tmp_path / "storage.cfg",
        "PNAS_UPDATEDB_CONF": tmp_path / "updatedb.conf",
        "PNAS_CRON_DIR": tmp_path / "cron.d",
    }
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))
    paths["PNAS_STATE_DIR"].mkdir(parents=True)
    paths["PNAS_SMB_CONF"].parent.mkdir(parents=True)
    paths["PNAS_SYSTEMD_DIR"].mkdir(parents=True)
    paths["PNAS_SMB_CONF"].write_text("[global]\n")
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


@pytest.fixture
def stub_chown(monkeypatch):
    """Replace the chown half of apply_share_perms with a recorder.

    Ownership changes to a *different* user require root - the real app runs
    as root, but the test runner does not, so a live chown would fail here
    for a reason that has nothing to do with what these tests check. chmod on
    a file the test process already owns needs no such privilege, so that
    half still runs for real.
    """
    calls = []
    monkeypatch.setattr(
        fsops.shutil, "chown",
        lambda path, user=None, group=None: calls.append((str(path), user, group)),
    )
    return calls


@pytest.fixture
def stub_nobody(monkeypatch):
    """Make "nobody:nogroup" resolve to the test process's own uid/gid.

    diagnostics._wrong_perms() decides "correct ownership" by comparing a
    path's uid/gid against pwd.getpwnam("nobody")/grp.getgrnam("nogroup").
    Without root, a test can never chown a directory it creates to the real
    nobody account, so a directory this test owns would always read as
    "wrong" - this patches the target identity down to whoever is running the
    suite instead, so a plain 0o777-chmodded directory reads as correct.
    """
    from app import diagnostics as diag

    pwd_result = type("PwEnt", (), {"pw_uid": os.getuid()})()
    grp_result = type("GrEnt", (), {"gr_gid": os.getgid()})()
    monkeypatch.setattr(diag.pwd, "getpwnam", lambda name: pwd_result)
    monkeypatch.setattr(diag.grp, "getgrnam", lambda name: grp_result)
