"""Proactive health checks across both feature halves.

Composition root, like state_view.py: a check here can legitimately need a
pool's branches, a bind mount's source, a share's path, or a disk's sleep
warnings, and none of those live in one package alone. Every check returns
plain finding dicts - {"id", "category", "entity", "severity", "fixable",
"vars", "command"} - the same shape disksleep.warnings_for() already uses for
a single disk, widened with "category" (for grouping in the UI) and "entity"
(the pool/bind/share/disk name a fix targets), so the frontend can render and
FIXABLE dispatch can find both automatically.

Two problems this exists to catch before they are found by hand: a bind mount
source that has quietly become unwritable (root:root 0755 instead of the
nobody:nogroup 0777 every presentation folder needs - see fsops.apply_share_
perms), and a bind mount source that currently lives on only one branch of its
pool, so removing that one disk would make it - and everything the bind mount
shows through it - vanish from the mergerfs union with nothing actually
deleted. Both happened once already; see storage/pools.py orphaned_binds()
and storage/api/binds.py's create_source() calling apply_share_perms.
"""

import grp
import os
import pwd
import stat
from typing import Callable, Dict, List

from .core import fsops
from .core.proc import SystemOpError
from .models import State
from .storage import bindconf, disksleep, mergerfs_env, poolconf
from .storage import binds as bind_ops
from .storage import pools as pool_ops
from .storage.models import BindMount

_SEVERITY_ORDER = {"crit": 0, "warn": 1, "info": 2}


def _finding(
    id_: str, category: str, entity: str, severity: str,
    fixable: bool, vars_: dict, command: str = "",
) -> dict:
    return {
        "id": id_, "category": category, "entity": entity,
        "severity": severity, "fixable": fixable, "vars": vars_,
        "command": command,
    }


