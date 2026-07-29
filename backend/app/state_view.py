"""GET /api/state - the aggregate the frontend reloads after every change.

A composition root, like models.py and routes.py: it is the one place allowed
to reach into both feature packages, so that neither has to know about the
other in order to appear in the payload.
"""

from fastapi import APIRouter

from .core import state as state_store
from .samba.state_view import state_view as samba_view
from .storage.state_view import state_view as storage_view

router = APIRouter(tags=["state"])


@router.get("/state")
def full_state():
    st = state_store.load_state()
    samba, storage = samba_view(st), storage_view(st)
    # A key added to both halves would otherwise silently lose one of them,
    # and the frontend would just render undefined.
    collision = samba.keys() & storage.keys()
    if collision:
        raise RuntimeError(f"state_view key collision: {sorted(collision)}")
    return {**samba, **storage}
