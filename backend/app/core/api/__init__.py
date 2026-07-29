from fastapi import APIRouter

from . import fs

# Routers are exported bare, without an auth dependency: app/routes.py wraps
# every package's routers in the one authenticated router, so no package can
# publish an unprotected endpoint by forgetting it.
routers: list[APIRouter] = [fs.router]
