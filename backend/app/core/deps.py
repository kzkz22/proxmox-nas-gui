"""Cross-feature dependency questions.

The Samba half and the storage half of the application do not import each
other. They meet in exactly one place: a share whose path lives inside a
mergerfs pool would break if that pool were unmounted or deleted. Answering
that question needs to see both halves of the state, so it belongs here, in
the module that already owns State and composes them.

Deliberately not a callback registry. Registration would happen as an import
side effect, so a caller reached before the Samba package was imported would
get an empty answer and the guard would pass - the storage side would then
unmount a pool out from under a live share. A plain function over State is
fail-closed by construction.
"""

from typing import List

from ..models import State


def _covers(root: str, path: str) -> bool:
    """Whether `path` is `root` or lies below it.

    Not a prefix test: /mnt/pool/media2 merely starts with the same characters
    as /mnt/pool/media, and is a different directory.
    """
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def blockers_for_path(state: State, path: str) -> List[str]:
    """Names of shares that would break if `path` stopped existing.

    A share can reach `path` two ways. Directly, by being at or below it. Or
    through a bind mount, which is the entire point of that feature: the
    share is on the presentation tree while the data lives somewhere else
    entirely, so a pool whose mountpoint carries no share at all can still be
    load-bearing for one. Both count, otherwise the delete/unmount guards
    would happily pull the filesystem out from under a live share.

    One level of indirection is enough because a bind mount may not be nested
    inside another one - the API refuses that - so no chain is longer than
    share -> bind -> storage.
    """
    target = path.rstrip("/")
    presented = {target}
    for bind in state.bind_mounts.values():
        source = bind.source.rstrip("/")
        if _covers(target, source):
            presented.add(bind.target)
        elif _covers(source, target):
            # `target` is inside the bind's source, so it also appears at the
            # matching place under the bind's target.
            presented.add(bind.target + target[len(source):])
    return sorted(
        name for name, share in state.shares.items()
        if any(_covers(p, share.path) for p in presented)
    )


def shares_containing_path(state: State, path: str) -> List[str]:
    """Names of shares that `path` sits *inside* of, without being their root.

    The mirror image of blockers_for_path, and deliberately not a blocker.
    The intended bind mount layout is one share on the presentation root with
    every bind hanging underneath it, so treating that share as a blocker
    would make every bind permanently undeletable. Removing one bind there
    leaves an empty folder inside a live share rather than breaking it - worth
    warning about, not worth refusing.
    """
    target = path.rstrip("/")
    return sorted(
        name for name, share in state.shares.items()
        if share.path != target and _covers(share.path, target)
    )
