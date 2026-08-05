"""Disk spin-down: everything that touches real devices and real config.

The pure half - parsing hdparm/lsblk/diskstats output, rewriting config text -
lives in sleepconf.py, mirroring the poolconf.py/pools.py split.

Why this replaces hd-idle rather than driving it: hd-idle logs only the
spin-downs it performs itself, and only to syslog. The question worth
answering is the other one - what woke a disk up, and when - and no amount of
configuration generation gets there. Owning the loop also means one place
decides how a disk is put to sleep, which matters because hdparm cannot do it
for every drive.
"""

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.proc import SystemOpError, run
from ..models import State
from . import poolconf, pools, sleepconf

# Tried in order until the drive reports standby. hdparm speaks ATA STANDBY
# IMMEDIATE, which plenty of drives ignore or never see - USB and SAS bridges
# in particular pass SCSI through, not ATA. sg_start issues SCSI START STOP
# UNIT, which is what those understand; sdparm is the last resort for drives
# that answer neither.
SPINDOWN_METHODS: Tuple[Tuple[str, List[str]], ...] = (
    ("hdparm", ["hdparm", "-y"]),
    ("sg_start", ["sg_start", "--stop"]),
    ("sdparm", ["sdparm", "--command=stop"]),
)
# A drive takes a moment to park its heads; asking straight away reports the
# state it was still in.
SPINDOWN_SETTLE = 3
SPINDOWN_TIMEOUT = 20

SMARTD_UNIT = "smartd"
HD_IDLE_UNIT = "hd-idle"

FIXABLE = ("smartd", "zfs_atime", "noatime")


def smartd_conf_path() -> Path:
    return Path(os.environ.get("PNAS_SMARTD_CONF", "/etc/smartd.conf"))


def hd_idle_conf_path() -> Path:
    return Path(os.environ.get("PNAS_HD_IDLE_CONF", "/etc/default/hd-idle"))


def pve_storage_path() -> Path:
    return Path(os.environ.get("PNAS_PVE_STORAGE", "/etc/pve/storage.cfg"))


def updatedb_conf_path() -> Path:
    return Path(os.environ.get("PNAS_UPDATEDB_CONF", "/etc/updatedb.conf"))


def cron_dir() -> Path:
    return Path(os.environ.get("PNAS_CRON_DIR", "/etc/cron.d"))


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


# --- device enumeration -----------------------------------------------------

def _lsblk_disks() -> List[dict]:
    try:
        # --tree=PATH for the same reason list_block_devices needs it: with a
        # custom -o list that omits NAME, lsblk silently flattens the output,
        # and the partitions are the evidence of what a disk is used for.
        out = run([
            "lsblk", "-J", "-b", "--tree=PATH", "-o",
            "PATH,TYPE,SIZE,FSTYPE,UUID,MOUNTPOINT,MODEL,SERIAL,ROTA",
        ])
    except SystemOpError:
        return []
    return sleepconf.parse_physical_disks(out)


def zfs_root_pool() -> Optional[str]:
    try:
        out = run(["findmnt", "-n", "-o", "SOURCE,FSTYPE", "/"])
    except SystemOpError:
        return None
    return sleepconf.parse_zfs_root_pool(out)


def zpool_names() -> List[str]:
    try:
        out = run(["zpool", "list", "-H", "-o", "name"])
    except SystemOpError:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def zpool_devices(pool: str) -> List[str]:
    try:
        out = run(["zpool", "list", "-vHP", pool])
    except SystemOpError:
        return []
    return sleepconf.parse_zpool_devices(out)


