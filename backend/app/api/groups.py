from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state as state_store, system
from ..models import ACCOUNT_NAME_RE, Access, GroupInfo
from .common import commit
from .users import _apply_access

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupCreate(BaseModel):
    name: str
    description: str = ""
    members: List[str] = []


class GroupUpdate(BaseModel):
    description: Optional[str] = None
    members: Optional[List[str]] = None
    access: Optional[Dict[str, Access]] = None


def _check_members(st, members: List[str]) -> None:
    for member in members:
        if member not in st.users:
            raise HTTPException(404, f"no such user: {member}")


@router.get("")
def list_groups():
    st = state_store.load_state()
    return {
        "groups": {
            name: {
                "description": info.description,
                "members": system.group_members(name),
            }
            for name, info in st.groups.items()
        }
    }


@router.post("")
def create_group(body: GroupCreate):
    if not ACCOUNT_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid group name")
    with state_store.lock:
        st = state_store.load_state()
        if body.name in st.groups:
            raise HTTPException(409, "group already exists")
        if system.group_exists(body.name):
            raise HTTPException(409, "a system group with this name already exists")
        _check_members(st, body.members)
        system.create_group(body.name)
        system.set_group_members(body.name, body.members)
        st.groups[body.name] = GroupInfo(description=body.description)
        return commit(st)


@router.put("/{name}")
def update_group(name: str, body: GroupUpdate):
    with state_store.lock:
        st = state_store.load_state()
        if name not in st.groups:
            raise HTTPException(404, "no such group")
        if body.members is not None:
            _check_members(st, body.members)
            system.set_group_members(name, body.members)
        if body.description is not None:
            st.groups[name].description = body.description
        if body.access is not None:
            _apply_access(st, "group", name, body.access)
        return commit(st)


@router.delete("/{name}")
def delete_group(name: str):
    with state_store.lock:
        st = state_store.load_state()
        if name not in st.groups:
            raise HTTPException(404, "no such group")
        system.delete_group(name)
        del st.groups[name]
        for share in st.shares.values():
            share.group_access.pop(name, None)
        return commit(st)
