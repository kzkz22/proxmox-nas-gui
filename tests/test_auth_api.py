"""The sign-in endpoint as the browser meets it.

PAM is stubbed rather than exercised: the suite runs unprivileged and must not
depend on the host having an account with a known password. What is asserted
here is everything around PAM - the cookie attributes, the throttling, and
what leaks without a session.
"""

import time

import pytest

from app.core import auth, loginguard
from app.core.loginguard import FREE_ATTEMPTS

GOOD = {"username": "root", "password": "correct"}
BAD = {"username": "root", "password": "wrong"}


@pytest.fixture(autouse=True)
def clean_guard():
    loginguard.reset()
    yield
    loginguard.reset()


@pytest.fixture(autouse=True)
def stub_pam(monkeypatch):
    """Accept exactly one password, so the tests can tell "rejected because
    the password was wrong" from "rejected because of the lockout"."""
    monkeypatch.setattr(
        auth, "_pam_auth", lambda user, password: password == GOOD["password"]
    )


def test_a_good_password_signs_in(client):
    response = client.post("/api/login", json=GOOD)
    assert response.status_code == 200
    assert response.json() == {"user": "root"}


def test_the_session_cookie_is_secure_httponly_and_samesite(client):
    """Asserted on the raw header: the cookie jar normalises away exactly the
    attributes that matter here."""
    cookie = client.post("/api/login", json=GOOD).headers["set-cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_a_user_outside_the_allow_list_is_rejected(client, monkeypatch):
    monkeypatch.setenv("PNAS_ADMIN_USERS", "someone-else")
    assert client.post("/api/login", json=GOOD).status_code == 401


def test_failed_attempts_within_the_allowance_stay_401(client):
    for _ in range(FREE_ATTEMPTS):
        assert client.post("/api/login", json=BAD).status_code == 401


def test_too_many_failed_attempts_lock_the_account_out(client):
    for _ in range(FREE_ATTEMPTS):
        client.post("/api/login", json=BAD)
    response = client.post("/api/login", json=BAD)
    assert response.status_code == 401  # the failure that earns the lockout
    locked = client.post("/api/login", json=BAD)
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


def test_the_lockout_also_refuses_the_correct_password(client):
    """The whole point: a lockout an attacker could end by finally guessing
    right would not be a lockout."""
    for _ in range(FREE_ATTEMPTS + 1):
        client.post("/api/login", json=BAD)
    assert client.post("/api/login", json=GOOD).status_code == 429


def test_signing_in_before_the_allowance_runs_out_clears_the_count(client):
    for _ in range(FREE_ATTEMPTS):
        client.post("/api/login", json=BAD)
    assert client.post("/api/login", json=GOOD).status_code == 200
    for _ in range(FREE_ATTEMPTS):
        assert client.post("/api/login", json=BAD).status_code == 401


def test_signing_in_drops_expired_sessions(client):
    """Nothing else ever removes a session nobody returns to, so without this
    the dict grows by one entry per sign-in for as long as the service runs."""
    auth._sessions["stale"] = {"user": "root", "expires": time.time() - 1}
    live = "still-valid"
    auth._sessions[live] = {"user": "root", "expires": time.time() + 3600}
    try:
        client.post("/api/login", json=GOOD)
        assert "stale" not in auth._sessions
        assert live in auth._sessions
    finally:
        auth._sessions.pop(live, None)


def test_the_openapi_schema_is_not_served(client):
    """It is mounted on the app rather than under the session-checked router,
    so leaving it on would publish the whole management API unauthenticated."""
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
