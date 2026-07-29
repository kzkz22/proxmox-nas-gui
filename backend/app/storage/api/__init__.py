from fastapi import APIRouter

from . import disks, pools

# Bare routers; app/routes.py applies the session dependency.
routers: list[APIRouter] = [pools.router, disks.router]
