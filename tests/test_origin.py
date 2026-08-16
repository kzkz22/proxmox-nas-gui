"""The cross-origin guard on state-changing requests.

The TestClient sends no Origin of its own, so every header here is explicit -
which is also what makes the "no Origin at all" case worth asserting: it is
the one the rest of the suite exercises by accident, and the one that would
break curl if it were ever tightened.

The write used throughout is a delete of a share that does not exist. It
reaches the handler and stops at the state lookup, so an allowed request is
provably an allowed request - a 404 from the endpoint itself rather than
merely "not a 403" - and no test here touches systemctl or the filesystem.
"""

import pytest

from app.core import auth

ORIGIN = "https://testserver"
FOREIGN = "https://evil.example"
WRITE = "/api/shares/nonexistent"


@pytest.fixture(autouse=True)
def stub_pam(monkeypatch):
    monkeypatch.setattr(auth, "_pam_auth", lambda user, password: True)


def test_a_write_from_the_same_origin_is_allowed(auth_client, sandbox):
    response = auth_client.delete(WRITE, headers={"Origin": ORIGIN})
    assert response.status_code == 404


def test_a_write_from_another_origin_is_refused(auth_client, sandbox):
    response = auth_client.delete(WRITE, headers={"Origin": FOREIGN})
    assert response.status_code == 403


def test_a_write_without_an_origin_header_is_allowed(auth_client, sandbox):
    """curl, scripts and non-browser clients. An attacking page cannot
    suppress the header, so its absence is not a case CSRF can arrange."""
    assert auth_client.delete(WRITE).status_code == 404


def test_a_null_origin_is_refused(auth_client, sandbox):
    """What a sandboxed iframe or a data: URL sends. It matches no host, and
    accepting it would be the one way past this check."""
    response = auth_client.delete(WRITE, headers={"Origin": "null"})
    assert response.status_code == 403


def test_a_read_from_another_origin_is_allowed(auth_client, sandbox):
    """Reads are deliberately exempt: no GET here changes state, and the
    same-origin policy already stops the response being read."""
    response = auth_client.get("/api/state", headers={"Origin": FOREIGN})
    assert response.status_code == 200


def test_a_configured_trusted_origin_is_allowed(auth_client, sandbox, monkeypatch):
    monkeypatch.setenv("PNAS_TRUSTED_ORIGINS", f"https://other.example,{FOREIGN}")
    response = auth_client.delete(WRITE, headers={"Origin": FOREIGN})
    assert response.status_code == 404


def test_the_login_endpoint_is_guarded_too(client):
    """It sits outside the session-checked router, so it needs the guard from
    the API router above it - otherwise a cross-site page could sign the
    browser in."""
    response = client.post(
        "/api/login",
        headers={"Origin": FOREIGN},
        json={"username": "root", "password": "x"},
    )
    assert response.status_code == 403


def test_the_origin_check_runs_before_the_session_check(client, sandbox):
    """403 rather than 401: a cross-origin caller should not learn whether
    its cookie was any good."""
    response = client.delete(WRITE, headers={"Origin": FOREIGN})
    assert response.status_code == 403
