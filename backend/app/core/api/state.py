"""The aggregate state endpoint the frontend polls after every change."""

from fastapi import APIRouter

from .. import state as state_store
from ...samba import accounts, service
from ...storage import pools as pool_ops

router = APIRouter(tags=["state"])


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
