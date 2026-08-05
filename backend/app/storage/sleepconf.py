"""Pure parsing and configuration helpers for disk spin-down.

No subprocess and no filesystem access: everything that talks to real devices
lives in disksleep.py, mirroring the poolconf.py/pools.py and
bindconf.py/binds.py split the rest of the package already uses.

The two signals this module parses are deliberately the cheap ones:

  /proc/diskstats  - request counters, served from kernel memory. Reading it
                     costs no I/O at all, so the idle clock it drives can
                     never be the thing that keeps a disk awake.
  hdparm -C        - the drive's actual power state. CHECK POWER MODE is
                     answered by the drive's electronics; unlike almost every
                     other query, it does not spin a sleeping platter back up.
"""

import json
import re
import shlex
from typing import Dict, List, Optional, Tuple

from .poolconf import contains_system_mount

# hdparm prints "drive state is:  active/idle", "standby" or "sleeping".
POWER_STATE_RE = re.compile(r"drive state is:\s*(.+?)\s*$", re.MULTILINE)
ACTIVE = "active"
STANDBY = "standby"
SLEEPING = "sleeping"
UNKNOWN = "unknown"
# Both of the drive's low-power states count as "asleep" for the UI: standby
# is heads parked and platters stopped, sleeping is that plus the interface
# powered down. Only the second needs a reset to come back, which is the
# drive's problem, not ours.
ASLEEP_STATES = (STANDBY, SLEEPING)

# A by-id name is model + serial, so it can carry anything the vendor put on
# the label. Kept to the characters udev actually emits.
BY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}$")

# lsblk reports an active swap area with this literal mountpoint.
SWAP_MOUNTPOINT = "[SWAP]"


def parse_power_state(output: str) -> str:
    """`hdparm -C` output -> one of ACTIVE / STANDBY / SLEEPING / UNKNOWN.

    Anything unrecognised becomes UNKNOWN rather than raising: a disk whose
    state cannot be read is a disk we must not act on, and that is exactly
    what the callers do with UNKNOWN.
    """
    match = POWER_STATE_RE.search(output or "")
    if not match:
        return UNKNOWN
    state = match.group(1).strip().lower()
    if state.startswith("active") or state == "idle":
        return ACTIVE
    if state.startswith("standby"):
        return STANDBY
    if state.startswith("sleeping"):
        return SLEEPING
    return UNKNOWN


def parse_diskstats(text: str) -> Dict[str, Tuple[int, int]]:
    """/proc/diskstats -> {kernel name: (reads completed, writes completed)}.

    Only the completed-request counters are used. Sector counts would also
    work, but the request counts are what makes the "0 reads, 96 writes"
    detail in the event log readable: a wake-up that is pure writes is a
    snapshot, an atime update or a scrub, never someone browsing a share.
    """
    result: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        fields = line.split()
        # major minor name reads_completed reads_merged sectors_read ms
        # writes_completed ...
        if len(fields) < 8:
            continue
        try:
            result[fields[2]] = (int(fields[3]), int(fields[7]))
        except ValueError:
            continue
    return result


# /proc/diskstats always reports sectors in 512-byte units, whatever the
# drive's own logical or physical sector size is. That is a kernel interface
# convention, not a property of the device - reading the real sector size from
# sysfs and multiplying by it would overstate throughput fourfold on every 4Kn
# disk.
DISKSTATS_SECTOR = 512


def parse_disk_io(text: str) -> Dict[str, Tuple[int, int]]:
    """/proc/diskstats -> {kernel name: (bytes read, bytes written)}.

    The counterpart of parse_diskstats: that one counts requests, for deciding
    whether a disk was touched at all, this one counts bytes, for showing how
    fast. Same file, same free read - no I/O is issued to any device, so
    polling it every couple of seconds cannot wake a sleeping disk.
    """
    result: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        fields = line.split()
        # major minor name reads_completed reads_merged sectors_read ms_reading
        # writes_completed writes_merged sectors_written ...
        if len(fields) < 10:
            continue
        try:
            result[fields[2]] = (
                int(fields[5]) * DISKSTATS_SECTOR,
                int(fields[9]) * DISKSTATS_SECTOR,
            )
        except ValueError:
            continue
    return result


def is_asleep(state: str) -> bool:
    return state in ASLEEP_STATES


def _descendant_mountpoints(dev: dict) -> List[str]:
    out = []
    mp = dev.get("mountpoint")
    if mp:
        out.append(mp)
    for child in dev.get("children") or []:
        out.extend(_descendant_mountpoints(child))
    return out


def _descendant_fstypes(dev: dict) -> List[str]:
    out = []
    fstype = dev.get("fstype")
    if fstype:
        out.append(fstype)
    for child in dev.get("children") or []:
        out.extend(_descendant_fstypes(child))
    return out


