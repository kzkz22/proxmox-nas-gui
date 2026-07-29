from fastapi import APIRouter

from ...core import state as state_store
from .. import service
from ..models import GlobalSettings
from .common import commit

router = APIRouter(tags=["settings"])


@router.put("/settings")
def update_settings(settings: GlobalSettings):
    with state_store.lock:
        st = state_store.load_state()
        st.settings = settings
        return commit(st)


@router.post("/service/restart")
def restart():
    service.restart_samba()
    return {"ok": True, "service": service.status()}
