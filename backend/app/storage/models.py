import re
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

POOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,30}$")
# The idle times the GUI offers, in seconds, plus 0 for "never sleep". A
# closed set rather than a free number: these are the choices the dropdown
# shows, and anything else arriving over the API is a client bug.
IDLE_CHOICES = (0, 900, 1800, 2700, 3600, 7200, 10800, 14400, 18000, 21600)
# A device that does not spin runs hotter as a matter of course: 60 C is
# unremarkable for an NVMe and alarming for a platter drive. Rather than a
# second pair of settings, the one pair is shifted for those.
TEMP_SSD_OFFSET = 20
MINFREESPACE_RE = re.compile(r"^\d+[KMGT]?$")
CREATE_POLICIES = ("mfs", "epmfs", "ff", "pfrd", "rand", "lus", "lfs", "eplfs", "epff")
# mergerfs cache.files modes. "off" forces direct_io: every read and write
# crosses into the mergerfs process one call at a time, the kernel page cache
# is bypassed entirely, and shared mmap stops working (FUSE cannot do it
# without page caching), which breaks any application that memory-maps its
# files - qBittorrent/libtorrent 2.x and sqlite3-based apps among them. It is
# the right choice only when RAM is tight enough that double caching hurts
# more than the lost throughput does, so it is offered but not the default.
CACHE_FILES_MODES = ("off", "partial", "full", "auto-full", "per-process")
# mergerfs passthrough.io modes. Passthrough hands the file descriptor to the
# kernel so reads and writes go straight to the branch filesystem, skipping
# the mergerfs process entirely - near native speed, and the only setting that
# removes the FUSE round trip rather than amortising it. It needs kernel 6.9
# and mergerfs 2.41 (see mergerfs_env.py), and it disables moveonenospc,
# because mergerfs no longer sees the writes and so cannot see them fail.
PASSTHROUGH_MODES = ("off", "ro", "wo", "rw")
# fstab is whitespace-delimited, so paths and options written there must not
# contain spaces; newlines would inject extra fstab entries.
UNSAFE_FSTAB_CHARS = re.compile(r"[\s\x00,]")
# A path that ends up inside a systemd unit's ExecStart= goes through the unit
# file parser first: % starts a specifier expansion, and quotes/backslashes are
# unescaped before the command line is split. None of that is wanted in a
# mountpoint, so they are rejected rather than escaped.
UNSAFE_UNIT_CHARS = re.compile(r"""[%"'\\$;`]""")


def _fstab_safe_abs_path(v: str) -> str:
    if not v.startswith("/") or UNSAFE_FSTAB_CHARS.search(v) or ":" in v:
        raise ValueError(f"invalid path for fstab: {v}")
    return v.rstrip("/") or "/"


def _unit_safe_abs_path(v: str) -> str:
    v = _fstab_safe_abs_path(v)
    if UNSAFE_UNIT_CHARS.search(v):
        raise ValueError(f"invalid path for a systemd unit: {v}")
    if v == "/":
        raise ValueError("path cannot be /")
    return v


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
    cache_files: str = "auto-full"
    cache_writeback: bool = False
    dropcacheonclose: bool = False
    passthrough: str = "off"
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

    @field_validator("cache_files")
    @classmethod
    def _valid_cache_files(cls, v: str) -> str:
        if v not in CACHE_FILES_MODES:
            raise ValueError("invalid cache.files mode")
        return v

    @field_validator("passthrough")
    @classmethod
    def _valid_passthrough(cls, v: str) -> str:
        if v not in PASSTHROUGH_MODES:
            raise ValueError("invalid passthrough mode")
        return v

    @model_validator(mode="after")
    def _writeback_needs_caching(self) -> "Pool":
        # The kernel writeback cache sits on top of the page cache, so it is
        # not merely useless without one - mergerfs refuses the combination.
        if self.cache_writeback and self.cache_files == "off":
            raise ValueError("cache.writeback requires cache.files other than off")
        return self

    @model_validator(mode="after")
    def _passthrough_conflicts(self) -> "Pool":
        # mergerfs resolves both of these silently - it flips cache.files to
        # auto-full and cache.writeback back to false - which would leave the
        # GUI showing settings the pool is not running. Refusing instead keeps
        # the form and the mount honest about each other.
        if self.passthrough != "off":
            if self.cache_files == "off":
                raise ValueError("passthrough requires cache.files other than off")
            if self.cache_writeback:
                raise ValueError("passthrough cannot be combined with cache.writeback")
        return self

    @field_validator("extra_options")
    @classmethod
    def _valid_extra(cls, v: str) -> str:
        v = v.strip().strip(",")
        if not v:
            return ""
        # The same rule the paths above get, and for the same reason: this
        # string is interpolated straight into the pool unit's ExecStart=,
        # where % opens a specifier expansion and quotes and backslashes are
        # unescaped before the command line is split. No mergerfs option needs
        # any of those characters, while a single stray % silently produces a
        # unit systemd refuses to load - so the pool stops mounting and the
        # cause is nowhere near the field that was edited.
        if UNSAFE_UNIT_CHARS.search(v):
            raise ValueError("invalid characters in extra options")
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


