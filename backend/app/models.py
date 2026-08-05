"""The persisted state.

The Samba and the storage half define their own models; this module only
composes them into the single document written to state.json. Keeping one
model, one file and one lock is deliberate: a share pointing into a pool means
the two halves have an invariant between them, and splitting the file would
turn that into a cross-file consistency problem with no way to write both
atomically.
"""

from typing import Dict

from pydantic import BaseModel, Field

from .samba.models import GlobalSettings, GroupInfo, Share, UserInfo
from .storage.models import BindMount, DiskMount, Pool


class State(BaseModel):
    version: int = 1
    settings: GlobalSettings = Field(default_factory=GlobalSettings)
    users: Dict[str, UserInfo] = Field(default_factory=dict)
    groups: Dict[str, GroupInfo] = Field(default_factory=dict)
    shares: Dict[str, Share] = Field(default_factory=dict)
    pools: Dict[str, Pool] = Field(default_factory=dict)
    disk_mounts: Dict[str, DiskMount] = Field(default_factory=dict)
    bind_mounts: Dict[str, BindMount] = Field(default_factory=dict)
