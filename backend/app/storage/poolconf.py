"""Pure fstab/mergerfs configuration helpers.

No filesystem or subprocess access here: everything operating on real
/etc/fstab or mounts lives in pools.py, so this core stays unit-testable.

Managed fstab lines are tagged with a trailing comment so they can be
updated or removed without touching anything hand-written:
    ... 0 0 # pnas:pool:<name>
    ... 0 2 # pnas:disk:<name>
"""

import json
import re
from typing import Dict, List, Optional, Tuple

from .models import DiskMount, Pool

TAG_RE = re.compile(r"#\s*pnas:(pool|disk):(\S+)\s*$")

MOUNTABLE_EXCLUDE_FSTYPES = {
    "swap", "LVM2_member", "zfs_member", "crypto_LUKS", "linux_raid_member",
}


def mergerfs_options(pool: Pool) -> str:
    """Build the mergerfs `-o` option string.

    The three cache options are spelled out even when they match mergerfs's
    own defaults, because they are the settings that decide write throughput
    and they are worth reading straight off the generated unit file:

      cache.files      whether the kernel page cache is used at all. With
                       "off" mergerfs runs in direct_io mode - no page cache,
                       no shared mmap, and every I/O is its own round trip
                       into the mergerfs process. Anything that writes in
                       small pieces (a torrent client filling a file 16 KiB
                       at a time) pays that cost per piece.
      cache.writeback  lets the kernel accumulate those small writes in the
                       page cache and hand mergerfs one large request
                       instead. Without it FUSE writes through: a 16 KiB
                       write from the application stays a 16 KiB write.
                       Requires cache.files to be enabled.
      dropcacheonclose drops a file's cached pages when it is closed. Saves
                       RAM (the data would otherwise sit in both mergerfs's
                       and the branch filesystem's cache) at the cost of
                       throwing away cache a re-reader would have used.

    passthrough is the fourth, and only appears once switched on - see
    merged_options() for why. It is the one setting that removes the round
    trip rather than amortising it: the kernel talks to the branch file
    directly, at the cost of moveonenospc.

    Options are merged by key rather than concatenated: if extra_options
    sets a key the GUI also sets (e.g. cache.files), the extra_options
    value wins instead of producing a duplicate `key=value` pair, which
    mergerfs would otherwise receive verbatim with an ambiguous winner.
    """
    return ",".join(
        f"{key}={value}" if value is not None else key
        for key, value in merged_options(pool).items()
    )


def merged_options(pool: Pool) -> Dict[str, Optional[str]]:
    """The effective option map, keyed by option name, in emission order.

    Split out from mergerfs_options() so a caller can ask about one option
    without re-parsing the joined string - the drift check compares individual
    values against what the running mount reports, and extra_options means the
    value for a key is not always the one the matching field holds.

    A valueless option (allow_other) maps to None.
    """
    defaults = [
        "allow_other",
        f"cache.files={pool.cache_files}",
        f"cache.writeback={'true' if pool.cache_writeback else 'false'}",
        f"dropcacheonclose={'true' if pool.dropcacheonclose else 'false'}",
        f"category.create={pool.create_policy}",
        f"minfreespace={pool.minfreespace}",
        f"moveonenospc={'true' if pool.moveonenospc else 'false'}",
        f"fsname={pool.name}",
    ]
    # Only emitted when actually enabled: mergerfs rejects options it does not
    # know, and passthrough arrived in 2.41. Writing passthrough=off would
    # make every pool fail to mount on the 2.40 that Debian 13 still ships.
    if pool.passthrough != "off":
        defaults.insert(4, f"passthrough={pool.passthrough}")
    merged: Dict[str, Optional[str]] = {}
    for opt in defaults + (pool.extra_options.split(",") if pool.extra_options else []):
        key, sep, value = opt.partition("=")
        merged[key] = value if sep else None
    return merged


def branches_spec(pool: Pool) -> str:
    return ":".join(f"{b.path}={b.mode.value}" for b in pool.branches)


def branches_removed(old: Pool, new: Pool) -> List[str]:
    """Branch paths present in `old` but not in `new`."""
    new_paths = {b.path for b in new.branches}
    return [b.path for b in old.branches if b.path not in new_paths]


