import logging
import os
import secrets
import time
from typing import Dict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import loginguard

SESSION_COOKIE = "pnas_session"
SESSION_TTL = 24 * 3600

_sessions: Dict[str, dict] = {}

log = logging.getLogger(__name__)

router = APIRouter()


def _allowed_users() -> list[str]:
    return [
        u.strip()
        for u in os.environ.get("PNAS_ADMIN_USERS", "root").split(",")
        if u.strip()
    ]


def _pam_auth(username: str, password: str) -> bool:
    import pam

    return pam.pam().authenticate(username, password, service="login")


class LoginRequest(BaseModel):
    username: str
    password: str


def _purge_expired_sessions() -> None:
    """Drop the sessions that have timed out.

    current_user() only removes the one token it was handed, so a session
    nobody ever comes back to would sit in the dict until the service
    restarts. Sign-in is the natural place to sweep: it is the only thing that
    grows the dict, and it is rare enough that walking it costs nothing.
    """
    now = time.time()
    for token in [t for t, s in _sessions.items() if s["expires"] < now]:
        _sessions.pop(token, None)


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def current_user(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    session = _sessions.get(token) if token else None
    if not session or session["expires"] < time.time():
        if token:
            _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="not authenticated")
    return session["user"]


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    """Authenticate against PAM and hand out a session cookie.

    The throttling check runs before PAM rather than after it, so a locked-out
    caller is turned away even when the password is right. That is the point:
    a lockout an attacker could end by finally guessing correctly would not be
    one. It costs the administrator the wait, which is why the free allowance
    is generous and the cap is 15 minutes.
    """
    peer = _peer(request)
    wait = loginguard.retry_after(peer, body.username)
    if wait:
        log.warning(
            "login refused for %r from %s: locked out for another %ss",
            body.username, peer, wait,
        )
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts, try again in {wait} seconds",
            headers={"Retry-After": str(wait)},
        )
    if body.username not in _allowed_users() or not _pam_auth(
        body.username, body.password
    ):
        lockout = loginguard.record_failure(peer, body.username)
        # Logged rather than only counted: this is the line an operator greps
        # for in the journal, and the one fail2ban would match on.
        log.warning(
            "failed login for %r from %s%s",
            body.username, peer,
            f" (locked out for {lockout}s)" if lockout else "",
        )
        raise HTTPException(status_code=401, detail="invalid credentials")
    loginguard.record_success(peer, body.username)
    _purge_expired_sessions()
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"user": body.username, "expires": time.time() + SESSION_TTL}
    response.set_cookie(
        # secure=True unconditionally: the unit serves TLS and nothing else,
        # so there is no deployment where sending this cookie over plain HTTP
        # would be wanted rather than a misconfiguration to be caught early.
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=True,
        max_age=SESSION_TTL, path="/",
    )
    log.info("login for %r from %s", body.username, peer)
    return {"user": body.username}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _sessions.pop(token, None)
    # The attributes are repeated so the expiring cookie is the same cookie
    # the browser was given, rather than a second one it stores alongside it.
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=True
    )
    return {"ok": True}


@router.get("/session")
def session_info(request: Request):
    return {"user": current_user(request)}
