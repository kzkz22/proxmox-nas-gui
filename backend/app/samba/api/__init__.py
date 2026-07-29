from fastapi import APIRouter

from . import groups, settings, shares, users

# Bare routers; app/routes.py applies the session dependency.
routers: list[APIRouter] = [
    shares.router,
    users.router,
    groups.router,
    settings.router,
]
