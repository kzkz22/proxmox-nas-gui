from fastapi import APIRouter

from .. import service, state as state_store, system
from ..models import GlobalSettings
from .common import commit

router = APIRouter(tags=["settings"])


@router.get("/state")
def full_state():
    st = state_store.load_state()
    return {
        "settings": st.settings,
        "shares": st.shares,
        "users": {
            name: {"description": info.description, "system": system.user_exists(name)}
            for name, info in st.users.items()
        },
        "groups": {
            name: {
                "description": info.description,
                "members": system.group_members(name),
            }
            for name, info in st.groups.items()
        },
        "service": service.status(),
    }


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
