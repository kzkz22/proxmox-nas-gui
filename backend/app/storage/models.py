import re
from enum import Enum
from typing import List

from pydantic import BaseModel, Field, field_validator, model_validator

POOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,30}$")
MINFREESPACE_RE = re.compile(r"^\d+[KMGT]?$")
CREATE_POLICIES = ("mfs", "epmfs", "ff", "pfrd", "rand", "lus", "lfs", "eplfs", "epff")
# fstab is whitespace-delimited, so paths and options written there must not
# contain spaces; newlines would inject extra fstab entries.
UNSAFE_FSTAB_CHARS = re.compile(r"[\s\x00,]")


def _fstab_safe_abs_path(v: str) -> str:
    if not v.startswith("/") or UNSAFE_FSTAB_CHARS.search(v) or ":" in v:
        raise ValueError(f"invalid path for fstab: {v}")
    return v.rstrip("/") or "/"


class BranchMode(str, Enum):
    RW = "RW"
    RO = "RO"
    NC = "NC"


class Branch(BaseModel):
    path: str
    mode: BranchMode = BranchMode.RW

    @field_validator("path")
    @classmethod
    def _valid_path(cls, v: str) -> str:
        return _fstab_safe_abs_path(v)


class Pool(BaseModel):
    name: str
    mountpoint: str
    branches: List[Branch] = Field(min_length=1)
    create_policy: str = "mfs"
    minfreespace: str = "4G"
    moveonenospc: bool = True
    extra_options: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not POOL_NAME_RE.match(v):
            raise ValueError("invalid pool name")
        return v

    @field_validator("mountpoint")
    @classmethod
    def _valid_mountpoint(cls, v: str) -> str:
        v = _fstab_safe_abs_path(v)
        if v == "/":
            raise ValueError("mountpoint cannot be /")
        return v

    @field_validator("create_policy")
    @classmethod
    def _valid_policy(cls, v: str) -> str:
        if v not in CREATE_POLICIES:
            raise ValueError("invalid create policy")
        return v

    @field_validator("minfreespace")
    @classmethod
    def _valid_minfree(cls, v: str) -> str:
        if not MINFREESPACE_RE.match(v):
            raise ValueError("invalid minfreespace")
        return v

    @field_validator("extra_options")
    @classmethod
    def _valid_extra(cls, v: str) -> str:
        v = v.strip().strip(",")
        if not v:
            return ""
        for opt in v.split(","):
            if not opt or re.search(r"\s", opt):
                raise ValueError("invalid extra options")
            key = opt.split("=", 1)[0]
            if key in ("branches", "fsname", "nofail"):
                raise ValueError(f"option managed by the GUI: {key}")
        return v

    @model_validator(mode="after")
    def _no_nesting(self) -> "Pool":
        mp = self.mountpoint + "/"
        seen = set()
        for b in self.branches:
            bp = b.path + "/"
            if bp in seen:
                raise ValueError(f"duplicate branch: {b.path}")
            seen.add(bp)
            if bp.startswith(mp) or mp.startswith(bp):
                raise ValueError(
                    f"branch and mountpoint may not contain each other: {b.path}"
                )
        return self


class DiskMount(BaseModel):
    uuid: str
    fstype: str
    mountpoint: str

    @field_validator("uuid")
    @classmethod
    def _valid_uuid(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9-]{4,40}$", v):
            raise ValueError("invalid uuid")
        return v

    @field_validator("fstype")
    @classmethod
    def _valid_fstype(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9]{2,16}$", v):
            raise ValueError("invalid fstype")
        return v

    @field_validator("mountpoint")
    @classmethod
    def _valid_mountpoint(cls, v: str) -> str:
        return _fstab_safe_abs_path(v)
