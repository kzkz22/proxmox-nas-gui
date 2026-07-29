import os
import subprocess
import tempfile
from pathlib import Path

from ..core.proc import SystemOpError
from ..models import State
from . import sambaconf

BACKUP_SUFFIX = ".psg-backup"


def smb_conf_path() -> Path:
    return Path(os.environ.get("PSG_SMB_CONF", "/etc/samba/smb.conf"))


def gen_conf_path() -> Path:
    return Path(os.environ.get("PSG_GEN_CONF", "/etc/samba/proxmox-samba-gui.conf"))


def _include_line() -> str:
    return f"include = {gen_conf_path()}"


def _master_without_include(text: str) -> str:
    lines = [
        line for line in text.splitlines()
        if line.strip() != _include_line()
    ]
    return "\n".join(lines)


def ensure_include() -> None:
    conf = smb_conf_path()
    text = conf.read_text() if conf.exists() else "[global]\n"
    if _include_line() in (line.strip() for line in text.splitlines()):
        return
    backup = conf.with_name(conf.name + BACKUP_SUFFIX)
    if conf.exists() and not backup.exists():
        backup.write_text(text)
    if not text.endswith("\n"):
        text += "\n"
    conf.write_text(text + _include_line() + "\n")


def validate(generated: str) -> None:
    """Run testparm against a temp copy of the full config before touching
    the live files, so a bad change can never break the running Samba."""
    conf = smb_conf_path()
    base = conf.read_text() if conf.exists() else "[global]\n"
    with tempfile.TemporaryDirectory(prefix="psg-testparm-") as tmp:
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