def pool_unit_name(pool_name: str) -> str:
    return f"pnas-pool-{pool_name}.service"


def pool_unit(pool: Pool) -> str:
    """systemd service that mounts the pool at boot.

    Pools deliberately do not go into fstab: the mount.mergerfs helper of
    the Debian 12 packaged mergerfs 2.33 rejects generic fstab options
    (nofail, x-systemd.*), while a service calling the mergerfs binary
    directly works on every version. RequiresMountsFor gives the same
    boot ordering that branches-mount-timeout / x-systemd would, and a
    failed pool never drops the host into emergency mode.
    """
    branch_paths = " ".join(b.path for b in pool.branches)
    return f"""# Managed by proxmox-nas-gui - DO NOT EDIT.
[Unit]
Description=mergerfs pool {pool.name} (proxmox-nas-gui)
After=local-fs.target
RequiresMountsFor={branch_paths}

[Service]
Type=forking
ExecStartPre=/bin/mkdir -p {pool.mountpoint}
ExecStart=/usr/bin/mergerfs -o {mergerfs_options(pool)} {branches_spec(pool)} {pool.mountpoint}
ExecStop=/bin/umount {pool.mountpoint}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def disk_fstab_line(name: str, disk: DiskMount) -> str:
    return (
        f"UUID={disk.uuid} {disk.mountpoint} {disk.fstype} "
        f"defaults,nofail 0 2 # pnas:disk:{name}"
    )


def parse_tag(line: str) -> Optional[Tuple[str, str]]:
    m = TAG_RE.search(line)
    return (m.group(1), m.group(2)) if m else None


def upsert_line(fstab: str, kind: str, name: str, new_line: str) -> str:
    lines = fstab.splitlines()
    out: List[str] = []
    replaced = False
    for line in lines:
        if parse_tag(line) == (kind, name):
            if not replaced:
                out.append(new_line)
                replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(new_line)
    return "\n".join(out) + "\n"


def remove_line(fstab: str, kind: str, name: str) -> str:
    lines = [line for line in fstab.splitlines() if parse_tag(line) != (kind, name)]
    return ("\n".join(lines) + "\n") if lines else ""


SYSTEM_MOUNTPOINTS = ("/", "/boot", "/boot/efi")


def contains_system_mount(dev: dict) -> bool:
    """True if dev or any descendant is mounted on the running OS itself.

    Used to exclude the whole Proxmox system disk (and every partition/LVM
    volume on it) from the candidate list, not just the mounted leaf - so it
    never shows up as either mountable or formattable, even indirectly via
    an LVM volume group. Public because sleepconf applies the same rule to
    keep the system disk out of the spin-down list.
    """
    mp = dev.get("mountpoint")
    if mp in SYSTEM_MOUNTPOINTS or (mp or "").startswith("/boot/"):
        return True
    return any(contains_system_mount(child) for child in dev.get("children") or [])


def parse_lsblk(output: str) -> List[dict]:
    """Flatten `lsblk -J -b` output into candidate/info entries."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    result: List[dict] = []

    def walk(dev: dict, parent_model: str) -> None:
        model = (dev.get("model") or parent_model or "").strip()
        for child in dev.get("children") or []:
            walk(child, model)
        if dev.get("type") not in ("disk", "part"):
            return
        if dev.get("children"):
            return
        fstype = dev.get("fstype") or ""
        mountpoint = dev.get("mountpoint")
        result.append({
            "path": dev.get("path") or "",
            "type": dev.get("type"),
            "size": dev.get("size") or 0,
            "fstype": fstype,
            "uuid": dev.get("uuid") or "",
            "mountpoint": mountpoint,
            "model": model,
            "serial": (dev.get("serial") or "").strip(),
            "label": dev.get("label") or "",
            "mountable": (
                bool(fstype)
                and mountpoint is None
                and fstype not in MOUNTABLE_EXCLUDE_FSTYPES
                and bool(dev.get("uuid"))
            ),
            "formattable": not fstype and mountpoint is None,
        })

    for dev in data.get("blockdevices", []):
        if contains_system_mount(dev):
            continue
        walk(dev, "")
    return result
