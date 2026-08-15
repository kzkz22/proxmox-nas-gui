"""Bind mounts: the presentation tree.

A bind mount here answers one question the rest of the storage page cannot:
"where should the users see this?". The physical layout (a ZFS dataset for
important data, a mergerfs pool for bulk) does not have to be the layout the
Samba clients browse, and without bind mounts every backing filesystem forces
another share on the user.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core import fsops, state as state_store
from ...core.deps import blockers_for_path, shares_containing_path
from ...models import State
from .. import bindconf, binds, pools
from ..models import BindMount, POOL_NAME_RE

router = APIRouter(prefix="/binds", tags=["binds"])


class Tier(BaseModel):
    """One level of the generated tree - a label and where it is backed."""

    label: str
    source_root: str


class TreeRequest(BaseModel):
    root: str
    folders: List[str] = Field(min_length=1)
    tiers: List[Tier] = Field(min_length=1)


class BulkRequest(BaseModel):
    binds: List[BindMount] = Field(min_length=1)
    create_sources: bool = False


def _segment_ok(value: str) -> bool:
    """Folder names and tier labels become both path segments and the bind
    mount's name, so they get the same character set as a pool name."""
    return bool(POOL_NAME_RE.match(value))


def _conflict(
    st: State, bind: BindMount, ignore: Optional[str] = None,
    siblings: Tuple[Tuple[str, BindMount], ...] = (),
) -> Optional[str]:
    """Why `bind` cannot be created, or None.

    Non-raising so the tree preview can show every problem at once instead of
    dying on the first one.
    """
    others: List[Tuple[str, BindMount]] = [
        (name, b) for name, b in st.bind_mounts.items() if name != ignore
    ]
    others += [(name, b) for name, b in siblings if name != bind.name]

    for other_name, other in others:
        if bind.target == other.target:
            return f"target is already used by bind mount {other_name}"
        if (bind.target + "/").startswith(other.target + "/") or (
            other.target + "/"
        ).startswith(bind.target + "/"):
            # Nested binds would make mount and unmount order significant,
            # and one level of indirection is what blockers_for_path assumes.
            return f"target is nested with bind mount {other_name}"
        if bind.target == other.source:
            return f"target is the source of bind mount {other_name}"
        if bind.source == other.target:
            return f"source is the target of bind mount {other_name}"

    for pool_name, pool in st.pools.items():
        if bind.target == pool.mountpoint:
            return f"target is the mountpoint of pool {pool_name}"
        if (bind.target + "/").startswith(pool.mountpoint + "/"):
            # mergerfs is a FUSE filesystem; a bind mounted into its tree is
            # not something the pool itself can see or manage.
            return f"target is inside mergerfs pool {pool_name}"

    for mount_name, dm in st.disk_mounts.items():
        if bind.target == dm.mountpoint:
            return f"target is the mountpoint of disk mount {mount_name}"

    branch_of = pools.pools_using_path(st, bind.target)
    if branch_of:
        return "target is a branch of pool(s): " + ", ".join(branch_of)
    return None


def _require_no_conflict(
    st: State, bind: BindMount, ignore: Optional[str] = None,
    siblings: Tuple[Tuple[str, BindMount], ...] = (),
) -> None:
    reason = _conflict(st, bind, ignore, siblings)
    if reason:
        raise HTTPException(409, reason)


def _create_source(bind: BindMount) -> None:
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


def _empty_folder_warning(st: State, bind: BindMount) -> Optional[str]:
    """Tell the user when unmounting leaves a hole in a share they still run.

    Not a refusal - see deps.shares_containing_path for why.
    """
    covering = shares_containing_path(st, bind.target)
    if not covering:
        return None
    return (
        f"{bind.target} is now an empty folder inside share(s): "
        + ", ".join(covering)
    )


def _apply(st: State, bind: BindMount) -> Optional[str]:
    """Install the unit and mount, returning a warning rather than failing
    when only the mount itself did not work - the configuration is saved
    either way, and the user can retry from the list."""
    binds.write_bind_unit(st, bind)
    try:
        binds.mount_bind(bind)
    except Exception as exc:
        return f"bind mount saved, but mounting failed: {exc}"
    return None


@router.get("")
def list_binds():
    st = state_store.load_state()
    return {
        "binds": {name: binds.bind_info(st, b) for name, b in st.bind_mounts.items()}
    }


