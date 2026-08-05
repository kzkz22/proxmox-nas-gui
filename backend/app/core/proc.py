"""Running external commands.

Every part of the application shells out - Samba to useradd/smbpasswd, the
storage side to mount/lsblk/mergerfs, the browser dialog to zfs - so this
lives in core rather than in either feature package.
"""

import subprocess
from typing import List, Tuple


class SystemOpError(Exception):
    pass


def run_unchecked(cmd: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    """Run a command whose exit status is data rather than success or failure.

    run() raises on anything non-zero, which is right for mount or mkfs. It is
    wrong for smartctl: that returns a bitmask, where bit 1 means "the drive is
    in standby" and bit 6 means "there are old errors in its log" - neither of
    which makes the output unusable, and the first of which is the answer we
    were asking for. Returns (returncode, stdout, stderr); a missing binary or
    a timeout comes back as -1 with the reason in stderr, so callers never have
    to catch anything.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"command timed out: {' '.join(cmd)}"
    return proc.returncode, proc.stdout, proc.stderr


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