def _has_swap(dev: dict) -> bool:
    if dev.get("mountpoint") == SWAP_MOUNTPOINT:
        return True
    return any(_has_swap(child) for child in dev.get("children") or [])


def parse_physical_disks(output: str) -> List[dict]:
    """`lsblk -J` output -> one entry per whole physical disk.

    Deliberately not poolconf.parse_lsblk: that one flattens to leaf devices
    because mounting and formatting act on partitions. Spinning a disk down
    acts on the disk itself - /dev/sdb, never /dev/sdb1 - so the parents are
    exactly what has to survive here, and the partitions only matter as
    evidence of what the disk is being used for.

    `system` marks the disks the GUI must never touch. The mountpoint rule
    inherited from poolconf catches an ordinary root filesystem (through LVM
    too, since it walks the children); the swap rule is added here because a
    disk holding an active swap area is load-bearing for the running system
    in a way that no mountpoint reveals. ZFS-on-root needs a third rule that
    cannot be answered from lsblk at all - see disksleep.system_disk_paths.
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    disks = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        mountpoints = _descendant_mountpoints(dev)
        disks.append({
            "path": dev.get("path") or "",
            "name": (dev.get("path") or "").rsplit("/", 1)[-1],
            "size": dev.get("size") or 0,
            "model": (dev.get("model") or "").strip(),
            "serial": (dev.get("serial") or "").strip(),
            # lsblk emits ROTA as a boolean in JSON mode, but older versions
            # emit the string "1"/"0"; both mean the same thing.
            "rotational": str(dev.get("rota", True)).lower() in ("true", "1"),
            "system": contains_system_mount(dev) or _has_swap(dev),
            "mountpoints": [m for m in mountpoints if m != SWAP_MOUNTPOINT],
            "fstypes": _descendant_fstypes(dev),
        })
    return disks


def parse_zfs_root_pool(findmnt_output: str) -> Optional[str]:
    """`findmnt -n -o SOURCE,FSTYPE /` -> the pool holding root, or None.

    Exists because the inherited system-disk rule cannot see a ZFS root. On
    a ZFS-on-root install the partition carrying the pool has fstype
    zfs_member and *no mountpoint at all* - the pool is mounted, the
    partition is not - so a rule that looks for "/" finds nothing and the
    Proxmox system disk would show up as a perfectly good candidate for
    spinning down.
    """
    fields = (findmnt_output or "").split()
    if len(fields) < 2 or fields[1] != "zfs":
        return None
    source = fields[0]
    return source.split("/", 1)[0] or None


def parse_zpool_devices(output: str) -> List[str]:
    """`zpool list -vHP <pool>` -> the device paths backing it.

    The vdev rows are indented with tabs and carry a full path thanks to -P;
    everything else (the pool row, the mirror/raidz rows, spares) has a bare
    name in that column and is skipped by the leading-slash test.
    """
    devices = []
    for line in (output or "").splitlines():
        fields = line.split()
        if fields and fields[0].startswith("/"):
            devices.append(fields[0])
    return devices


def parse_zfs_property(output: str) -> str:
    """`zfs get -H -o value <prop> <target>` -> the bare value."""
    return (output or "").strip().splitlines()[0].strip() if (output or "").strip() else ""


def parse_hd_idle_opts(text: str) -> Tuple[int, Dict[str, int]]:
    """`/etc/default/hd-idle` contents -> (default idle, {by-id name: idle}).

    Imported once when the GUI takes over from hd-idle, so the disks keep the
    timings the user already chose instead of silently reverting to "never".

    hd-idle's own grammar: -i before any -a sets the default, and every later
    -a names the disk that the following -i applies to. The values are
    seconds, and 0 means "never spin this one down".
    """
    match = re.search(r"^\s*HD_IDLE_OPTS\s*=\s*(.+?)\s*$", text or "", re.MULTILINE)
    if not match:
        return 0, {}
    raw = match.group(1).strip()
    try:
        # The line is a shell assignment, so the value is usually quoted and
        # may be quoted either way.
        tokens = shlex.split(raw)
    except ValueError:
        return 0, {}
    if len(tokens) == 1:
        try:
            tokens = shlex.split(tokens[0])
        except ValueError:
            return 0, {}

    default = 0
    per_disk: Dict[str, int] = {}
    current: Optional[str] = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-a" and index + 1 < len(tokens):
            current = tokens[index + 1].rsplit("/", 1)[-1]
            index += 2
            continue
        if token == "-i" and index + 1 < len(tokens):
            try:
                seconds = int(tokens[index + 1])
            except ValueError:
                index += 2
                continue
            if current is None:
                default = seconds
            else:
                per_disk[current] = seconds
            index += 2
            continue
        index += 1
    return default, per_disk


def nearest_idle_choice(seconds: int, choices) -> int:
    """Snap an imported hd-idle timing onto the GUI's dropdown.

    hd-idle accepts any number of seconds; the GUI offers a fixed list. The
    user's 300-second Toshiba setting has no exact match, and rounding it to
    the closest offered value (15 minutes) is both harmless and honest -
    which is why the takeover reports what it did.
    """
    if seconds <= 0:
        return 0
    return min((c for c in choices if c > 0), key=lambda c: abs(c - seconds))


# --- smartd -----------------------------------------------------------------

SMARTD_SKIP_RE = re.compile(r"(^|\s)-n\s")


def _is_smartd_device_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    return stripped.startswith("DEVICESCAN") or stripped.startswith("/dev/")


def smartd_lines_without_standby(conf: str) -> List[str]:
    """The smartd device lines that would wake a sleeping disk.

    smartd polls SMART data every 30 minutes by default. Reading SMART from a
    drive in standby spins it up, so a config with no -n directive quietly
    guarantees that no disk ever stays asleep for longer than one polling
    interval - which is the single most common reason spin-down "does not
    work" on an otherwise correctly configured host.
    """
    return [
        line for line in (conf or "").splitlines()
        if _is_smartd_device_line(line) and not SMARTD_SKIP_RE.search(line)
    ]


def fix_smartd_conf(conf: str) -> str:
    """Append `-n standby,q` to every device line that has no -n directive.

    standby, not idle: it is the state this application actually puts disks
    into. The trailing ",q" suppresses the log line smartd would otherwise
    write on every skipped check.
    """
    out = []
    for line in (conf or "").splitlines():
        if _is_smartd_device_line(line) and not SMARTD_SKIP_RE.search(line):
            out.append(line.rstrip() + " -n standby,q")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if out else "")


# --- mount options ----------------------------------------------------------

def mount_options(proc_mounts: str, mountpoint: str) -> Optional[List[str]]:
    """The options a mountpoint is currently mounted with, or None.

    /proc/mounts escapes spaces and a few other characters as octal; the
    mountpoints this application creates cannot contain them (the model
    rejects whitespace outright), so a plain comparison is enough.
    """
    target = mountpoint.rstrip("/") or "/"
    for line in (proc_mounts or "").splitlines():
        fields = line.split()
        if len(fields) >= 4 and (fields[1].rstrip("/") or "/") == target:
            return fields[3].split(",")
    return None


def add_noatime(fstab_line: str) -> str:
    """Add noatime to the options field of one fstab line.

    Only ever applied to the GUI's own `# pnas:disk:<name>` lines, so the
    six-field layout is guaranteed by disk_fstab_line() rather than assumed.
    """
    parts = fstab_line.split(None, 4)
    if len(parts) < 4:
        return fstab_line
    options = [o for o in parts[3].split(",") if o]
    if "noatime" in options:
        return fstab_line
    # relatime and atime contradict noatime; the kernel takes the last one,
    # but leaving them in makes the line read as if it says two things.
    options = [o for o in options if o not in ("atime", "relatime", "strictatime")]
    options.append("noatime")
    parts[3] = ",".join(options)
    return " ".join(parts)


# --- PVE / updatedb ---------------------------------------------------------

def parse_pve_storage_paths(conf: str) -> Dict[str, str]:
    """/etc/pve/storage.cfg -> {storage id: path}, for path-based storages.

    pvestatd asks every configured storage for its status every ten seconds.
    For a directory storage that means stat()ing the path and, depending on
    its content types, listing it - which is enough to keep the disk under it
    awake no matter what this application does.
    """
    result: Dict[str, str] = {}
    current: Optional[str] = None
    for line in (conf or "").splitlines():
        header = re.match(r"^(\w+):\s*(\S+)\s*$", line)
        if header:
            current = header.group(2)
            continue
        body = re.match(r"^\s+path\s+(\S+)\s*$", line)
        if body and current:
            result[current] = body.group(1)
    return result


def parse_prune_paths(conf: str) -> List[str]:
    """The PRUNEPATHS entries of /etc/updatedb.conf."""
    match = re.search(r'^\s*PRUNEPATHS\s*=\s*"([^"]*)"', conf or "", re.MULTILINE)
    return match.group(1).split() if match else []


def path_is_covered(paths: List[str], target: str) -> bool:
    """Whether `target` is at or below any of `paths`.

    Not a prefix test, for the same reason deps._covers is not one:
    /mnt/disks/media2 merely starts with the same characters as
    /mnt/disks/media.
    """
    target = target.rstrip("/")
    for path in paths:
        root = path.rstrip("/")
        if target == root or target.startswith(root + "/"):
            return True
    return False
