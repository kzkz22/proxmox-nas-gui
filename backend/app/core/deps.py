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


def blockers_for_path(state: State, path: str) -> List[str]:
    """Names of shares that would break if `path` stopped existing.

    Matches the path itself and anything below it, but not siblings that
    merely start with the same characters: /mnt/pool/media2 is unaffected by
    /mnt/pool/media going away.
    """
    target = path.rstrip("/")
    prefix = target + "/"
    return sorted(
        name for name, share in state.shares.items()
        if share.path == target or share.path.startswith(prefix)
    )
