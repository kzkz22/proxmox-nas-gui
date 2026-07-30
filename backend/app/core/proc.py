"""Running external commands.

Every part of the application shells out - Samba to useradd/smbpasswd, the
storage side to mount/lsblk/mergerfs, the browser dialog to zfs - so this
lives in core rather than in either feature package.
"""

import subprocess
from typing import List


class SystemOpError(Exception):
    pass


def run(cmd: List[str], input_text: str | None = None, timeout: int = 30) -> str:
    try:
        proc = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise SystemOpError(f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise SystemOpError(f"command timed out: {' '.join(cmd)}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SystemOpError(f"{cmd[0]} failed: {detail}")
    return proc.stdout
