"""Pure bind-mount configuration helpers.

No filesystem or subprocess access here: everything that touches real mounts
or unit files lives in binds.py, so this core stays unit-testable.

Bind mounts get a generated systemd unit rather than an fstab line, for a
reason fstab cannot solve: a mergerfs pool is mounted by
pnas-pool-<name>.service, not by a .mount unit, so fstab's
x-systemd.requires-mounts-for= has nothing to order against. A unit can name
the pool service directly - and can carry the guard that matters most, see
bind_unit() below.
"""

from typing import Dict, List, Optional

from . import poolconf
from .models import BindMount, Pool

MOUNTPOINT = "/usr/bin/mountpoint"
TEST = "/usr/bin/test"
MKDIR = "/bin/mkdir"
MOUNT = "/bin/mount"
UMOUNT = "/bin/umount"


def bind_unit_name(name: str) -> str:
    return f"pnas-bind-{name}.service"


def pool_for_path(pools: Dict[str, Pool], path: str) -> Optional[str]:
    """Name of the GUI-managed mergerfs pool `path` lives in, if any.

    The longest match wins, so a pool mounted inside another pool's tree is
    still attributed to the inner one.
    """
    best: Optional[str] = None
    best_len = -1
    target = path.rstrip("/")
    for name, pool in pools.items():
        mp = pool.mountpoint.rstrip("/")
        if (target == mp or target.startswith(mp + "/")) and len(mp) > best_len:
            best, best_len = name, len(mp)
    return best


def bind_unit(
    bind: BindMount, source_mount: str, pool_name: Optional[str] = None
) -> str:
    """systemd service that establishes one bind mount at boot.

    `source_mount` is the mount the source directory actually sits on - the
    ZFS dataset, the managed disk mount, or the mergerfs pool. It is what the
    ExecStartPre guard checks, and that guard is the whole point of this
    feature: without it, a source whose filesystem is not mounted yet would
    still bind successfully, onto the *empty* directory underneath. Samba
    would then serve an empty share, and a sync client pointed at it could
    propagate the emptiness as deletions.

    Ordering is expressed two different ways on purpose. For a mergerfs pool
    the provider is a service, so Requires=/After= names it directly;
    RequiresMountsFor= would resolve to a .mount unit that does not exist
    until something has already mounted it. For everything else (ZFS, a
    managed disk mount, a plain filesystem) RequiresMountsFor= is exactly
    right.
    """
    unit = [
        "# Managed by proxmox-nas-gui - DO NOT EDIT.",
        "[Unit]",
        f"Description=bind mount {bind.target} (proxmox-nas-gui)",
        "After=local-fs.target",
    ]
    if pool_name:
        pool_service = poolconf.pool_unit_name(pool_name)
        unit += [f"Requires={pool_service}", f"After={pool_service}"]
    elif source_mount != "/":
        unit.append(f"RequiresMountsFor={source_mount}")

    unit += ["", "[Service]", "Type=oneshot", "RemainAfterExit=yes"]
    if source_mount != "/":
        unit.append(f"ExecStartPre={MOUNTPOINT} -q {source_mount}")
    unit += [
        f"ExecStartPre={TEST} -d {bind.source}",
        f"ExecStartPre={MKDIR} -p {bind.target}",
        f"ExecStart={MOUNT} --bind {bind.source} {bind.target}",
    ]
    if bind.read_only:
        # A bind mount inherits the source's options; read-only needs a
        # second call, and "bind" must be repeated or the remount would apply
        # to the underlying filesystem instead.
        unit.append(f"ExecStart={MOUNT} -o remount,bind,ro {bind.target}")
    unit += [
        f"ExecStop={UMOUNT} {bind.target}",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(unit)


def generate_tree(
    root: str, folders: List[str], tiers: List[tuple]
) -> List[BindMount]:
    """Expand a presentation-tree template into the bind mounts it needs.

    `tiers` is a list of (label, source_root) pairs. Every folder gets one
    subdirectory per tier, backed by the same-named directory under that
    tier's source root:

        root=/mnt/family_pool, folders=[kz], tiers=[("fontos", "/mnt/fontos")]
        -> /mnt/fontos/kz  ->  /mnt/family_pool/kz/fontos

    Which is the whole reason the feature exists: the user browses one tree
    while the tiers live on different physical storage.
    """
    out: List[BindMount] = []
    for folder in folders:
        for label, source_root in tiers:
            out.append(
                BindMount(
                    name=f"{folder}-{label}",
                    source=f"{source_root.rstrip('/')}/{folder}",
                    target=f"{root.rstrip('/')}/{folder}/{label}",
                )
            )
    return out
