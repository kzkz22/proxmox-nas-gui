from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...core import fsops, state as state_store
from .. import service
from ..models import Share
from .common import commit, normalize_access

router = APIRouter(prefix="/shares", tags=["shares"])


def _prepare(share: Share) -> Share:
    share.user_access = normalize_access(share.user_access)
    share.group_access = normalize_access(share.group_access)
    if not Path(share.path).is_dir():
        raise HTTPException(400, f"path is not a directory: {share.path}")
    return share


@router.get("")
def list_shares():
    st = state_store.load_state()
    return {"shares": st.shares, "service": service.status()}


@router.post("")
def create_share(share: Share):
    share = _prepare(share)
    with state_store.lock:
        st = state_store.load_state()
        if share.name in st.shares:
            raise HTTPException(409, "share already exists")
        for known in st.shares.values():
            if known.name.lower() == share.name.lower():
                raise HTTPException(409, "share name differs only in case")
        fsops.apply_share_perms(share.path)
        st.shares[share.name] = share
        return commit(st)


@router.put("/{name}")
def update_share(name: str, share: Share):
    if share.name != name:
        raise HTTPException(400, "share name cannot be changed")
    share = _prepare(share)
    with state_store.lock:
        st = state_store.load_state()
        if name not in st.shares:
            raise HTTPException(404, "no such share")
        fsops.apply_share_perms(share.path)
        st.shares[name] = share
        return commit(st)


@router.delete("/{name}")
def delete_share(name: str):
    with state_store.lock:
        st = state_store.load_state()
        if name not in st.shares:
            raise HTTPException(404, "no such share")
        del st.shares[name]
        return commit(st)


@router.get("/{name}/recycle")
def recycle_info(name: str):
    st = state_store.load_state()
    if name not in st.shares:
        raise HTTPException(404, "no such share")
    return fsops.recycle_usage(st.shares[name].path)


@router.post("/{name}/recycle/empty")
def recycle_empty(name: str):
    st = state_store.load_state()
    if name not in st.shares:
        raise HTTPException(404, "no such share")
    fsops.empty_recycle(st.shares[name].path)
    return {"ok": True}
