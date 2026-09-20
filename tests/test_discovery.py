"""Windows discovery checks: is the host findable, and by a usable address.

The wsdd2/IPv6 case is the one these exist for. wsdd2 answers LLMNR as well
as WS-Discovery and, without -4, hands Windows the host's link-local IPv6;
Windows prefers that AAAA, cannot route it, and pays a TCP timeout before
falling back. It presents as an intermittent 0x80070035 plus slow startup in
anything touching a mapped drive, and every server-side check looks clean.
"""

import pytest

from app import diagnostics
from app.models import State
from app.samba import discovery

# address ifindex prefixlen scope flags device
IF_INET6 = (
    "00000000000000000000000000000001 01 80 10 80       lo\n"
    "fe800000000000008e3223fffe820493 02 40 20 80     vmbr0\n"
    "20010db8000000000000000000000001 02 40 00 00     vmbr0\n"
)


def fake_proc(tmp_path, pids: dict, if_inet6: str = IF_INET6):
    """A /proc tree with the given {pid: argv} and an if_inet6."""
    for pid, argv in pids.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(argv[0].rsplit("/", 1)[-1] + "\n")
        (d / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "if_inet6").write_text(if_inet6)
    (tmp_path / "self").mkdir()  # a non-numeric entry must be skipped
    return str(tmp_path)


# --- reading the running process ---------------------------------------------

def test_process_argv_finds_the_daemon(tmp_path):
    root = fake_proc(tmp_path, {42: ["/usr/sbin/wsdd2", "-4"]})
    assert discovery.process_argv("wsdd2", root) == ["/usr/sbin/wsdd2", "-4"]


def test_process_argv_is_none_when_not_running(tmp_path):
    root = fake_proc(tmp_path, {42: ["/usr/sbin/smbd"]})
    assert discovery.process_argv("wsdd2", root) is None


def test_link_local_interfaces_skip_loopback_and_global_scope(tmp_path):
    root = fake_proc(tmp_path, {})
    assert discovery.link_local_ipv6_interfaces(root) == ["vmbr0"]


def test_no_link_local_addresses_at_all(tmp_path):
    root = fake_proc(tmp_path, {}, if_inet6="")
    assert discovery.link_local_ipv6_interfaces(root) == []


def test_missing_proc_files_are_not_an_error(tmp_path):
    root = str(tmp_path / "nope")
    assert discovery.process_argv("wsdd2", root) is None
    assert discovery.link_local_ipv6_interfaces(root) == []


# --- the condition the check is about ----------------------------------------

def test_wsdd2_without_dash_four_publishes_ipv6(tmp_path):
    root = fake_proc(tmp_path, {42: ["/usr/sbin/wsdd2"]})
    assert discovery.wsdd2_publishes_ipv6(root) is True


def test_wsdd2_with_dash_four_does_not(tmp_path):
    root = fake_proc(tmp_path, {42: ["/usr/sbin/wsdd2", "-4"]})
    assert discovery.wsdd2_publishes_ipv6(root) is False


def test_quiet_on_a_host_with_no_link_local_address(tmp_path):
    """Without an fe80:: address there is no bad AAAA to publish, so the
    missing -4 costs nothing and saying so would be noise."""
    root = fake_proc(tmp_path, {42: ["/usr/sbin/wsdd2"]}, if_inet6="")
    assert discovery.wsdd2_publishes_ipv6(root) is False


def test_quiet_when_wsdd2_is_not_running(tmp_path):
    """That is the other check's business - one finding per problem."""
    root = fake_proc(tmp_path, {42: ["/usr/sbin/smbd"]})
    assert discovery.wsdd2_publishes_ipv6(root) is False


# --- the findings ------------------------------------------------------------

@pytest.fixture
def units(monkeypatch):
    """Both discovery units installed and running unless a test says otherwise."""
    state = {"nmbd": True, "wsdd2": True}
    monkeypatch.setattr(discovery, "unit_is_installed", lambda u: u in state)
    monkeypatch.setattr(discovery, "unit_is_active", lambda u: state.get(u, False))
    return state