class BindMount(BaseModel):
    """One entry of the presentation tree: `source` shown at `target`.

    The point of a bind mount here is to decouple what the users browse over
    Samba from where the bytes actually live - a ZFS dataset and a mergerfs
    pool can appear as two sibling folders of the same share.
    """

    name: str
    source: str
    target: str
    read_only: bool = False

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not POOL_NAME_RE.match(v):
            raise ValueError("invalid bind mount name")
        return v

    @field_validator("source", "target")
    @classmethod
    def _valid_path(cls, v: str) -> str:
        return _unit_safe_abs_path(v)

    @model_validator(mode="after")
    def _no_nesting(self) -> "BindMount":
        if self.source == self.target:
            raise ValueError("source and target must differ")
        # Binding a directory into itself, or into something below itself,
        # produces a mount loop rather than an error message.
        if (self.target + "/").startswith(self.source + "/") or (
            self.source + "/"
        ).startswith(self.target + "/"):
            raise ValueError("source and target may not contain each other")
        return self


class DiskSleepPolicy(BaseModel):
    """How long a single physical disk may idle before it is spun down.

    Keyed in the state by the /dev/disk/by-id name rather than by /dev/sdX:
    the kernel names are assigned in discovery order and can move between
    boots, so a policy keyed by them would silently start applying to a
    different disk. The by-id name carries the model and serial, which is
    also exactly what the hd-idle configuration this replaces used.
    """

    idle_seconds: int = 0
    # The spin-down command that last worked on this disk. hdparm cannot put
    # every drive into standby, so the caller walks a chain of methods and
    # remembers the winner instead of paying for the failures every time.
    method: Optional[str] = None

    @field_validator("idle_seconds")
    @classmethod
    def _valid_idle(cls, v: int) -> int:
        if v not in IDLE_CHOICES:
            raise ValueError("invalid idle time")
        return v


class DiskSleepSettings(BaseModel):
    enabled: bool = True
    poll_seconds: int = 30
    # A disk nobody has configured yet keeps spinning. Spinning down a disk
    # the user did not ask about is the one failure mode that can cost data
    # availability (or a stalled VM), so the safe direction is "do nothing".
    default_idle_seconds: int = 0
    retention_days: int = 90

    # Temperature sampling. Its own retention, deliberately longer than the
    # event log's: a year of readings is a few megabytes and makes summer
    # comparable with winter, while a year of event rows is just a long list.
    temp_enabled: bool = True
    temp_interval_seconds: int = 300
    temp_retention_days: int = 365
    # The usual upper half of the healthy range for a spinning disk. Applied
    # with an offset for devices that do not spin - see TEMP_SSD_OFFSET.
    temp_warn_celsius: int = 45
    temp_crit_celsius: int = 55

    @field_validator("poll_seconds")
    @classmethod
    def _valid_poll(cls, v: int) -> int:
        if not 10 <= v <= 300:
            raise ValueError("poll interval must be between 10 and 300 seconds")
        return v

    @field_validator("default_idle_seconds")
    @classmethod
    def _valid_default_idle(cls, v: int) -> int:
        if v not in IDLE_CHOICES:
            raise ValueError("invalid idle time")
        return v

    @field_validator("retention_days", "temp_retention_days")
    @classmethod
    def _valid_retention(cls, v: int) -> int:
        if not 1 <= v <= 3650:
            raise ValueError("retention must be between 1 and 3650 days")
        return v

    @field_validator("temp_interval_seconds")
    @classmethod
    def _valid_temp_interval(cls, v: int) -> int:
        if not 60 <= v <= 3600:
            raise ValueError("temperature interval must be between 60 and 3600 seconds")
        return v

    @field_validator("temp_warn_celsius", "temp_crit_celsius")
    @classmethod
    def _valid_threshold(cls, v: int) -> int:
        if not 20 <= v <= 100:
            raise ValueError("threshold must be between 20 and 100 degrees")
        return v

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "DiskSleepSettings":
        if self.temp_crit_celsius <= self.temp_warn_celsius:
            raise ValueError("the critical threshold must be above the warning one")
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
