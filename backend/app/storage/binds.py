"""Bind mounts: everything that touches real mounts and unit files.

The pure half - unit text, template expansion, provider lookup - lives in
bindconf.py, mirroring the poolconf.py/pools.py split.
"""

import os
from pathlib import Path
from typing import List, Optional

from ..core import fsops
from ..core.proc import SystemOpError, run
from ..models import State
from . import bindconf, pools
from .models import BindMount


def mount_root(path: str) -> str:
    """The mount the given path sits on.

    Walks up until it finds a mountpoint, so it answers correctly for a path
    that does not exist yet - /mnt/fontos/kz resolves to the ZFS dataset at
    /mnt/fontos whether or not the kz directory has been created. Falls back
    to "/", which means "on the root filesystem": legal, but usually a sign
    that the storage the user expected there is not mounted.
    """
    p = Path(path)
    while True:
        if os.path.ismount(p):
            return str(p)
        if p == p.parent:
            return "/"
        p = p.parent


def write_bind_unit(state: State, bind: BindMount) -> None:
    unit_name = bindconf.bind_unit_name(bind.name)
    path = pools.systemd_dir() / unit_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        bindconf.bind_unit(
            bind, mount_root(bind.source), bindconf.pool_for_path(state.pools, bind.source)
        )
    )
    pools.systemctl("daemon-reload")
    pools.systemctl("enable", unit_name)


def remove_bind_unit(name: str) -> None:
    unit_name = bindconf.bind_unit_name(name)
    pools.systemctl("disable", unit_name)
    path = pools.systemd_dir() / unit_name
    if path.exists():
        path.unlink()
    pools.systemctl("daemon-reload")


def mount_bind(bind: BindMount) -> None:
    Path(bind.target).mkdir(parents=True, exist_ok=True)
    if pools.has_systemd():
        # Deliberately not best-effort, unlike mount_pool: the unit's
        # ExecStartPre guard exists to refuse the mount when the source's
        # filesystem is missing, and falling back to a plain mount --bind
        # after that refusal would bind the empty directory the guard was
        # protecting against.
        run(["systemctl", "start", bindconf.bind_unit_name(bind.name)], timeout=60)
        return
    # No systemd (a plain container, or the dev server): do what the unit
    # would have done, including the source check.
    if not os.path.isdir(bind.source):
        raise SystemOpError(f"bind source is not a directory: {bind.source}")
    run(["mount", "--bind", bind.source, bind.target])
    if bind.read_only:
        run(["mount", "-o", "remount,bind,ro", bind.target])


def unmount_bind(bind: BindMount) -> Optional[str]:
    """Unmount, returning a warning string instead of raising.

    A target the users are actively browsing over Samba is busy, and failing
    the whole request would leave state.json and the unit files disagreeing
    with each other over something the user can simply retry.
    """
    if pools.has_systemd():
        pools.systemctl("stop", bindconf.bind_unit_name(bind.name))
    if pools.is_mounted(bind.target):
        try:
            run(["umount", bind.target])
        except SystemOpError as exc:
            return str(exc)
    return None


def create_source(bind: BindMount) -> None:
    """Create the source directory, as a ZFS dataset when it belongs in one.

    A dataset rather than a plain directory whenever the parent is one, so
    the per-folder snapshots and quotas people set up ZFS for stay possible.

    Ownership/mode are then set the same way a share root gets them (see
    fsops.apply_share_perms): a plain mkdir is root:root 0755, which Samba's
    "force user = nobody" can list but not write into - so without this a
    freshly (re)created presentation folder would look empty and read-only
    to every client even though the mount succeeded.
    """
    if os.path.isdir(bind.source):
        return
    source = Path(bind.source)
    parent = str(source.parent)
    fsops.make_dir(parent, source.name, dataset=parent in fsops.zfs_datasets())
    fsops.apply_share_perms(bind.source)


def bind_info(state: State, bind: BindMount) -> dict:
    """One bind plus everything observed live rather than stored."""
    source_mount = mount_root(bind.source)
    mounted = pools.is_mounted(bind.target)
    return {
        **bind.model_dump(),
        "mounted": mounted,
        "source_exists": os.path.isdir(bind.source),
        "source_mount": source_mount,
        "backing_pool": bindconf.pool_for_path(state.pools, bind.source),
        "source_on_root_fs": source_mount == "/",
        "usage": pools.usage(bind.target) if mounted else None,
    }


def binds_using_path(state: State, path: str) -> List[str]:
    """Names of bind mounts whose source is at or below `path`.

    The mirror of pools.pools_using_path: unmounting a pool or a disk that a
    bind mount presents elsewhere would hollow out that presentation tree.
    """
    p = path.rstrip("/")
    prefix = p + "/"
    return sorted(
        name for name, bind in state.bind_mounts.items()
        if bind.source == p or bind.source.startswith(prefix)
    )


def remount_pool_with_binds(state: State, pool) -> List[str]:
    """Remount a pool and bring its bind mounts back up. Returns their names.

    The two halves belong together and are never wanted apart: the bind units
    declare Requires= on the pool service, so stopping the pool takes them
    down with it, and nothing brings them back on its own. A bind left down
    means Samba serving an empty directory over what still looks like a
    working share - the exact failure the bind units exist to prevent.

    Lives here rather than in pools.py because pools.py cannot see bind
    mounts (binds imports pools, not the other way round), and both the
    diagnostics fix and the pool editor's remount need it.
    """
    pools.write_pool_unit(pool)
    pools.remount_pool(pool)
    restarted = []
    for name, bind in sorted(state.bind_mounts.items()):
        if bindconf.pool_for_path(state.pools, bind.source) == pool.name:
            mount_bind(bind)
            restarted.append(name)
    return restarted
