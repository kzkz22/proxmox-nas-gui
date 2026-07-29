import os
import subprocess
from pathlib import Path
from typing import List, Optional

from ..core.proc import SystemOpError, run
from ..models import State
from . import poolconf
from .models import Pool

FSTAB_BACKUP_SUFFIX = ".psg-backup"
XATTR_CTL = ".mergerfs"


def fstab_path() -> Path:
    return Path(os.environ.get("PSG_FSTAB", "/etc/fstab"))


def read_fstab() -> str:
    p = fstab_path()
    return p.read_text() if p.exists() else ""


def write_fstab(text: str) -> None:
    p = fstab_path()
    backup = p.with_name(p.name + FSTAB_BACKUP_SUFFIX)
    if p.exists() and not backup.exists():
        backup.write_text(p.read_text())
    tmp = p.with_name(p.name + ".psg-tmp")
    tmp.write_text(text)
    os.replace(tmp, p)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=30)


def systemd_dir() -> Path:
    return Path(os.environ.get("PSG_SYSTEMD_DIR", "/etc/systemd/system"))


def _systemctl(*args: str) -> bool:
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
    _systemctl("daemon-reload")
    _systemctl("enable", poolconf.pool_unit_name(pool.name))


def remove_pool_unit(name: str) -> None:
    _systemctl("disable", poolconf.pool_unit_name(name))
    path = systemd_dir() / poolconf.pool_unit_name(name)
    if path.exists():
        path.unlink()
    _systemctl("daemon-reload")


def is_mounted(mountpoint: str) -> bool:
    return os.path.ismount(mountpoint)


def mount_pool(pool: Pool) -> None:
    Path(pool.mountpoint).mkdir(parents=True, exist_ok=True)
    if _systemctl("start", poolconf.pool_unit_name(pool.name)):
        return
    # No systemd (e.g. running in a plain container): invoke mergerfs
    # directly with the exact options the unit would use.
    run([
        "mergerfs", "-o", poolconf.mergerfs_options(pool),
        poolconf.branches_spec(pool), pool.mountpoint,
    ])


def unmount_pool(pool: Pool) -> None:
    if _systemctl("stop", poolconf.pool_unit_name(pool.name)):
        return
    if is_mounted(pool.mountpoint):
        run(["umount", pool.mountpoint])


def mount_disk(mountpoint: str) -> None:
    Path(mountpoint).mkdir(parents=True, exist_ok=True)
    run(["mount", mountpoint])


def unmount_disk(mountpoint: str) -> None:
    if is_mounted(mountpoint):
        run(["umount", mountpoint])


def usage(path: str) -> Optional[dict]:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_frsize * st.f_blocks
    free = st.f_frsize * st.f_bavail
    return {"total": total, "free": free, "used": total - free}


def runtime_update(pool: Pool) -> Optional[str]:
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
    except OSError as exc:
        return (
            "fstab updated, but the live pool could not be reconfigured "
            f"({exc}); changes take effect after a remount"
        )
    return None


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
        out = run([
            "lsblk", "-J", "-b", "-o",
            "PATH,TYPE,SIZE,FSTYPE,UUID,MOUNTPOINT,MODEL,SERIAL,LABEL",
        ])
    except SystemOpError:
        return []
    return poolconf.parse_lsblk(out)


def pools_using_path(state: State, path: str) -> List[str]:
    p = path.rstrip("/")
    return sorted(
        name for name, pool in state.pools.items()
        if any(b.path == p for b in pool.branches)
    )