def _resolve(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def _owning_disk(disk_paths: List[str], device: str) -> Optional[str]:
    """Which whole disk a partition path belongs to.

    Purely by string containment against the known disk paths, which is what
    the kernel naming guarantees (/dev/sda1 under /dev/sda, /dev/nvme0n1p1
    under /dev/nvme0n1) and avoids another round of sysfs walking.
    """
    resolved = _resolve(device)
    for disk in sorted(disk_paths, key=len, reverse=True):
        if resolved == disk or resolved.startswith(disk):
            return disk
    return None


def list_sleep_disks() -> List[dict]:
    """Every physical disk the GUI may manage the sleep state of.

    Excluded, in order of how obvious they are: the system disk by mountpoint
    (inherited from the pool code), a disk holding active swap, the members of
    a ZFS root pool (invisible to both of those rules), and anything without a
    stable /dev/disk/by-id name to key a policy on.

    Non-rotational devices are kept in the list but marked. Spinning down an
    SSD achieves nothing, and silently dropping it would read as the GUI
    failing to see the disk.
    """
    disks = _lsblk_disks()
    paths = [d["path"] for d in disks if d["path"]]

    system_paths = {d["path"] for d in disks if d["system"]}
    root_pool = zfs_root_pool()
    if root_pool:
        for device in zpool_devices(root_pool):
            owner = _owning_disk(paths, device)
            if owner:
                system_paths.add(owner)

    by_id = pools.by_id_map()
    result = []
    for disk in disks:
        if disk["path"] in system_paths:
            continue
        names = sorted(by_id.get(_resolve(disk["path"]), []))
        if not names:
            # No by-id name means no stable key, and /dev/sdX is not one:
            # a policy stored against it could land on a different disk
            # after a reboot.
            continue
        result.append({**disk, "by_id": names[0], "by_id_all": names})
    return sorted(result, key=lambda d: d["by_id"])


# --- power state ------------------------------------------------------------

def power_state(path: str) -> str:
    """The drive's current power state, without waking it.

    hdparm -C issues ATA CHECK POWER MODE, which the drive answers from its
    electronics. Almost every other way of asking a disk anything - including
    a plain read of sector 0, and including most of smartctl - spins it back
    up instead.
    """
    try:
        return sleepconf.parse_power_state(run(["hdparm", "-C", path], timeout=15))
    except SystemOpError:
        return sleepconf.UNKNOWN


def read_diskstats() -> Dict[str, Tuple[int, int]]:
    return sleepconf.parse_diskstats(_read(Path("/proc/diskstats")))


def read_disk_io() -> Dict[str, Tuple[int, int]]:
    return sleepconf.parse_disk_io(_read(Path("/proc/diskstats")))


# by-id name -> kernel name, with a short time-to-live. The throughput
# endpoint is polled every couple of seconds and only needs this mapping;
# rebuilding it means running lsblk and walking /dev/disk/by-id, which is far
# too much for that cadence. A disk plugged in mid-session therefore takes up
# to NAME_CACHE_TTL seconds to start showing a rate, which is a fair trade for
# not running lsblk 1800 times an hour.
NAME_CACHE_TTL = 60
_name_cache: Tuple[float, Dict[str, str]] = (0.0, {})


def disk_names() -> Dict[str, str]:
    global _name_cache
    cached_at, cached = _name_cache
    now = time.time()
    if cached and now - cached_at < NAME_CACHE_TTL:
        return cached
    names = {d["by_id"]: d["name"] for d in list_sleep_disks()}
    _name_cache = (now, names)
    return names


def spin_down(path: str, preferred: Optional[str] = None) -> Tuple[bool, Optional[str], str]:
    """Put a disk into standby. Returns (ok, method that worked, detail).

    Walks the method chain and verifies with hdparm -C after each attempt,
    because a command that exits 0 is not evidence: hdparm reports success for
    a STANDBY IMMEDIATE the drive quietly ignored. `preferred` is the method
    that worked last time, tried first so the steady state costs one command.
    """
    order = list(SPINDOWN_METHODS)
    if preferred:
        order.sort(key=lambda m: m[0] != preferred)

    tried = []
    for name, argv in order:
        try:
            run([*argv, path], timeout=SPINDOWN_TIMEOUT)
        except SystemOpError as exc:
            tried.append(f"{name}: {exc}")
            continue
        time.sleep(SPINDOWN_SETTLE)
        if sleepconf.is_asleep(power_state(path)):
            detail = f"{name} ok"
            if tried:
                detail = "; ".join(tried) + f"; {name} ok"
            return True, name, detail
        tried.append(f"{name}: no standby")
    return False, None, "; ".join(tried) or "no spin-down method available"


# --- what keeps a disk awake ------------------------------------------------

class Environment:
    """Everything the warning checks need, collected once per request.

    Each check on its own is a subprocess or a file read; running them per
    disk would mean a dozen of them per disk per page load. Gathering the
    host-wide facts once and mapping them onto disks afterwards keeps the
    page cheap enough to render on every visit.
    """

    def __init__(self, state: State):
        self.state = state
        self.smartd_active = pools.systemctl("is-active", "--quiet", SMARTD_UNIT)
        self.smartd_conf = _read(smartd_conf_path())
        self.proc_mounts = _read(Path("/proc/mounts"))
        self.pve_storages = sleepconf.parse_pve_storage_paths(_read(pve_storage_path()))
        self.prune_paths = sleepconf.parse_prune_paths(_read(updatedb_conf_path()))
        self.updatedb_installed = updatedb_conf_path().exists()
        self.autosnapshot_cron = (cron_dir() / "zfs-auto-snapshot").exists()
        self.zfs_scrub_cron = (cron_dir() / "zfsutils-linux").exists()

        # disk path -> ZFS pool name, and the pool properties we ask about.
        self.zfs_pool_of: Dict[str, str] = {}
        self.zfs_atime: Dict[str, str] = {}
        self.zfs_autosnapshot: Dict[str, str] = {}
        self.zfs_volumes: Dict[str, int] = {}

    def load_zfs(self, disk_paths: List[str]) -> None:
        root_pool = zfs_root_pool()
        for pool in zpool_names():
            if pool == root_pool:
                continue
            members = [
                owner for device in zpool_devices(pool)
                if (owner := _owning_disk(disk_paths, device))
            ]
            if not members:
                continue
            for member in members:
                self.zfs_pool_of[member] = pool
            self.zfs_atime[pool] = _zfs_get(pool, "atime")
            self.zfs_autosnapshot[pool] = _zfs_get(pool, "com.sun:auto-snapshot")
            self.zfs_volumes[pool] = _zfs_volume_count(pool)


def _zfs_get(target: str, prop: str) -> str:
    try:
        return sleepconf.parse_zfs_property(
            run(["zfs", "get", "-H", "-o", "value", prop, target])
        )
    except SystemOpError:
        return ""


def _zfs_volume_count(pool: str) -> int:
    try:
        out = run(["zfs", "list", "-H", "-o", "name", "-t", "volume", "-r", pool])
    except SystemOpError:
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def _managed_mount_for(state: State, disk: dict) -> Optional[Tuple[str, str]]:
    """The GUI-managed disk mount living on this disk, as (name, mountpoint)."""
    for name, mount in state.disk_mounts.items():
        if mount.mountpoint in disk["mountpoints"]:
            return name, mount.mountpoint
    return None


def _pools_on(state: State, mountpoints: List[str]) -> List[str]:
    found = []
    for name, pool in state.pools.items():
        if any(branch.path in mountpoints for branch in pool.branches):
            found.append(name)
    return sorted(found)


MERGERFS_CACHE_KEYS = ("cache.statfs", "cache.attr", "cache.entry")
MERGERFS_CACHE_SUGGESTION = "cache.statfs=60,cache.attr=300,cache.entry=300"


def warnings_for(env: Environment, disk: dict) -> List[dict]:
    """Everything found that can keep this particular disk awake.

    Each entry carries a stable `id` and a `vars` map instead of a sentence:
    the frontend translates it, the same way the pool policy names are
    translated from a key prefix.
    """
    found: List[dict] = []
    state = env.state
    mountpoints = disk["mountpoints"]

    if env.smartd_active:
        offenders = sleepconf.smartd_lines_without_standby(env.smartd_conf)
        if offenders:
            found.append({
                "id": "smartd",
                "severity": "crit",
                "fixable": True,
                "vars": {"path": str(smartd_conf_path())},
                "command": offenders[0].strip() + " -n standby,q",
            })

    zfs_pool = env.zfs_pool_of.get(disk["path"])
    if zfs_pool:
        if env.zfs_atime.get(zfs_pool) == "on":
            found.append({
                "id": "zfs_atime",
                "severity": "warn",
                "fixable": True,
                "vars": {"pool": zfs_pool},
                "command": f"zfs set atime=off {zfs_pool}",
            })
        if env.autosnapshot_cron and env.zfs_autosnapshot.get(zfs_pool) != "false":
            found.append({
                "id": "zfs_autosnapshot",
                "severity": "info",
                "fixable": False,
                "vars": {"pool": zfs_pool},
                "command": f"zfs set com.sun:auto-snapshot=false {zfs_pool}",
            })
        if env.zfs_scrub_cron:
            found.append({
                "id": "zfs_scrub", "severity": "info", "fixable": False,
                "vars": {"pool": zfs_pool}, "command": "",
            })
        if env.zfs_volumes.get(zfs_pool):
            found.append({
                "id": "zfs_zvol", "severity": "info", "fixable": False,
                "vars": {"pool": zfs_pool, "count": env.zfs_volumes[zfs_pool]},
                "command": "",
            })

    managed = _managed_mount_for(state, disk)
    if managed:
        name, mountpoint = managed
        options = sleepconf.mount_options(env.proc_mounts, mountpoint)
        if options is not None and "noatime" not in options:
            found.append({
                "id": "noatime",
                "severity": "warn",
                "fixable": True,
                "vars": {"mountpoint": mountpoint, "name": name},
                "command": f"mount -o remount,noatime {mountpoint}",
            })

    for pool_name in _pools_on(state, mountpoints):
        extra = state.pools[pool_name].extra_options
        if not any(key in extra for key in MERGERFS_CACHE_KEYS):
            found.append({
                "id": "mergerfs_cache",
                "severity": "warn",
                "fixable": False,
                "vars": {"pool": pool_name, "options": MERGERFS_CACHE_SUGGESTION},
                "command": MERGERFS_CACHE_SUGGESTION,
            })

    for storage_id, path in sorted(env.pve_storages.items()):
        if sleepconf.path_is_covered(mountpoints, path):
            found.append({
                "id": "pve_storage",
                "severity": "info",
                "fixable": False,
                "vars": {"id": storage_id, "path": path},
                "command": f"pvesm set {storage_id} --disable 1",
            })

    if env.updatedb_installed:
        unpruned = [
            mp for mp in mountpoints
            if not sleepconf.path_is_covered(env.prune_paths, mp)
        ]
        if unpruned:
            found.append({
                "id": "updatedb",
                "severity": "info",
                "fixable": False,
                "vars": {"mountpoint": unpruned[0], "path": str(updatedb_conf_path())},
                "command": "",
            })

    order = {"crit": 0, "warn": 1, "info": 2}
    return sorted(found, key=lambda w: (order.get(w["severity"], 3), w["id"]))


def describe(state: State, disks: List[dict]) -> Dict[str, dict]:
    """Per-disk warnings and ZFS membership, from a single Environment.

    Both answers come out of the same host-wide gathering, so they are
    returned together rather than making the caller pay for it twice.
    """
    env = Environment(state)
    env.load_zfs([d["path"] for d in disks])
    return {
        disk["by_id"]: {
            "warnings": warnings_for(env, disk),
            "zfs_pool": env.zfs_pool_of.get(disk["path"]),
        }
        for disk in disks
    }


# --- one-click fixes --------------------------------------------------------

def apply_fix(state: State, check: str, disk: dict) -> str:
    """Run one of the three fixes the GUI is allowed to perform.

    The whitelist is the point. Everything else the checks report - the
    Proxmox storage config, the ZFS snapshot policy, the mergerfs cache
    trade-off - is either outside this application's ownership or changes
    behaviour in a way only the user can weigh, so those are shown as a
    command to copy and nothing more.
    """
    if check not in FIXABLE:
        raise SystemOpError(f"not a fixable check: {check}")

    if check == "smartd":
        path = smartd_conf_path()
        conf = _read(path)
        if not conf:
            raise SystemOpError(f"{path} is empty or unreadable")
        backup = path.with_name(path.name + pools.FSTAB_BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_text(conf)
        path.write_text(sleepconf.fix_smartd_conf(conf))
        pools.systemctl("restart", SMARTD_UNIT)
        return f"{path} updated, {SMARTD_UNIT} restarted"

    if check == "zfs_atime":
        env_pool = Environment(state)
        env_pool.load_zfs([disk["path"]])
        pool = env_pool.zfs_pool_of.get(disk["path"])
        if not pool:
            raise SystemOpError("this disk is not a member of a ZFS pool")
        run(["zfs", "set", "atime=off", pool])
        return f"atime=off set on {pool}"

    # noatime
    managed = _managed_mount_for(state, disk)
    if not managed:
        raise SystemOpError("no GUI-managed mount on this disk")
    name, mountpoint = managed
    fstab = pools.read_fstab()
    lines = []
    changed = False
    for line in fstab.splitlines():
        if poolconf.parse_tag(line) == ("disk", name):
            fixed = sleepconf.add_noatime(line)
            changed = changed or fixed != line
            lines.append(fixed)
        else:
            lines.append(line)
    if not changed:
        raise SystemOpError(f"no managed fstab line found for {name}")
    pools.write_fstab("\n".join(lines) + "\n")
    run(["mount", "-o", "remount,noatime", mountpoint])
    return f"noatime applied to {mountpoint}"


# --- hd-idle takeover -------------------------------------------------------

def hd_idle_running() -> bool:
    return pools.systemctl("is-active", "--quiet", HD_IDLE_UNIT)


def take_over_from_hd_idle(choices) -> Tuple[Dict[str, int], List[str]]:
    """Stop hd-idle and translate its config into GUI policies.

    Returns the imported policies keyed by by-id name, plus notes about
    timings that had to be rounded onto the dropdown's fixed choices.
    """
    pools.systemctl("disable", "--now", HD_IDLE_UNIT)
    default, per_disk = sleepconf.parse_hd_idle_opts(_read(hd_idle_conf_path()))
    imported: Dict[str, int] = {}
    notes: List[str] = []
    for name, seconds in per_disk.items():
        snapped = sleepconf.nearest_idle_choice(seconds, choices)
        imported[name] = snapped
        if snapped != seconds:
            notes.append(f"{name}: {seconds}s -> {snapped}s")
    if default:
        notes.append(f"default idle time {default}s not imported")
    return imported, notes
