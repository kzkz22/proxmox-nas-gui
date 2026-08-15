import os
import subprocess
from pathlib import Path
from typing import List, Optional

from ..core.proc import SystemOpError, run
from ..models import State
from . import poolconf
from .models import Pool

FSTAB_BACKUP_SUFFIX = ".pnas-backup"
XATTR_CTL = ".mergerfs"


def fstab_path() -> Path:
    return Path(os.environ.get("PNAS_FSTAB", "/etc/fstab"))


def read_fstab() -> str:
    p = fstab_path()
    return p.read_text() if p.exists() else ""


def write_fstab(text: str) -> None:
    p = fstab_path()
    backup = p.with_name(p.name + FSTAB_BACKUP_SUFFIX)
    if p.exists() and not backup.exists():
        backup.write_text(p.read_text())
    tmp = p.with_name(p.name + ".pnas-tmp")
    tmp.write_text(text)
    os.replace(tmp, p)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=30)


def systemd_dir() -> Path:
    return Path(os.environ.get("PNAS_SYSTEMD_DIR", "/etc/systemd/system"))


def has_systemd() -> bool:
    """Whether the host is actually running systemd.

    Callers that must not silently substitute a plain mount for a unit start
    (see binds.mount_bind) need to tell "systemd refused" apart from "there is
    no systemd here", which the boolean below cannot express.
    """
    return Path("/run/systemd/system").is_dir()


