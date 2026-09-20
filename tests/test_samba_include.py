"""Placement of the generated-config include line inside smb.conf.

The include used to be appended to the end of the file, which on a stock
Debian smb.conf lands inside [print$]. These pin it to [global].
"""

import os

import pytest

from app.samba import service

DEBIAN_SMB_CONF = """\
[global]
   workgroup = WORKGROUP
   log file = /var/log/samba/log.%m

[homes]
   comment = Home Directories
   browseable = no

[print$]
   comment = Printer Drivers
   path = /var/lib/samba/printers
"""


@pytest.fixture
def conf(tmp_path, monkeypatch):
    path = tmp_path / "smb.conf"
    monkeypatch.setenv("PNAS_SMB_CONF", str(path))
    monkeypatch.setenv("PNAS_GEN_CONF", str(tmp_path / "generated.conf"))
    return path


def section_of(text: str, needle: str) -> str | None:
    """The section header the first line containing `needle` sits under."""
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped
        elif needle in stripped:
            return current
    return None


def test_include_lands_in_global_not_the_last_section(conf):
    conf.write_text(DEBIAN_SMB_CONF)
    service.ensure_include()
    assert section_of(conf.read_text(), "include =") == "[global]"


def test_include_is_added_once_and_is_idempotent(conf):
    conf.write_text(DEBIAN_SMB_CONF)
    service.ensure_include()
    once = conf.read_text()
    service.ensure_include()
    assert conf.read_text() == once
    assert once.count(service._include_line()) == 1


def test_stray_include_at_end_of_file_is_migrated(conf):
    """An install written by the old code must end up with exactly one
    include line, in [global] - not a second copy alongside the stray one."""
    conf.write_text(DEBIAN_SMB_CONF + service._include_line() + "\n")
    service.ensure_include()
    text = conf.read_text()
    assert text.count(service._include_line()) == 1
    assert section_of(text, "include =") == "[global]"


def test_other_sections_survive_untouched(conf):
    conf.write_text(DEBIAN_SMB_CONF)
    service.ensure_include()
    text = conf.read_text()
    for line in ("[homes]", "[print$]", "path = /var/lib/samba/printers"):
        assert line in text


def test_missing_global_section_gets_one(conf):
    conf.write_text("[print$]\n   path = /var/lib/samba/printers\n")
    service.ensure_include()
    text = conf.read_text()
    assert section_of(text, "include =") == "[global]"
    assert "[print$]" in text


def test_absent_smb_conf_is_created(conf):
    assert not conf.exists()
    service.ensure_include()
    assert section_of(conf.read_text(), "include =") == "[global]"


def test_first_rewrite_backs_up_the_original(conf):
    conf.write_text(DEBIAN_SMB_CONF)
    service.ensure_include()
    backup = conf.with_name(conf.name + service.BACKUP_SUFFIX)
    assert backup.read_text() == DEBIAN_SMB_CONF


def test_generated_config_still_wins_over_master_globals(conf):
    """The include must come after the master file's own [global] settings,
    so the values the GUI manages override the packaged defaults."""
    conf.write_text(DEBIAN_SMB_CONF)
    service.ensure_include()
    lines = conf.read_text().splitlines()
    assert lines.index("   workgroup = WORKGROUP") < next(
        i for i, l in enumerate(lines) if "include =" in l
    )
