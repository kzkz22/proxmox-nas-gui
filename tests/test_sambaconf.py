import pytest

from app.models import State
from app.samba.models import (
    Access,
    ExportMode,
    GlobalSettings,
    Security,
    Share,
)
from app.samba.sambaconf import generate


def share_lines(conf: str, name: str) -> list[str]:
    lines = conf.splitlines()
    start = lines.index(f"[{name}]")
    out = []
    for line in lines[start + 1:]:
        if line.startswith("["):
            break
        if line.strip():
            out.append(line.strip())
    return out


def make_state(**share_kwargs) -> State:
    share = Share(name="media", path="/srv/media", **share_kwargs)
    return State(shares={"media": share})


def test_global_section():
    state = State(
        settings=GlobalSettings(
            workgroup="HOME", server_string="NAS", netbios_name="PVE",
            min_protocol="SMB3",
        )
    )
    conf = generate(state)
    g = share_lines(conf, "global")
    assert "workgroup = HOME" in g
    assert "server string = NAS" in g
    assert "netbios name = PVE" in g
    assert "server min protocol = SMB3" in g
    assert "map to guest = Bad User" in g
    assert "security = user" in g


def test_netbios_omitted_when_empty():
    conf = generate(State())
    assert "netbios name" not in conf


def test_public_share():
    conf = generate(make_state(security=Security.PUBLIC))
    s = share_lines(conf, "media")
    assert "path = /srv/media" in s
    assert "guest ok = yes" in s
    assert "read only = no" in s
    assert "browseable = yes" in s
    assert "force user = nobody" in s
    assert not any(line.startswith("valid users") for line in s)


def test_secure_share_write_list():
    conf = generate(
        make_state(
            security=Security.SECURE,
            user_access={"bob": Access.WRITE, "alice": Access.WRITE,
                         "carol": Access.READ},
            group_access={"family": Access.WRITE},
        )
    )
    s = share_lines(conf, "media")
    assert "guest ok = yes" in s
    assert "read only = yes" in s
    assert "write list = alice bob @family" in s


def test_private_share_lists():
    conf = generate(
        make_state(
            security=Security.PRIVATE,
            user_access={"alice": Access.WRITE, "bob": Access.READ},
            group_access={"family": Access.READ},
        )
    )
    s = share_lines(conf, "media")
    assert "guest ok = no" in s
    assert "read only = yes" in s
    assert "valid users = alice bob @family" in s
    assert "write list = alice" in s
    assert "invalid users = *" not in s


def test_private_share_empty_denies_everyone():
    conf = generate(make_state(security=Security.PRIVATE))
    s = share_lines(conf, "media")
    assert "invalid users = *" in s
    assert not any(line.startswith("valid users") for line in s)


def test_no_access_entries_are_ignored():
    conf = generate(
        make_state(
            security=Security.PRIVATE,
            user_access={"alice": Access.WRITE, "bob": Access.NONE},
        )
    )
    s = share_lines(conf, "media")
    assert "valid users = alice" in s


def test_hidden_export():
    conf = generate(make_state(export=ExportMode.HIDDEN))
    assert "browseable = no" in share_lines(conf, "media")


def test_export_no_marks_unavailable():
    conf = generate(make_state(export=ExportMode.NO))
    assert "available = no" in share_lines(conf, "media")


def test_recycle_bin():
    conf = generate(make_state(recycle=True))
    s = share_lines(conf, "media")
    assert "vfs objects = recycle" in s
    assert "recycle:repository = .Recycle.Bin/%U" in s
    assert "recycle:keeptree = yes" in s


def test_no_recycle_by_default():
    conf = generate(make_state())
    assert "recycle" not in conf


def test_shares_sorted_and_deterministic():
    state = State(
        shares={
            "zeta": Share(name="zeta", path="/srv/z"),
            "Alpha": Share(name="Alpha", path="/srv/a"),
        }
    )
    conf = generate(state)
    assert conf.index("[Alpha]") < conf.index("[zeta]")
    assert conf == generate(state)


def test_comment_rendered():
    conf = generate(make_state(comment="family photos"))
    assert "comment = family photos" in share_lines(conf, "media")


@pytest.mark.parametrize("bad", ["../etc", "a\nb", "with%macro", "name]"])
def test_share_name_rejected(bad):
    with pytest.raises(ValueError):
        Share(name=bad, path="/srv/x")


@pytest.mark.parametrize("bad", ["relative/path", "/has\nnewline", "/has%macro"])
def test_share_path_rejected(bad):
    with pytest.raises(ValueError):
        Share(name="ok", path=bad)


def test_reserved_share_names_rejected():
    for reserved in ("global", "homes", "printers"):
        with pytest.raises(ValueError):
            Share(name=reserved, path="/srv/x")