@router.post("/plan")
def plan_tree(body: TreeRequest):
    """Expand a tree template into bind mounts without touching anything.

    Drives the generator's live preview: the user sees all six rows, which
    sources do not exist yet, and any conflict, before committing.
    """
    for folder in body.folders:
        if not _segment_ok(folder):
            raise HTTPException(400, f"invalid folder name: {folder}")
    for tier in body.tiers:
        if not _segment_ok(tier.label):
            raise HTTPException(400, f"invalid tier label: {tier.label}")

    try:
        planned = bindconf.generate_tree(
            body.root, body.folders,
            [(tier.label, tier.source_root) for tier in body.tiers],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    st = state_store.load_state()
    siblings = tuple((b.name, b) for b in planned)
    rows = []
    for bind in planned:
        conflict = _conflict(st, bind, siblings=siblings)
        if not conflict and bind.name in st.bind_mounts:
            conflict = "a bind mount with this name already exists"
        rows.append({
            **bind.model_dump(),
            "source_exists": os.path.isdir(bind.source),
            "source_mount": binds.mount_root(bind.source),
            "backing_pool": bindconf.pool_for_path(st.pools, bind.source),
            "conflict": conflict,
        })
    return {"binds": rows}


@router.post("/bulk")
def create_binds(body: BulkRequest):
    """Create a whole generated tree at once.

    Every entry is validated before any of them is applied, so a template with
    one bad row does not leave half a tree behind.
    """
    with state_store.lock:
        st = state_store.load_state()
        siblings = tuple((b.name, b) for b in body.binds)
        seen: Dict[str, BindMount] = {}
        for bind in body.binds:
            if bind.name in st.bind_mounts or bind.name in seen:
                raise HTTPException(409, f"bind mount already exists: {bind.name}")
            seen[bind.name] = bind
            _require_no_conflict(st, bind, siblings=siblings)

        warnings: List[str] = []
        for bind in body.binds:
            if body.create_sources:
                _create_source(bind)
            st.bind_mounts[bind.name] = bind
            warning = _apply(st, bind)
            if warning:
                warnings.append(warning)
        state_store.save_state(st)
        return {"ok": True, "warning": "; ".join(warnings) or None}


@router.post("")
def create_bind(bind: BindMount, create_source: bool = False):
    with state_store.lock:
        st = state_store.load_state()
        if bind.name in st.bind_mounts:
            raise HTTPException(409, "bind mount already exists")
        _require_no_conflict(st, bind)
        if create_source:
            _create_source(bind)
        st.bind_mounts[bind.name] = bind
        warning = _apply(st, bind)
        state_store.save_state(st)
        return {"ok": True, "warning": warning}


@router.put("/{name}")
def update_bind(name: str, bind: BindMount, create_source: bool = False):
    if bind.name != name:
        raise HTTPException(400, "bind mount name cannot be changed")
    with state_store.lock:
        st = state_store.load_state()
        old = st.bind_mounts.get(name)
        if not old:
            raise HTTPException(404, "no such bind mount")
        _require_no_conflict(st, bind, ignore=name)
        if old.target != bind.target:
            deps = blockers_for_path(st, old.target)
            if deps:
                raise HTTPException(
                    409, "shares depend on this bind mount's target: " + ", ".join(deps)
                )
        # Even an unchanged target has to come down first: the source or the
        # read-only flag may have changed, and a bind cannot be re-pointed in
        # place.
        binds.unmount_bind(old)
        if create_source:
            _create_source(bind)
        st.bind_mounts[name] = bind
        warning = _apply(st, bind)
        state_store.save_state(st)
        return {"ok": True, "warning": warning}


@router.delete("/{name}")
def delete_bind(name: str):
    """Remove the bind mount. The target directory is left in place, empty -
    same promise as everywhere else in the app: no user data is deleted."""
    with state_store.lock:
        st = state_store.load_state()
        bind = st.bind_mounts.get(name)
        if not bind:
            raise HTTPException(404, "no such bind mount")
        deps = blockers_for_path(st, bind.target)
        if deps:
            raise HTTPException(409, "shares depend on this bind mount: " + ", ".join(deps))
        warnings = [w for w in (binds.unmount_bind(bind), _empty_folder_warning(st, bind)) if w]
        binds.remove_bind_unit(name)
        del st.bind_mounts[name]
        state_store.save_state(st)
        return {"ok": True, "warning": "; ".join(warnings) or None}


@router.post("/{name}/mount")
def mount_bind(name: str):
    st = state_store.load_state()
    bind = st.bind_mounts.get(name)
    if not bind:
        raise HTTPException(404, "no such bind mount")
    binds.mount_bind(bind)
    return {"ok": True}


@router.post("/{name}/unmount")
def unmount_bind(name: str):
    st = state_store.load_state()
    bind = st.bind_mounts.get(name)
    if not bind:
        raise HTTPException(404, "no such bind mount")
    deps = blockers_for_path(st, bind.target)
    if deps:
        raise HTTPException(409, "shares depend on this bind mount: " + ", ".join(deps))
    warnings = [w for w in (binds.unmount_bind(bind), _empty_folder_warning(st, bind)) if w]
    return {"ok": True, "warning": "; ".join(warnings) or None}
