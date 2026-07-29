import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Dict, List

from .proc import SystemOpError, run

RECYCLE_DIR = ".Recycle.Bin"


def zfs_datasets() -> Dict[str, str]:
    """Map of mountpoint -> dataset name; empty when ZFS is unavailable."""
    try:
        out = subprocess.run(
            ["zfs", "list", "-H", "-o", "name,mountpoint"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if out.returncode != 0:
        return {}
    result: Dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].startswith("/"):
            result[parts[1]] = parts[0]
    return result


def _check_dir(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute() or not p.resolve().is_dir():
        raise SystemOpError(f"not a directory: {path}")
    return p.resolve()


def list_dirs(path: str) -> dict:
    p = _check_dir(path)
    datasets = zfs_datasets()
    entries: List[dict] = []
    for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except OSError:
            continue
        entries.append(
            {"name": child.name, "path": str(child),
             "dataset": datasets.get(str(child))}
        )
    return {
        "path": str(p),
        "parent": str(p.parent) if p != p.parent else None,
        "dataset": datasets.get(str(p)),
        "zfs_available": bool(datasets),
        "entries": entries,
    }


def make_dir(parent: str, name: str, dataset: bool = False) -> str:
    if not name or "/" in name or name in (".", "..") or name.startswith("."):
        raise SystemOpError(f"invalid directory name: {name}")
    p = _check_dir(parent)
    target = p / name
    if target.exists():
        raise SystemOpError(f"already exists: {target}")
    if dataset:
        parent_ds = zfs_datasets().get(str(p))
        if not parent_ds:
            raise SystemOpError(f"parent is not a ZFS dataset: {p}")
        run(["zfs", "create", f"{parent_ds}/{name}"])
    else:
        target.mkdir()
    return str(target)


def apply_share_perms(path: str) -> None:
    """Unraid-style data ownership: the share root belongs to the guest
    account and Samba enforces access, so the matrix never needs chown runs."""
    p = _check_dir(path)
    shutil.chown(p, user="nobody", group="nogroup")
    p.chmod(0o777)


def recycle_usage(share_path: str) -> dict:
    bin_dir = Path(share_path) / RECYCLE_DIR
    total = 0
    files = 0
    if bin_dir.is_dir():
        for root, _dirs, names in os.walk(bin_dir):
            for n in names:
                try:
                    st = os.lstat(os.path.join(root, n))
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
                    files += 1
    return {"bytes": total, "files": files}


def empty_recycle(share_path: str) -> None:
    bin_dir = Path(share_path) / RECYCLE_DIR
    if bin_dir.is_dir() and not bin_dir.is_symlink():
        for child in bin_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
