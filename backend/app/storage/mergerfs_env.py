"""What the installed mergerfs and the running kernel can actually do.

Kept apart from poolconf.py (pure config) and pools.py (acts on mounts)
because this answers a third kind of question: not "what should this pool's
options be" nor "apply them", but "which options would this machine even
honour". The pool editor and the diagnostics page both need that answer, and
neither should be shelling out for it itself.

The one capability that currently matters is FUSE IO passthrough, which needs
*both* halves to be new enough - kernel 6.9 brought the feature, mergerfs
2.41.0 started using it - and which is worth singling out because it is the
difference between paying the FUSE round trip on every read and write and not
paying it at all. A machine with a new kernel and a distro-packaged mergerfs
(Debian 13 ships 2.40.2) has the kernel half and not the other, which looks
like nothing being wrong at all unless something goes looking.
"""

import os
import re
from typing import Optional, Tuple

from ..core.proc import run_unchecked

Version = Tuple[int, ...]

PASSTHROUGH_MIN_MERGERFS: Version = (2, 41, 0)
PASSTHROUGH_MIN_KERNEL: Version = (6, 9)

# Debian's package version ("2.40.2-5") and mergerfs's own -V output
# ("mergerfs v2.41.0") both start with the upstream version; a package built
# from a release tarball with no git metadata prints "vunknown" instead, which
# is why the package manager is asked first.
_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str) -> Optional[Version]:
    m = _VERSION_RE.search(text or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def installed_version() -> Optional[Version]:
    """Version of the mergerfs binary, or None when it cannot be determined.

    None is a real answer, not an error: on a non-dpkg system with a tarball
    build, neither source knows. Callers must treat it as "cannot tell" and
    stay quiet rather than guess, so an unknown version never produces advice
    that might be wrong.
    """
    rc, out, _ = run_unchecked(
        ["dpkg-query", "-W", "-f=${Version}", "mergerfs"], timeout=10
    )
    if rc == 0:
        version = parse_version(out)
        if version:
            return version
    rc, out, err = run_unchecked(["mergerfs", "-V"], timeout=10)
    if rc < 0:
        return None
    return parse_version(out or err)


def kernel_version() -> Optional[Version]:
    return parse_version(os.uname().release)


def _at_least(actual: Optional[Version], minimum: Version) -> Optional[bool]:
    if actual is None:
        return None
    return actual[: len(minimum)] >= minimum


def capabilities() -> dict:
    """One probe of the machine, shared by every check that needs it.

    `passthrough` is True only when both halves are known to be new enough.
    `passthrough_missing` names the half that is holding it back - "mergerfs"
    or "kernel" - and is None when passthrough is available or when something
    could not be determined at all.
    """
    mergerfs = installed_version()
    kernel = kernel_version()
    mergerfs_ok = _at_least(mergerfs, PASSTHROUGH_MIN_MERGERFS)
    kernel_ok = _at_least(kernel, PASSTHROUGH_MIN_KERNEL)
    missing = None
    if kernel_ok is False:
        missing = "kernel"
    elif kernel_ok and mergerfs_ok is False:
        missing = "mergerfs"
    return {
        "mergerfs_version": version_text(mergerfs),
        "kernel_version": version_text(kernel),
        "passthrough": bool(mergerfs_ok and kernel_ok),
        "passthrough_missing": missing,
    }


def version_text(version: Optional[Version]) -> str:
    return ".".join(str(part) for part in version) if version else "?"
