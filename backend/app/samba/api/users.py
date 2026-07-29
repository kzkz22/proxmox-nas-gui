from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core import state as state_store
from .. import accounts
from ..models import ACCOUNT_NAME_RE, Access, UserInfo
from .common import commit

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    name: str
    password: str
    description: str = ""


class UserUpdate(BaseModel):
    description: Optional[str] = None
    password: Optional[str] = None
    access: Optional[Dict[str, Access]] = None


def _apply_access(st, kind: str, account: str, access: Dict[str, Access]) -> None:
    for share_name, level in access.items():
        share = st.shares.get(share_name)
        if not share:
            raise HTTPException(404, f"no such share: {share_name}")
        mapping = share.user_access if kind == "user" else share.group_access
        if level == Access.NONE:
            mapping.pop(account, None)
        else:
            mapping[account] = level


@router.get("")
def list_users():
    st = state_store.load_state()
    return {
        "users": {
            name: {"description": info.description, "system": accounts.user_exists(name)}
            for name, info in st.users.items()
        }
    }


@router.post("")
def create_user(body: UserCreate):
    if not ACCOUNT_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid user name")
    if not body.password:
        raise HTTPException(400, "password is required")
    with state_store.lock:
        st = state_store.load_state()
        if body.name in st.users:
            raise HTTPException(409, "user already exists")
        if accounts.user_exists(body.name):
            raise HTTPException(409, "a system user with this name already exists")
        accounts.create_user(body.name, body.password, body.description)
        st.users[body.name] = UserInfo(description=body.description)
        return commit(st)


@router.put("/{name}")
def update_user(name: str, body: UserUpdate):
    with state_store.lock:
        st = state_store.load_state()
        if name not in st.users:
            raise HTTPException(404, "no such user")
        if body.password:
            accounts.set_smb_password(name, body.password)
        if body.description is not None:
            st.users[name].description = body.description
        if body.access is not None:
            _apply_access(st, "user", name, body.access)
        return commit(st)


@router.delete("/{name}")
def delete_user(name: str):
    with state_store.lock:
        st = state_store.load_state()
        if name not in st.users:
            raise HTTPException(404, "no such user")
        accounts.delete_user(name)
        del st.users[name]
        for share in st.shares.values():
            share.user_access.pop(name, None)
        return commit(st)
