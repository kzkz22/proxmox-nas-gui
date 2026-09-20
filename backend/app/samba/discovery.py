"""What the Windows discovery daemons are doing, and whether it is sane.

Kept apart from service.py (which owns smbd's configuration) because this
answers a different question: not "what should Samba be serving" but "can a
Windows client find this host at all, and by an address it can actually
reach". The diagnostics page is the only consumer, the same way
mergerfs_env.py exists for the pool checks.

The one thing worth singling out is that wsdd2 is not only a WS-Discovery
daemon - it answers LLMNR name queries too, and by default it replies with
every address on the interface, link-local IPv6 included. Windows prefers the
AAAA answer, cannot route an fe80:: address without a zone id, and only falls
back to IPv4 after the TCP connect times out. Every name lookup then pays a
timeout, which is slow enough to stall any application that touches a mapped
drive, and the lookups that run out of patience surface as 0x80070035, "the
network path was not found" - a message that sends you hunting for a firewall
or a Samba misconfiguration, neither of which is there. Passing -4 keeps
discovery working and stops the unusable AAAA being published.
"""

import os
from pathlib import Path
from typing import List, Optional

from ..core.proc import run_unchecked
from ..core.systemd import systemd_dir

# Both matter and neither is enough alone: nmbd serves NetBIOS name lookups
# and the browse list for older clients, wsdd2 the WS-Discovery that Windows
# 10/11 use since SMB1 (and with it NetBIOS browsing) was removed.
DISCOVERY_UNITS = ("nmbd", "wsdd2")

DROPIN_NAME = "ipv4-only.conf"
DROPIN_TEXT = """\
# Managed by proxmox-nas-gui - see deploy/install.sh
[Service]
ExecStart=
ExecStart=/usr/sbin/wsdd2 -4
"""


def dropin_path() -> Path:
    return systemd_dir() / "wsdd2.service.d" / DROPIN_NAME


def process_argv(name: str, proc_root: str = "/proc") -> Optional[List[str]]:
    """argv of the running process called `name`, or None if it is not running.

    Read straight out of /proc rather than shelling out to ps, because the
    answer has to reflect the process that is actually running: a drop-in can
    be on disk and correct while the daemon still runs with the old command
    line, and `systemctl show` would report the former.
    """
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry.name, "comm")) as fh:
                if fh.read().strip() != name:
                    continue
            with open(os.path.join(proc_root, entry.name, "cmdline"), "rb") as fh:
                raw = fh.read()
        except OSError:
            # The process exited between scandir and open, or is not ours.
            continue
        return [arg for arg in raw.decode(errors="replace").split("\0") if arg]
    return None


def link_local_ipv6_interfaces(proc_root: str = "/proc") -> List[str]:
    """Non-loopback interfaces carrying an fe80:: address.

    Without one there is no bad AAAA for wsdd2 to publish, so this is what
    keeps the check from firing on an IPv4-only host where the missing -4
    costs nothing. Columns of /proc/net/if_inet6 are address, ifindex,
    prefix length, scope, flags, device; scope 0x20 is link-local.
    """
    found = set()
    try:
        with open(os.path.join(proc_root, "net", "if_inet6")) as fh:
            lines = fh.readlines()
    except OSError:
        return []
    for line in lines:
        parts = line.split()
        if len(parts) < 6:
            continue
        address, scope, device = parts[0], parts[3], parts[5]
        if device == "lo":
            continue
        try:
            if int(scope, 16) != 0x20:
                continue
        except ValueError:
            continue
        if address.lower().startswith("fe80"):
            found.add(device)
    return sorted(found)


def wsdd2_publishes_ipv6(proc_root: str = "/proc") -> bool:
    """True when the running wsdd2 will hand Windows a link-local AAAA.

    False - not an error - when wsdd2 is not running at all: that is a
    different problem, reported by its own check, and one silent finding per
    problem is the point.
    """
    argv = process_argv("wsdd2", proc_root)
    if argv is None or "-4" in argv[1:]:
        return False
    return bool(link_local_ipv6_interfaces(proc_root))


def unit_is_installed(unit: str) -> bool:
    """Whether systemd knows this unit, so an absent package stays quiet."""
    code, out, _ = run_unchecked(
        ["systemctl", "show", unit, "--property=LoadState", "--value"]
    )
    return code == 0 and out.strip() == "loaded"


def unit_is_active(unit: str) -> bool:
    code, out, _ = run_unchecked(["systemctl", "is-active", unit])
    return code == 0 and out.strip() == "active"


def write_ipv4_only_dropin() -> Path:
    """Install the drop-in the installer writes, and report where it went."""
    path = dropin_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DROPIN_TEXT)
    return path
