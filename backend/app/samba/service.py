import os
import subprocess
import tempfile
from pathlib import Path

from ..core.proc import SystemOpError
from ..models import State
from . import sambaconf

BACKUP_SUFFIX = ".pnas-backup"


def smb_conf_path() -> Path:
    return Path(os.environ.get("PNAS_SMB_CONF", "/etc/samba/smb.conf"))


def gen_conf_path() -> Path:
    return Path(os.environ.get("PNAS_GEN_CONF", "/etc/samba/proxmox-nas-gui.conf"))


def _include_line() -> str:
    return f"include = {gen_conf_path()}"


def _master_without_include(text: str) -> str:
    lines = [
        line for line in text.splitlines()
        if line.strip() != _include_line()
    ]
    return "\n".join(lines)


def _global_end(lines: list[str]) -> int | None:
    """Index of the line just past the [global] section, or None if absent.

    Trailing blank lines are treated as the gap before the next section
    rather than part of [global], so the include lands directly under the
    last real parameter and re-running this leaves the file byte-identical.
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "[global]":
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _with_include(text: str) -> str:
    """smb.conf content with the include line anchored inside [global]."""
    want = _include_line()
    # Strip every existing copy first, so a line an older version appended to
    # the end of the file is migrated rather than duplicated.
    lines = [line for line in text.splitlines() if line.strip() != want]
    at = _global_end(lines)
    if at is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[global]")
        at = len(lines)
    lines.insert(at, "    " + want)
    return "\n".join(lines) + "\n"


def ensure_include() -> None:
    """Make sure smb.conf pulls in the generated file, from inside [global].

    Appending the line to the end of the file - the obvious approach, and what
    this used to do - drops it inside whichever section happens to come last,
    which on a stock Debian smb.conf is [print$]. Samba still applies the
    generated globals, because the included file opens with its own [global]
    header and that switches the context back, so the old placement worked by
    accident. It stops working the moment the master file grows a new trailing
    section, and it reads as a bug to anyone running testparm. Anchoring the
    line to [global] makes the placement mean what it says, and existing
    installs are migrated in place the next time the config is applied.
    """
    conf = smb_conf_path()
    text = conf.read_text() if conf.exists() else "[global]\n"
    updated = _with_include(text)
    if updated == text:
        return
    backup = conf.with_name(conf.name + BACKUP_SUFFIX)
    if conf.exists() and not backup.exists():
        backup.write_text(text)
    conf.write_text(updated)


def validate(generated: str) -> None:
    """Run testparm against a temp copy of the full config before touching
    the live files, so a bad change can never break the running Samba."""
    conf = smb_conf_path()
    base = conf.read_text() if conf.exists() else "[global]\n"
    with tempfile.TemporaryDirectory(prefix="pnas-testparm-") as tmp:
        gen = Path(tmp) / "generated.conf"
        gen.write_text(generated)
        master = Path(tmp) / "smb.conf"
        master.write_text(
            _master_without_include(base) + f"\ninclude = {gen}\n"
        )
        try:
            proc = subprocess.run(
                ["testparm", "-s", "--suppress-prompt", str(master)],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            raise SystemOpError("testparm not found - is Samba installed?")
        except subprocess.TimeoutExpired:
            raise SystemOpError("testparm timed out")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SystemOpError(f"invalid Samba configuration: {detail}")


def reload_samba() -> str | None:
    """Best effort reload; the config is already validated, so a reload
    failure (e.g. smbd not running yet) must not fail the API request."""
    for cmd in (
        ["smbcontrol", "all", "reload-config"],
        ["systemctl", "reload-or-restart", "smbd"],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return None
    return "Samba config saved, but the smbd service could not be reloaded"


def apply(state: State) -> str | None:
    generated = sambaconf.generate(state)
    validate(generated)
    gen = gen_conf_path()
    gen.parent.mkdir(parents=True, exist_ok=True)
    tmp = gen.with_suffix(".conf.tmp")
    tmp.write_text(generated)
    os.replace(tmp, gen)
    ensure_include()
    return reload_samba()


def restart_samba() -> None:
    for cmd in (["systemctl", "restart", "smbd"], ["service", "smbd", "restart"]):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return
    raise SystemOpError("could not restart smbd")


def status() -> dict:
    active = False
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "smbd"],
            capture_output=True, text=True, timeout=10,
        )
        active = proc.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        proc = subprocess.run(["pgrep", "-x", "smbd"], capture_output=True)
        active = proc.returncode == 0
    version = ""
    try:
        out = subprocess.run(
            ["smbd", "--version"], capture_output=True, text=True, timeout=10
        )
        version = out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return {"active": active, "version": version}