def systemctl(*args: str) -> bool:
    """Best-effort systemctl; False when systemd is absent or the call failed.

    Public within the storage package: the bind mounts install units the same
    way pools do, so both go through this one wrapper.
    """
    try:
        proc = subprocess.run(
            ["systemctl", *args], capture_output=True, text=True, timeout=60
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def write_pool_unit(pool: Pool) -> None:
    path = systemd_dir() / poolconf.pool_unit_name(pool.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(poolconf.pool_unit(pool))
    systemctl("daemon-reload")
    systemctl("enable", poolconf.pool_unit_name(pool.name))


def remove_pool_unit(name: str) -> None:
    systemctl("disable", poolconf.pool_unit_name(name))
    path = systemd_dir() / poolconf.pool_unit_name(name)
    if path.exists():
        path.unlink()
    systemctl("daemon-reload")


def is_mounted(mountpoint: str) -> bool:
    return os.path.ismount(mountpoint)


def mount_pool(pool: Pool) -> None:
    Path(pool.mountpoint).mkdir(parents=True, exist_ok=True)
    if systemctl("start", poolconf.pool_unit_name(pool.name)):
        return
    # No systemd (e.g. running in a plain container): invoke mergerfs
    # directly with the exact options the unit would use.
    run([
        "mergerfs", "-o", poolconf.mergerfs_options(pool),
        poolconf.branches_spec(pool), pool.mountpoint,
    ])


def unmount_pool(pool: Pool) -> None:
    if systemctl("stop", poolconf.pool_unit_name(pool.name)):
        return
    if is_mounted(pool.mountpoint):
        run(["umount", pool.mountpoint])


def mount_disk(mountpoint: str) -> None:
    Path(mountpoint).mkdir(parents=True, exist_ok=True)
    run(["mount", mountpoint])


def unmount_disk(mountpoint: str) -> None:
    if is_mounted(mountpoint):
        run(["umount", mountpoint])


FORMAT_TIMEOUT = 120
BY_ID_PREFIXES = ("ata-", "scsi-", "nvme-")

MKFS_COMMANDS = {
    "ext4": lambda path: ["mkfs.ext4", "-F", path],
    "xfs": lambda path: ["mkfs.xfs", "-f", path],
}


def format_device(path: str, fstype: str) -> None:
    """Wipe any existing signatures and lay down a fresh filesystem directly
    on the given path - a partition, or a whole disk that's meant to hold the
    filesystem itself (see partition_whole_disk for the disk case actually
    used by the API, which creates a partition first and formats that).

    Settles udev afterward for the same reason partition_whole_disk does:
    the API immediately re-reads the device via list_block_devices() to
    pick up the fresh UUID, and without waiting for udev to process the
    mkfs's "change" event first, that read can still see no filesystem at
    all - "format succeeded but the new UUID could not be determined",
    even though the format itself worked.
    """
    run(["wipefs", "-a", path], timeout=FORMAT_TIMEOUT)
    run(MKFS_COMMANDS[fstype](path), timeout=FORMAT_TIMEOUT)
    run(["udevadm", "settle", "--timeout=10"], timeout=15)


def partition_whole_disk(path: str) -> str:
    """Create a GPT label with a single Linux-filesystem partition spanning
    the whole disk, and return the new partition's device path.

    A bare filesystem directly on the disk device (no partition table) works
    fine for mergerfs, but confuses other tools/OSes if the disk is ever
    moved elsewhere - so a blank whole disk always gets one full-size
    partition first, matching how the existing sda1/sdb1-style disks in this
    system are already laid out.
    """
    run(["wipefs", "-a", path], timeout=FORMAT_TIMEOUT)
    run(["sfdisk", path], input_text="label: gpt\n,,L\n", timeout=FORMAT_TIMEOUT)
    run(["udevadm", "settle", "--timeout=10"], timeout=15)
    created = [
        d for d in list_block_devices()
        if d["path"] != path and d["path"].startswith(path)
    ]
    if len(created) != 1:
        raise SystemOpError(
            f"expected exactly one partition on {path} after partitioning, "
            f"found {len(created)}"
        )
    return created[0]["path"]


def by_id_map() -> dict:
    """Map a resolved device path to its stable /dev/disk/by-id symlinks.

    Only ata-/scsi-/nvme- names are kept (physical-disk identifiers); the
    wwn- and LVM/dm- entries in the same directory aren't useful for a
    human picking a disk to format.
    """
    base = Path("/dev/disk/by-id")
    result: dict = {}
    if not base.is_dir():
        return result
    for entry in base.iterdir():
        if not entry.name.startswith(BY_ID_PREFIXES):
            continue
        try:
            target = str(entry.resolve())
        except OSError:
            continue
        result.setdefault(target, []).append(entry.name)
    return result


def usage(path: str) -> Optional[dict]:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_frsize * st.f_blocks
    free = st.f_frsize * st.f_bavail
    return {"total": total, "free": free, "used": total - free}


def runtime_update(pool: Pool, old: Optional[Pool] = None) -> Optional[str]:
    """Push the new branch list and tunables into a mounted pool via the
    mergerfs xattr control file, so edits apply without a remount. Returns
    a warning string when that fails (changes then apply on next remount)."""
    if not is_mounted(pool.mountpoint):
        return None
    ctl = os.path.join(pool.mountpoint, XATTR_CTL)
    try:
        os.setxattr(ctl, "user.mergerfs.branches",
                    poolconf.branches_spec(pool).encode())
        os.setxattr(ctl, "user.mergerfs.category.create",
                    pool.create_policy.encode())
        os.setxattr(ctl, "user.mergerfs.minfreespace",
                    pool.minfreespace.encode())
        os.setxattr(ctl, "user.mergerfs.moveonenospc",
                    (b"true" if pool.moveonenospc else b"false"))
        os.setxattr(ctl, "user.mergerfs.cache.files",
                    pool.cache_files.encode())
        os.setxattr(ctl, "user.mergerfs.dropcacheonclose",
                    (b"true" if pool.dropcacheonclose else b"false"))
    except OSError as exc:
        return (
            "fstab updated, but the live pool could not be reconfigured "
            f"({exc}); changes take effect after a remount"
        )
    return remount_only_warning(pool, old, ctl)


def remount_only_warning(pool: Pool, old: Optional[Pool], ctl: str) -> Optional[str]:
    """Warn about changed settings that a running mergerfs cannot pick up.

    cache.writeback is negotiated with the kernel when the FUSE connection is
    established, so unlike every other option the GUI writes it cannot be
    changed through the control file - the save would otherwise look like it
    took effect while the pool kept running the old setting, which is exactly
    the kind of silent no-op that sends someone benchmarking a setting they
    are not actually running. extra_options gets the same treatment for a
    different reason: the GUI does not know which of the keys a user typed
    there are runtime-settable, so it does not guess.

    Only *changes* are reported. A save that leaves these fields alone has
    nothing to warn about, however they are set.
    """
    if old is None:
        return None
    stale: List[str] = []
    if pool.cache_writeback != old.cache_writeback:
        want = "true" if pool.cache_writeback else "false"
        try:
            live = os.getxattr(ctl, "user.mergerfs.cache.writeback").decode().strip()
        except OSError:
            live = ""  # not readable on this mergerfs; trust the stored value
        if live != want:
            stale.append(f"cache.writeback={want}")
    if pool.extra_options != old.extra_options:
        stale.extend(opt for opt in pool.extra_options.split(",") if opt)
    if not stale:
        return None
    return (
        "saved, but these options only take effect after remounting the "
        f"pool: {', '.join(stale)}"
    )


def pool_info(pool: Pool) -> dict:
    mounted = is_mounted(pool.mountpoint)
    return {
        **pool.model_dump(),
        "mounted": mounted,
        "usage": usage(pool.mountpoint) if mounted else None,
        "branch_usage": [
            {"path": b.path, "mode": b.mode.value, "usage": usage(b.path)}
            for b in pool.branches
        ],
    }


def list_block_devices() -> List[dict]:
    try:
        # lsblk only nests dependents (partitions, LVM volumes) under their
        # parent as "children" in JSON when the NAME column is requested or
        # tree output is forced explicitly; with a custom -o list that omits
        # NAME (as below, in favor of PATH) it silently falls back to a flat
        # list instead. parse_lsblk relies on the nested tree to recognise a
        # disk's own partitions and to walk down to the system disk's root
        # mountpoint, so --tree=PATH is required, not optional.
        out = run([
            "lsblk", "-J", "-b", "--tree=PATH", "-o",
            "PATH,TYPE,SIZE,FSTYPE,UUID,MOUNTPOINT,MODEL,SERIAL,LABEL",
        ])
    except SystemOpError:
        return []
    devices = poolconf.parse_lsblk(out)
    by_id = by_id_map()
    for dev in devices:
        try:
            resolved = str(Path(dev["path"]).resolve())
        except OSError:
            resolved = dev["path"]
        dev["by_id"] = by_id.get(resolved, [])
    return devices


def pools_using_path(state: State, path: str) -> List[str]:
    p = path.rstrip("/")
    return sorted(
        name for name, pool in state.pools.items()
        if any(b.path == p for b in pool.branches)
    )


def orphaned_binds(state: State, old: Pool, new: Pool) -> List[str]:
    """Bind mounts whose source would stop existing under `new`'s branches.

    mergerfs's create policy (see mergerfs_options/category.create) puts a
    path that does not exist yet on exactly one branch, not every branch. So
    a subtree a bind mount presents can easily live on only one disk, and
    removing that disk from the pool makes the union - and everything a bind
    mount shows through it - go missing even though nothing was deleted. This
    only checks removed branches against what is left on disk, so it is safe
    to call before the pool's branch list is actually saved or remounted.
    """
    removed = poolconf.branches_removed(old, new)
    if not removed:
        return []
    remaining = [b.path for b in new.branches]
    mountpoint = old.mountpoint.rstrip("/")
    out: List[str] = []
    for name, bind in state.bind_mounts.items():
        source = bind.source.rstrip("/")
        if source != mountpoint and not source.startswith(mountpoint + "/"):
            continue
        rel = source[len(mountpoint):]
        if not any(os.path.isdir(branch + rel) for branch in remaining):
            out.append(name)
    return sorted(out)
