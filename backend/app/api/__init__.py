from fastapi import APIRouter, Depends

from ..auth import current_user, router as auth_router
from . import fs, groups, settings, shares, users

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)

protected = APIRouter(dependencies=[Depends(current_user)])
protected.include_router(shares.router)
protected.include_router(users.router)
protected.include_router(groups.router)
protected.include_router(settings.router)
protected.include_router(fs.router)
api_router.include_router(protected)
