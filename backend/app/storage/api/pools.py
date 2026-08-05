from fastapi import APIRouter, HTTPException

from ...core import state as state_store
from ...core.deps import blockers_for_path
from .. import binds, pools
from ..models import Pool

router = APIRouter(prefix="/pools", tags=["pools"])


def _bind_blockers(st, mountpoint: str) -> None:
    """Refuse to pull a pool out from under a bind mount that presents it.

    blockers_for_path already follows binds through to the shares behind
    them, but a bind with no share on it yet is still a configured mapping
    the user would rather be told about than silently see break.
    """
    using = binds.binds_using_path(st, mountpoint)
    if using:
        raise HTTPException(
            409, "bind mounts take their source from this pool: " + ", ".join(using)
        )


def _save_and_apply(st, pool: Pool, was_mounted: bool) -> dict:
    pools.write_pool_unit(pool)
    state_store.save_state(st)
    warning = None
    if was_mounted:
        warning = pools.runtime_update(pool)
    else:
        try:
            pools.mount_pool(pool)
        except Exception as exc:
            warning = f"pool saved, but mount failed: {exc}"
    return {"ok": True, "warning": warning}


@router.get("")
def list_pools():
    st = state_store.load_state()
    return {"pools": {name: pools.pool_info(p) for name, p in st.pools.items()}}


@router.post("")
def create_pool(pool: Pool):
    with state_store.lock:
        st = state_store.load_state()
        if pool.name in st.pools:
            raise HTTPException(409, "pool already exists")
        _check_conflicts(st, pool)
        st.pools[pool.name] = pool
        return _save_and_apply(st, pool, was_mounted=False)


@router.put("/{name}")
def update_pool(name: str, pool: Pool):
    if pool.name != name:
        raise HTTPException(400, "pool name cannot be changed")
    with state_store.lock:
        st = state_store.load_state()
        old = st.pools.get(name)
        if not old:
            raise HTTPException(404, "no such pool")
        _check_conflicts(st, pool, ignore=name)
        if pool.mountpoint != old.mountpoint:
            deps = blockers_for_path(st, old.mountpoint)
            if deps:
                raise HTTPException(
                    409, "shares depend on this pool's mountpoint: " + ", ".join(deps)
                )
            pools.unmount_pool(old)
        was_mounted = pools.is_mounted(pool.mountpoint)
        st.pools[name] = pool
        return _save_and_apply(st, pool, was_mounted)


@router.delete("/{name}")
def delete_pool(name: str):
    with state_store.lock:
        st = state_store.load_state()
        pool = st.pools.get(name)
        if not pool:
            raise HTTPException(404, "no such pool")
        deps = blockers_for_path(st, pool.mountpoint)
        if deps:
            raise HTTPException(
                409, "shares depend on this pool: " + ", ".join(deps)
            )
        _bind_blockers(st, pool.mountpoint)
        pools.unmount_pool(pool)
        pools.remove_pool_unit(name)
        del st.pools[name]
        state_store.save_state(st)
        return {"ok": True}


@router.post("/{name}/mount")
def mount_pool(name: str):
    st = state_store.load_state()
    pool = st.pools.get(name)
    if not pool:
        raise HTTPException(404, "no such pool")
    pools.mount_pool(pool)
    return {"ok": True}


@router.post("/{name}/unmount")
def unmount_pool(name: str):
    st = state_store.load_state()
    pool = st.pools.get(name)
    if not pool:
        raise HTTPException(404, "no such pool")
    deps = blockers_for_path(st, pool.mountpoint)
    if deps:
        raise HTTPException(409, "shares depend on this pool: " + ", ".join(deps))
    _bind_blockers(st, pool.mountpoint)
    pools.unmount_pool(pool)
    return {"ok": True}


def _check_conflicts(st, pool: Pool, ignore: str | None = None) -> None:
    for other_name, other in st.pools.items():
        if other_name == ignore:
            continue
        if other.mountpoint == pool.mountpoint:
            raise HTTPException(409, f"mountpoint already used by pool {other_name}")
        if any(b.path == pool.mountpoint for b in other.branches):
            raise HTTPException(
                409, f"mountpoint is a branch of pool {other_name}"
            )
        for b in pool.branches:
            if b.path == other.mountpoint:
                raise HTTPException(
                    409, f"branch {b.path} is the mountpoint of pool {other_name}"
                )
