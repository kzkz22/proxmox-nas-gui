"""Composition root for the HTTP API.

Every feature package exports bare routers; the single authenticated router
is built here so that adding a package cannot accidentally publish an
endpoint without a session check. Only the login endpoints sit outside it.
"""

from fastapi import APIRouter, Depends

from .core.api import routers as core_routers
from .core.auth import current_user, router as auth_router
from .samba.api import routers as samba_routers
from .storage.api import routers as storage_routers

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)

protected = APIRouter(dependencies=[Depends(current_user)])
for router in (*core_routers, *samba_routers, *storage_routers):
    protected.include_router(router)
api_router.include_router(protected)
