"""POSIX and Samba account management.

A GUI user is always both: useradd creates the system account that owns the
POSIX group memberships used by the access matrix, and smbpasswd creates the
Samba account that actually authenticates over SMB. Only the Samba side of the
application calls any of this.
"""

import grp
import pwd
from typing import List

from ..core.proc import SystemOpError, run


def user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def group_members(name: str) -> List[str]:
    try:
        return sorted(grp.getgrnam(name).gr_mem)
    except KeyError:
        return []


def create_user(name: str, password: str, description: str = "") -> None:
    if not user_exists(name):
        run(
            ["useradd", "-M", "-s", "/usr/sbin/nologin",
             "-c", description or "proxmox-samba-gui user", name]
        )
    set_smb_password(name, password)


def set_smb_password(name: str, password: str) -> None:
    if "\n" in password or "\0" in password:
        raise SystemOpError("invalid password")
    run(["smbpasswd", "-s", "-a", name], input_text=f"{password}\n{password}\n")
    run(["smbpasswd", "-e", name])


def delete_user(name: str) -> None:
    try:
        run(["smbpasswd", "-x", name])
    except SystemOpError:
        pass
    if user_exists(name):
        run(["userdel", name])


def create_group(name: str) -> None:
    if not group_exists(name):
        run(["groupadd", name])


def delete_group(name: str) -> None:
    if group_exists(name):
        run(["groupdel", name])


def set_group_members(name: str, members: List[str]) -> None:
    run(["gpasswd", "-M", ",".join(members), name])
