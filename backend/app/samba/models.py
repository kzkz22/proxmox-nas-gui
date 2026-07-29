import re
from enum import Enum
from typing import Dict

from pydantic import BaseModel, Field, field_validator

SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,78}[A-Za-z0-9._-]$|^[A-Za-z0-9]$")
ACCOUNT_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
# Values are written into smb.conf as "key = value" lines; newlines would allow
# injecting arbitrary directives and '%' triggers Samba variable substitution.
UNSAFE_CONF_CHARS = re.compile(r"[\r\n\x00%\[\]]")


class ExportMode(str, Enum):
    NO = "no"
    YES = "yes"
    HIDDEN = "hidden"


class Security(str, Enum):
    PUBLIC = "public"
    SECURE = "secure"
    PRIVATE = "private"


class Access(str, Enum):
    NONE = "no"
    READ = "read"
    WRITE = "write"


class Share(BaseModel):
    name: str
    path: str
    comment: str = ""
    export: ExportMode = ExportMode.YES
    security: Security = Security.PUBLIC
    recycle: bool = False
    user_access: Dict[str, Access] = Field(default_factory=dict)
    group_access: Dict[str, Access] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not SHARE_NAME_RE.match(v) or v.lower() in ("global", "homes", "printers"):
            raise ValueError("invalid share name")
        return v

    @field_validator("path")
    @classmethod
    def _valid_path(cls, v: str) -> str:
        if not v.startswith("/") or UNSAFE_CONF_CHARS.search(v) or v != v.strip():
            raise ValueError("invalid share path")
        return v

    @field_validator("comment")
    @classmethod
    def _valid_comment(cls, v: str) -> str:
        if UNSAFE_CONF_CHARS.search(v):
            raise ValueError("invalid characters in comment")
        return v

    @field_validator("user_access", "group_access")
    @classmethod
    def _valid_accounts(cls, v: Dict[str, Access]) -> Dict[str, Access]:
        for name in v:
            if not ACCOUNT_NAME_RE.match(name):
                raise ValueError(f"invalid account name: {name}")
        return v


class UserInfo(BaseModel):
    description: str = ""


class GroupInfo(BaseModel):
    description: str = ""


class GlobalSettings(BaseModel):
    workgroup: str = "WORKGROUP"
    server_string: str = "Proxmox Samba GUI"
    netbios_name: str = ""
    min_protocol: str = "SMB2"

    @field_validator("workgroup", "server_string", "netbios_name")
    @classmethod
    def _safe(cls, v: str) -> str:
        if UNSAFE_CONF_CHARS.search(v):
            raise ValueError("invalid characters")
        return v.strip()

    @field_validator("min_protocol")
    @classmethod
    def _proto(cls, v: str) -> str:
        if v not in ("NT1", "SMB2", "SMB2_10", "SMB3", "SMB3_11"):
            raise ValueError("invalid protocol")
        return v
