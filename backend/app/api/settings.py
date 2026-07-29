from fastapi import APIRouter

from .. import pools as pool_ops, service, state as state_store
from ..models import GlobalSettings
from ..samba import accounts
from .common import commit

router = APIRouter(tags=["settings"])


@router.get("/state")
def full_state():
    st = state_store.load_state()
    return {
        "settings": st.settings,
        "shares": st.shares,
        "users": {
            name: {"description": info.description, "system": accounts.user_exists(name)}
            for name, info in st.users.items()
        },
        "groups": {
            name: {
                "description": info.description,
                "members": accounts.group_members(name),
            }
            for name, info in st.groups.items()
        },
        "pools": {name: pool_ops.pool_info(p) for name, p in st.pools.items()},
        "disk_mounts": {
            name: {**dm.model_dump(),
                   "mounted": pool_ops.is_mounted(dm.mountpoint)}
            for name, dm in st.disk_mounts.items()
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