def test_finding_is_raised_for_a_wsdd2_publishing_ipv6(monkeypatch, units):
    monkeypatch.setattr(discovery, "wsdd2_publishes_ipv6", lambda: True)
    monkeypatch.setattr(
        discovery, "link_local_ipv6_interfaces", lambda: ["vmbr0"]
    )
    found = diagnostics._network_checks(State())
    assert [f["id"] for f in found] == ["wsdd2_publishes_ipv6"]
    assert found[0]["category"] == "network"
    assert found[0]["entity"] == "wsdd2"
    assert found[0]["vars"]["interfaces"] == "vmbr0"
    assert found[0]["fixable"] is True


def test_copyable_command_stays_on_one_line(monkeypatch, units):
    """The UI offers it for copy-paste, so an embedded newline would leave
    half the command behind."""
    monkeypatch.setattr(discovery, "wsdd2_publishes_ipv6", lambda: True)
    monkeypatch.setattr(discovery, "link_local_ipv6_interfaces", lambda: ["eth0"])
    command = diagnostics._network_checks(State())[0]["command"]
    assert "\n" not in command
    assert "wsdd2 -4" in command


def test_inactive_discovery_unit_is_reported(monkeypatch, units):
    monkeypatch.setattr(discovery, "wsdd2_publishes_ipv6", lambda: False)
    units["nmbd"] = False
    found = diagnostics._network_checks(State())
    assert [(f["id"], f["entity"]) for f in found] == [
        ("discovery_unit_inactive", "nmbd")
    ]


def test_uninstalled_unit_is_not_a_fault(monkeypatch, units):
    """No package is a deliberate choice, or a host that never ran the
    installer - neither is something to report."""
    monkeypatch.setattr(discovery, "wsdd2_publishes_ipv6", lambda: False)
    monkeypatch.setattr(discovery, "unit_is_installed", lambda u: False)
    monkeypatch.setattr(discovery, "unit_is_active", lambda u: False)
    assert diagnostics._network_checks(State()) == []


def test_nothing_to_report_when_all_is_well(monkeypatch, units):
    monkeypatch.setattr(discovery, "wsdd2_publishes_ipv6", lambda: False)
    assert diagnostics._network_checks(State()) == []


def test_network_findings_reach_run_all(monkeypatch, units, sandbox):
    monkeypatch.setattr(discovery, "wsdd2_publishes_ipv6", lambda: True)
    monkeypatch.setattr(discovery, "link_local_ipv6_interfaces", lambda: ["eth0"])
    ids = [f["id"] for f in diagnostics.run_all(State())]
    assert "wsdd2_publishes_ipv6" in ids


# --- the one-click fixes -----------------------------------------------------

def test_fix_writes_the_dropin_and_restarts(monkeypatch, units, sandbox):
    calls = []
    monkeypatch.setattr(
        diagnostics.systemd, "systemctl",
        lambda *a: calls.append(a) or True,
    )
    detail = diagnostics.apply_fix(State(), "wsdd2_publishes_ipv6", "wsdd2")
    assert discovery.dropin_path().read_text() == discovery.DROPIN_TEXT
    assert ("daemon-reload",) in calls and ("restart", "wsdd2") in calls
    assert "IPv4" in detail


def test_fix_reverts_when_wsdd2_will_not_start(monkeypatch, units, sandbox):
    """An older build may not know -4, and a host with no discovery at all is
    worse off than one publishing an address Windows has to time out on."""
    monkeypatch.setattr(diagnostics.systemd, "systemctl", lambda *a: True)
    monkeypatch.setattr(discovery, "unit_is_active", lambda u: False)
    with pytest.raises(diagnostics.SystemOpError, match="restored"):
        diagnostics.apply_fix(State(), "wsdd2_publishes_ipv6", "wsdd2")
    assert not discovery.dropin_path().exists()


def test_unit_fix_enables_the_named_unit(monkeypatch, units):
    calls = []
    monkeypatch.setattr(
        diagnostics.systemd, "systemctl",
        lambda *a: calls.append(a) or True,
    )
    diagnostics.apply_fix(State(), "discovery_unit_inactive", "nmbd")
    assert calls == [("enable", "--now", "nmbd")]


def test_unit_fix_refuses_an_entity_it_does_not_know(monkeypatch):
    """entity arrives from the HTTP request, so it must never reach
    systemctl unchecked."""
    called = []
    monkeypatch.setattr(
        diagnostics.systemd, "systemctl", lambda *a: called.append(a) or True
    )
    with pytest.raises(diagnostics.SystemOpError, match="not a discovery unit"):
        diagnostics.apply_fix(State(), "discovery_unit_inactive", "sshd")
    assert called == []