def _wrong_perms(path: str) -> bool:
    """True when `path` does not already have what apply_share_perms would
    set: owned nobody:nogroup, mode 0777."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    try:
        want_uid = pwd.getpwnam("nobody").pw_uid
        want_gid = grp.getgrnam("nogroup").gr_gid
    except KeyError:
        return False
    if st.st_uid != want_uid or st.st_gid != want_gid:
        return True
    return stat.S_IMODE(st.st_mode) != 0o777


# --- checks ------------------------------------------------------------------

def _mergerfs_checks(state: State) -> List[dict]:
    """Whether the machine is leaving mergerfs performance on the table.

    Only asked when there is a pool to care about, and only reported when both
    version numbers are actually known - "cannot tell" must stay silent rather
    than advise an upgrade that might be a downgrade.
    """
    if not state.pools:
        return []
    caps = mergerfs_env.capabilities()
    if caps["passthrough_missing"] != "mergerfs":
        return []
    return [_finding(
        "mergerfs_outdated", "pools", "mergerfs", "info", False,
        {
            "installed": caps["mergerfs_version"],
            "needed": mergerfs_env.version_text(mergerfs_env.PASSTHROUGH_MIN_MERGERFS),
            "kernel": caps["kernel_version"],
        },
        command=(
            "wget https://github.com/trapexit/mergerfs/releases/latest && "
            "dpkg -i mergerfs_<version>.debian-<codename>_<arch>.deb"
        ),
    )]


def _pool_checks(state: State) -> List[dict]:
    out: List[dict] = []
    caps = mergerfs_env.capabilities() if state.pools else {"passthrough": False}
    for name, pool in sorted(state.pools.items()):
        for branch in pool.branches:
            if not os.path.isdir(branch.path):
                out.append(_finding(
                    "pool_branch_missing", "pools", name, "crit", False,
                    {"pool": name, "path": branch.path},
                ))
        if not pool_ops.is_mounted(pool.mountpoint):
            out.append(_finding(
                "pool_not_mounted", "pools", name, "warn", True,
                {"pool": name, "mountpoint": pool.mountpoint},
                command=f"systemctl start {poolconf.pool_unit_name(name)}",
            ))
        # Reported off the effective option string rather than the field, so
        # a cache.files=off typed into extra_options is caught too - that is
        # how the setting usually ends up back on after being changed.
        if "cache.files=off" in poolconf.mergerfs_options(pool):
            out.append(_finding(
                "pool_cache_files_off", "pools", name, "warn", False,
                {"pool": name, "mountpoint": pool.mountpoint},
            ))
        drift = pool_ops.option_drift(pool)
        if drift:
            out.append(_finding(
                "pool_needs_remount", "pools", name, "warn", True,
                {"pool": name, "mountpoint": pool.mountpoint,
                 "options": ", ".join(drift)},
                command=f"systemctl restart {poolconf.pool_unit_name(name)}",
            ))
        if caps["passthrough"] and pool.passthrough == "off":
            out.append(_finding(
                "pool_passthrough_available", "pools", name, "info", True,
                {"pool": name, "mountpoint": pool.mountpoint},
            ))
    return out


def _single_branch_findings(state: State, name: str, bind: BindMount) -> List[dict]:
    """Proactive version of pools.orphaned_binds(): does this bind's source
    currently exist on only one branch of its backing pool, right now - with
    no branch removal needed to see the problem?"""
    pool_name = bindconf.pool_for_path(state.pools, bind.source)
    if not pool_name:
        return []
    pool = state.pools[pool_name]
    if len(pool.branches) < 2:
        return []
    mountpoint = pool.mountpoint.rstrip("/")
    rel = bind.source.rstrip("/")[len(mountpoint):]
    present_on = [b.path for b in pool.branches if os.path.isdir(b.path + rel)]
    if len(present_on) != 1:
        return []
    return [_finding(
        "bind_source_single_branch", "binds", name, "warn", False,
        {"bind": name, "pool": pool_name, "branch": present_on[0]},
    )]


def _bind_checks(state: State) -> List[dict]:
    out: List[dict] = []
    for name, bind in sorted(state.bind_mounts.items()):
        info = bind_ops.bind_info(state, bind)
        if not info["source_exists"]:
            out.append(_finding(
                "bind_source_missing", "binds", name, "crit", True,
                {"bind": name, "path": bind.source},
                command=(
                    f"mkdir -p {bind.source} && "
                    f"chown nobody:nogroup {bind.source} && chmod 0777 {bind.source}"
                ),
            ))
            continue
        if _wrong_perms(bind.source):
            out.append(_finding(
                "bind_source_wrong_perms", "binds", name, "warn", True,
                {"bind": name, "path": bind.source},
                command=f"chown nobody:nogroup {bind.source} && chmod 0777 {bind.source}",
            ))
        if not info["mounted"]:
            out.append(_finding(
                "bind_not_mounted", "binds", name, "warn", True,
                {"bind": name, "target": bind.target},
                command=f"systemctl start {bindconf.bind_unit_name(name)}",
            ))
        out.extend(_single_branch_findings(state, name, bind))
    return out


def _share_checks(state: State) -> List[dict]:
    out: List[dict] = []
    for name, share in sorted(state.shares.items()):
        if not os.path.isdir(share.path):
            out.append(_finding(
                "share_path_missing", "shares", name, "crit", False,
                {"share": name, "path": share.path},
            ))
            continue
        if _wrong_perms(share.path):
            out.append(_finding(
                "share_wrong_perms", "shares", name, "warn", True,
                {"share": name, "path": share.path},
                command=f"chown nobody:nogroup {share.path} && chmod 0777 {share.path}",
            ))
        for user in sorted(u for u in share.user_access if u not in state.users):
            out.append(_finding(
                "share_unknown_user", "shares", name, "warn", False,
                {"share": name, "user": user},
            ))
        for group in sorted(g for g in share.group_access if g not in state.groups):
            out.append(_finding(
                "share_unknown_group", "shares", name, "warn", False,
                {"share": name, "group": group},
            ))
    return out


def _mount_checks(state: State) -> List[dict]:
    out: List[dict] = []
    known_uuids = {d["uuid"] for d in pool_ops.list_block_devices() if d["uuid"]}
    for name, dm in sorted(state.disk_mounts.items()):
        if dm.uuid not in known_uuids:
            out.append(_finding(
                "disk_mount_uuid_missing", "mounts", name, "crit", False,
                {"name": name, "uuid": dm.uuid},
            ))
            continue
        if not pool_ops.is_mounted(dm.mountpoint):
            out.append(_finding(
                "disk_mount_not_mounted", "mounts", name, "warn", True,
                {"name": name, "mountpoint": dm.mountpoint},
                command=f"mount {dm.mountpoint}",
            ))
    return out


def _unit_checks(state: State) -> List[dict]:
    """Pool units get a full content diff against what poolconf.pool_unit()
    would generate now - pure fn of the Pool model, so the diff is reliable.
    Bind units deliberately do not: bindconf.bind_unit() bakes in
    binds.mount_root()'s live answer, which depends on what happens to be
    mounted at check time, so a content diff would false-positive whenever
    the bind's backing disk is transiently unmounted. Existence is still a
    reliable, live-state-independent check for both."""
    out: List[dict] = []
    systemd_dir = pool_ops.systemd_dir()
    for name, pool in sorted(state.pools.items()):
        path = systemd_dir / poolconf.pool_unit_name(name)
        command = (
            f"systemctl daemon-reload && "
            f"systemctl restart {poolconf.pool_unit_name(name)}"
        )
        if not path.exists():
            out.append(_finding(
                "pool_unit_missing", "units", name, "crit", True,
                {"pool": name}, command=command,
            ))
        elif path.read_text() != poolconf.pool_unit(pool):
            out.append(_finding(
                "pool_unit_drift", "units", name, "warn", True,
                {"pool": name}, command=command,
            ))
    for name, bind in sorted(state.bind_mounts.items()):
        path = systemd_dir / bindconf.bind_unit_name(name)
        if not path.exists():
            out.append(_finding(
                "bind_unit_missing", "units", name, "crit", True,
                {"bind": name},
                command=(
                    f"systemctl daemon-reload && "
                    f"systemctl restart {bindconf.bind_unit_name(name)}"
                ),
            ))
    return out


def _disk_checks(state: State) -> List[dict]:
    """Folds in disksleep's existing per-disk warnings verbatim - no
    duplicated logic, no duplicated i18n strings (the frontend reuses the
    sleepwarn.* keys for category == "disks")."""
    disks = [d for d in disksleep.list_sleep_disks() if d["rotational"]]
    described = disksleep.describe(state, disks)
    return [
        {**w, "category": "disks", "entity": by_id}
        for by_id, info in described.items()
        for w in info["warnings"]
    ]


def run_all(state: State) -> List[dict]:
    findings = [
        *_mergerfs_checks(state), *_pool_checks(state), *_bind_checks(state),
        *_share_checks(state), *_mount_checks(state), *_unit_checks(state),
        *_disk_checks(state),
    ]
    return sorted(findings, key=lambda f: (
        _SEVERITY_ORDER.get(f["severity"], 3), f["category"], f["id"], f["entity"],
    ))


# --- one-click fixes ---------------------------------------------------------

def _fix_pool_not_mounted(state: State, entity: str) -> str:
    pool = state.pools.get(entity)
    if not pool:
        raise SystemOpError(f"no such pool: {entity}")
    pool_ops.mount_pool(pool)
    return f"pool {entity} mounted"


def _fix_bind_not_mounted(state: State, entity: str) -> str:
    bind = state.bind_mounts.get(entity)
    if not bind:
        raise SystemOpError(f"no such bind mount: {entity}")
    bind_ops.mount_bind(bind)
    return f"bind mount {entity} mounted"


def _fix_bind_source_missing(state: State, entity: str) -> str:
    bind = state.bind_mounts.get(entity)
    if not bind:
        raise SystemOpError(f"no such bind mount: {entity}")
    bind_ops.create_source(bind)
    return f"created {bind.source}"


def _fix_bind_source_wrong_perms(state: State, entity: str) -> str:
    bind = state.bind_mounts.get(entity)
    if not bind:
        raise SystemOpError(f"no such bind mount: {entity}")
    fsops.apply_share_perms(bind.source)
    return f"ownership fixed on {bind.source}"


def _fix_share_wrong_perms(state: State, entity: str) -> str:
    share = state.shares.get(entity)
    if not share:
        raise SystemOpError(f"no such share: {entity}")
    fsops.apply_share_perms(share.path)
    return f"ownership fixed on {share.path}"


def _fix_disk_mount_not_mounted(state: State, entity: str) -> str:
    dm = state.disk_mounts.get(entity)
    if not dm:
        raise SystemOpError(f"no such disk mount: {entity}")
    pool_ops.mount_disk(dm.mountpoint)
    return f"mounted {dm.mountpoint}"


def _fix_pool_unit(state: State, entity: str) -> str:
    pool = state.pools.get(entity)
    if not pool:
        raise SystemOpError(f"no such pool: {entity}")
    pool_ops.write_pool_unit(pool)
    if not pool_ops.is_mounted(pool.mountpoint):
        pool_ops.mount_pool(pool)
    return f"systemd unit rewritten for pool {entity}"


def _fix_bind_unit(state: State, entity: str) -> str:
    bind = state.bind_mounts.get(entity)
    if not bind:
        raise SystemOpError(f"no such bind mount: {entity}")
    bind_ops.write_bind_unit(state, bind)
    if not pool_ops.is_mounted(bind.target):
        bind_ops.mount_bind(bind)
    return f"systemd unit rewritten for bind mount {entity}"


def _restart_binds_on(state: State, pool_name: str) -> List[str]:
    """Bring back up every bind mount whose source is in this pool.

    Required after any pool remount: the bind units declare Requires= on the
    pool service, so stopping the pool takes them down with it, and nothing
    brings them back on its own. A bind left down means Samba serving an empty
    directory over what looks like a working share.
    """
    restarted = []
    for name, bind in sorted(state.bind_mounts.items()):
        if bindconf.pool_for_path(state.pools, bind.source) == pool_name:
            bind_ops.mount_bind(bind)
            restarted.append(name)
    return restarted


def _remount_pool(state: State, entity: str) -> str:
    pool = state.pools.get(entity)
    if not pool:
        raise SystemOpError(f"no such pool: {entity}")
    pool_ops.remount_pool(pool)
    restarted = _restart_binds_on(state, entity)
    detail = f"pool {entity} remounted"
    if restarted:
        detail += f"; bind mounts restarted: {', '.join(restarted)}"
    return detail


def _fix_pool_passthrough(state: State, entity: str) -> str:
    """Turn on IO passthrough and remount, adjusting what it is incompatible
    with. Mutates the Pool, so this id is in MUTATES_STATE and the caller
    persists the result - see diagnostics_api.fix.

    moveonenospc has to go: under passthrough the kernel writes to the branch
    file directly, so mergerfs never sees an ENOSPC to react to and the option
    is a promise it can no longer keep. Better to have the setting say so than
    to leave it on and silently inert.
    """
    pool = state.pools.get(entity)
    if not pool:
        raise SystemOpError(f"no such pool: {entity}")
    changed = ["passthrough=rw"]
    pool.passthrough = "rw"
    if pool.cache_files == "off":
        pool.cache_files = "auto-full"
        changed.append("cache.files=auto-full")
    if pool.cache_writeback:
        pool.cache_writeback = False
        changed.append("cache.writeback=false")
    if pool.moveonenospc:
        pool.moveonenospc = False
        changed.append("moveonenospc=false")
    pool_ops.write_pool_unit(pool)
    if pool_ops.is_mounted(pool.mountpoint):
        pool_ops.remount_pool(pool)
        _restart_binds_on(state, entity)
    else:
        pool_ops.mount_pool(pool)
    return f"pool {entity}: {', '.join(changed)}; remounted"


FIXABLE: Dict[str, Callable[[State, str], str]] = {
    "pool_not_mounted": _fix_pool_not_mounted,
    "bind_not_mounted": _fix_bind_not_mounted,
    "bind_source_missing": _fix_bind_source_missing,
    "bind_source_wrong_perms": _fix_bind_source_wrong_perms,
    "share_wrong_perms": _fix_share_wrong_perms,
    "disk_mount_not_mounted": _fix_disk_mount_not_mounted,
    "pool_unit_missing": _fix_pool_unit,
    "pool_unit_drift": _fix_pool_unit,
    "bind_unit_missing": _fix_bind_unit,
    "pool_needs_remount": _remount_pool,
    "pool_passthrough_available": _fix_pool_passthrough,
}

# Fixes that change the saved configuration rather than only acting on the
# running system. The HTTP layer holds the state lock and writes the result
# back for these; everything else leaves state.json alone.
MUTATES_STATE = frozenset({"pool_passthrough_available"})


def is_fixable(finding_id: str) -> bool:
    return finding_id in FIXABLE or finding_id in disksleep.FIXABLE


def mutates_state(finding_id: str) -> bool:
    return finding_id in MUTATES_STATE


def apply_fix(state: State, finding_id: str, entity: str) -> str:
    """Delegates disk-category ids to disksleep's own whitelist and
    by-id-keyed disks, so its fixes are not reimplemented here."""
    if finding_id in disksleep.FIXABLE:
        disk = next(
            (d for d in disksleep.list_sleep_disks() if d["by_id"] == entity), None,
        )
        if not disk:
            raise SystemOpError(f"no such disk: {entity}")
        return disksleep.apply_fix(state, finding_id, disk)
    handler = FIXABLE.get(finding_id)
    if not handler:
        raise SystemOpError(f"not a fixable check: {finding_id}")
    return handler(state, entity)
