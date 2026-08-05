from ..models import State
from . import binds as bind_ops
from . import pools as pool_ops


def state_view(st: State) -> dict:
    """The storage half of the GET /api/state payload.

    Mount state and free space are not stored, only observed, so both are read
    from the live filesystem on every call.
    """
    return {
        "pools": {name: pool_ops.pool_info(p) for name, p in st.pools.items()},
        "disk_mounts": {
            name: {**dm.model_dump(),
                   "mounted": pool_ops.is_mounted(dm.mountpoint)}
            for name, dm in st.disk_mounts.items()
        },
        "bind_mounts": {
            name: bind_ops.bind_info(st, b) for name, b in st.bind_mounts.items()
        },
    }
