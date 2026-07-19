import grp
import pwd
import subprocess
from typing import List


class SystemOpError(Exception):
    pass


def run(cmd: List[str], input_text: str | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        raise SystemOpError(f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise SystemOpError(f"command timed out: {' '.join(cmd)}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SystemOpError(f"{cmd[0]} failed: {detail}")
    return proc.stdout


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
