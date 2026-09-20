"""Talking to systemd: where units live, whether it is there, running it.

Promoted out of storage/pools.py, where these started life because pools and
bind mounts were the only things installing units. They are infrastructure
rather than storage domain logic, and the Samba half now needs them too - the
Windows discovery checks read and write a drop-in for wsdd2 - so keeping them
in a feature package would mean one half importing the other. pools.py still
re-exports all three, so its callers are unaffected.
"""

import os
import subprocess
from pathlib import Path


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
    """Best-effort systemctl; False when systemd is absent or the call failed."""
    try:
        proc = subprocess.run(
            ["systemctl", *args], capture_output=True, text=True, timeout=60
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
