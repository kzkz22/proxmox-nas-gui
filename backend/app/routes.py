"""Composition root for the HTTP API.

Every feature package exports bare routers; the single authenticated router
is built here so that adding a package cannot accidentally publish an
endpoint without a session check. Only the login endpoints sit outside it.

The origin check sits one level further out, on the API router itself, so it
also covers those login endpoints - a cross-site page should not be able to
sign the browser in either - and so it runs before the session check rather
than after it.
"""

from fastapi import APIRouter, Depends

from .core.api import routers as core_routers
from .core.auth import current_user, router as auth_router
from .core.origin import same_origin
from .diagnostics_api import router as diagnostics_router
from .samba.api import routers as samba_routers
from .state_view import router as state_router
from .storage.api import routers as storage_routers

api_router = APIRouter(prefix="/api", dependencies=[Depends(same_origin)])
api_router.include_router(auth_router)

protected = APIRouter(dependencies=[Depends(current_user)])
for router in (state_router, diagnostics_router, *core_routers, *samba_routers, *storage_routers):
    protected.include_router(router)
api_router.include_router(protected)
