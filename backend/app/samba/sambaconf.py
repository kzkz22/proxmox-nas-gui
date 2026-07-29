"""Deterministic smb.conf generation from application state.

Pure functions only: no filesystem or subprocess access, so the whole
Unraid-style Export/Security model is unit-testable in isolation.
"""

from typing import List

from ..models import State
from .models import Access, ExportMode, Security, Share

HEADER = (
    "# Managed by proxmox-nas-gui - DO NOT EDIT.\n"
    "# Changes are overwritten whenever the GUI applies its configuration.\n"
)


def _entities(share: Share, levels: tuple) -> List[str]:
    users = sorted(u for u, a in share.user_access.items() if a in levels)
    groups = sorted("@" + g for g, a in share.group_access.items() if a in levels)
    return users + groups


def _global_section(state: State) -> List[str]:
    s = state.settings
    lines = [
        "[global]",
        f"    workgroup = {s.workgroup}",
        f"    server string = {s.server_string}",
    ]
    if s.netbios_name:
        lines.append(f"    netbios name = {s.netbios_name}")
    lines += [
        f"    server min protocol = {s.min_protocol}",
        "    security = user",
        "    map to guest = Bad User",
        "    guest account = nobody",
        "    load printers = no",
        "    printing = bsd",
        "    printcap name = /dev/null",
        "    disable spoolss = yes",
    ]
    return lines


def _share_section(share: Share) -> List[str]:
    lines = [f"[{share.name}]", f"    path = {share.path}"]
    if share.comment:
        lines.append(f"    comment = {share.comment}")
    if share.export == ExportMode.NO:
        lines.append("    available = no")
    lines.append(f"    browseable = {'no' if share.export == ExportMode.HIDDEN else 'yes'}")

    if share.security == Security.PUBLIC:
        lines += ["    guest ok = yes", "    read only = no"]
    elif share.security == Security.SECURE:
        lines += ["    guest ok = yes", "    read only = yes"]
        writers = _entities(share, (Access.WRITE,))
        if writers:
            lines.append(f"    write list = {' '.join(writers)}")
    else:
        lines += ["    guest ok = no", "    read only = yes"]
        valid = _entities(share, (Access.READ, Access.WRITE))
        writers = _entities(share, (Access.WRITE,))
        if valid:
            lines.append(f"    valid users = {' '.join(valid)}")
            if writers:
                lines.append(f"    write list = {' '.join(writers)}")
        else:
            # An empty "valid users" list would allow every authenticated
            # user, so a private share with nobody granted must deny all.
            lines.append("    invalid users = *")

    lines += [
        "    force user = nobody",
        "    create mask = 0666",
        "    directory mask = 0777",
    ]

    if share.recycle:
        lines += [
            "    vfs objects = recycle",
            "    recycle:repository = .Recycle.Bin/%U",
            "    recycle:keeptree = yes",
            "    recycle:versions = yes",
            "    recycle:touch = yes",
            "    recycle:directory_mode = 0777",
            "    recycle:exclude = *.tmp ~$*",
        ]
    return lines


def generate(state: State) -> str:
    blocks = [HEADER.rstrip("\n"), "\n".join(_global_section(state))]
    for name in sorted(state.shares, key=str.lower):
        blocks.append("\n".join(_share_section(state.shares[name])))
    return "\n\n".join(blocks) + "\n"
