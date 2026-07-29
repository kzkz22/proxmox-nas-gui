import os
import secrets
import time
from typing import Dict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

SESSION_COOKIE = "psg_session"
SESSION_TTL = 24 * 3600

_sessions: Dict[str, dict] = {}

router = APIRouter()


def _allowed_users() -> list[str]:
    return [
        u.strip()
        for u in os.environ.get("PSG_ADMIN_USERS", "root").split(",")
        if u.strip()
    ]


def _pam_auth(username: str, password: str) -> bool:
    import pam

    return pam.pam().authenticate(username, password, service="login")


class LoginRequest(BaseModel):
    username: str
    password: str


def current_user(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    session = _sessions.get(token) if token else None
    if not session or session["expires"] < time.time():
        if token:
            _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="not authenticated")
    return session["user"]


@router.post("/login")
def login(body: LoginRequest, response: Response):
    if body.username not in _allowed_users() or not _pam_auth(
        body.username, body.password
    ):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"user": body.username, "expires": time.time() + SESSION_TTL}
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        max_age=SESSION_TTL, path="/",
    )
    return {"user": body.username}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _sessions.pop(token, None)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/session")
def session_info(request: Request):
    return {"user": current_user(request)}
