"""The device-facing half: the spin-down chain and disk enumeration.

`run` is replaced with a scripted fake, so the sequence of commands the code
would actually issue is what gets asserted - which is the only way to pin the
behaviour that motivated this whole feature: hdparm cannot put every drive
into standby, and a command exiting 0 is not evidence that it did.
"""

import json

import pytest

from app.core.proc import SystemOpError
from app.storage import disksleep, sleepconf

WD = "ata-WDC_WD10SPCX-21KHST0_WD-WX21A44D9471"
TOSHIBA = "ata-TOSHIBA_DT01ACA300_334401VAS"


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """The settle delay after each spin-down attempt is real seconds."""
    monkeypatch.setattr(disksleep.time, "sleep", lambda _s: None)


class FakeRun:
    """Records every command and answers the power-state query from a script.

    Only `hdparm -C` consumes a state; the spin-down commands themselves just
    succeed, which is exactly the situation being modelled - a drive that
    accepts STANDBY IMMEDIATE and keeps spinning reports no error anywhere
    except in the next -C.
    """

    def __init__(self, states=(), failures=()):
        self.states = list(states)
        self.failures = set(failures)
        self.calls = []

    def __call__(self, cmd, input_text=None, timeout=30):
        self.calls.append(cmd)
        if cmd[0] in self.failures:
            raise SystemOpError(f"{cmd[0]} failed: no such device")
        if cmd[0] == "hdparm" and "-C" in cmd:
            state = self.states.pop(0) if self.states else "active/idle"
            return f"\n{cmd[-1]}:\n drive state is:  {state}\n"
        return ""

    @property
    def programs(self):
        return [c[0] for c in self.calls]


# --- the spin-down chain ----------------------------------------------------

def test_hdparm_alone_is_enough_for_a_cooperative_drive(monkeypatch):
    fake = FakeRun(["standby"])
    monkeypatch.setattr(disksleep, "run", fake)

    ok, method, detail = disksleep.spin_down("/dev/sdb")

    assert (ok, method) == (True, "hdparm")
    assert fake.programs == ["hdparm", "hdparm"], "spin down, then verify"
    assert detail == "hdparm ok"


def test_a_drive_that_ignores_hdparm_falls_through_to_sg_start(monkeypatch):
    """The case the user hit: hdparm reports success and the drive keeps
    spinning, so only the verification catches it."""
    fake = FakeRun(["active/idle", "standby"])
    monkeypatch.setattr(disksleep, "run", fake)

    ok, method, detail = disksleep.spin_down("/dev/sdb")

    assert (ok, method) == (True, "sg_start")
    assert fake.programs == ["hdparm", "hdparm", "sg_start", "hdparm"]
    assert "hdparm: no standby" in detail


def test_the_chain_continues_past_a_missing_tool(monkeypatch):
    fake = FakeRun(["standby"], failures={"sg_start"})
    monkeypatch.setattr(disksleep, "run", fake)

    ok, method, _ = disksleep.spin_down("/dev/sdb", preferred="sg_start")

    assert (ok, method) == (True, "hdparm")
    assert fake.programs[0] == "sg_start", "the remembered method is tried first"


def test_a_drive_that_refuses_everything_reports_what_was_tried(monkeypatch):
    fake = FakeRun(["active/idle"] * 4)
    monkeypatch.setattr(disksleep, "run", fake)

    ok, method, detail = disksleep.spin_down("/dev/sdb")

    assert ok is False and method is None
    assert [p for p in fake.programs if p != "hdparm"] == ["sg_start", "sdparm"]
    assert detail.count("no standby") == 3


def test_the_remembered_method_is_tried_first(monkeypatch):
    fake = FakeRun(["standby"])
    monkeypatch.setattr(disksleep, "run", fake)

    ok, method, _ = disksleep.spin_down("/dev/sdb", preferred="sdparm")

    assert (ok, method) == (True, "sdparm")
    assert fake.programs == ["sdparm", "hdparm"], "one command in the steady state"


def test_an_unreadable_power_state_is_unknown_not_a_crash(monkeypatch):
    monkeypatch.setattr(disksleep, "run", FakeRun(failures={"hdparm"}))
    assert disksleep.power_state("/dev/sdb") == sleepconf.UNKNOWN


# --- enumeration ------------------------------------------------------------

LSBLK = json.dumps({"blockdevices": [
    {"path": "/dev/sda", "type": "disk", "size": 1, "rota": True,
     "children": [{"path": "/dev/sda1", "type": "part", "fstype": "ext4",
                   "mountpoint": "/mnt/disks/wd1tb"}]},
    {"path": "/dev/sdb", "type": "disk", "size": 1, "rota": True,
     "children": [{"path": "/dev/sdb1", "type": "part", "fstype": "ext4",
                   "mountpoint": "/mnt/disks/toshiba3"}]},
    # ZFS root: no mountpoint anywhere, so only findmnt + zpool can reveal it.
    {"path": "/dev/sdc", "type": "disk", "size": 1, "rota": True,
     "children": [{"path": "/dev/sdc3", "type": "part", "fstype": "zfs_member",
                   "mountpoint": None}]},
]})


@pytest.fixture
def enumerated(monkeypatch):
    def fake_run(cmd, input_text=None, timeout=30):
        if cmd[0] == "lsblk":
            return LSBLK
        if cmd[0] == "findmnt":
            return "rpool/ROOT/pve-1 zfs\n"
        if cmd[0] == "zpool":
            return "rpool\t-\t-\n\t/dev/sdc3\t-\t-\n"
        return ""

    monkeypatch.setattr(disksleep, "run", fake_run)
    monkeypatch.setattr(disksleep.pools, "by_id_map", lambda: {
        "/dev/sda": [WD], "/dev/sdb": [TOSHIBA], "/dev/sdc": ["ata-SYSTEM_DISK"],
    })
    monkeypatch.setattr(disksleep, "_resolve", lambda p: p)


def test_the_zfs_root_disk_never_appears(enumerated):
    """Without the findmnt/zpool lookup this disk looks like any other data
    disk, and the GUI would offer to spin the Proxmox system disk down."""
    found = disksleep.list_sleep_disks()

    assert [d["by_id"] for d in found] == sorted([WD, TOSHIBA])


def test_a_disk_without_a_by_id_name_is_skipped(enumerated, monkeypatch):
    """/dev/sdX is not a stable key: after a reboot the policy stored against
    it could be applied to a different disk."""
    monkeypatch.setattr(disksleep.pools, "by_id_map", lambda: {"/dev/sdb": [TOSHIBA]})

    assert [d["by_id"] for d in disksleep.list_sleep_disks()] == [TOSHIBA]


def test_enumeration_survives_a_host_without_zfs(enumerated, monkeypatch):
    def no_zfs(cmd, input_text=None, timeout=30):
        if cmd[0] == "lsblk":
            return LSBLK
        raise SystemOpError(f"command not found: {cmd[0]}")

    monkeypatch.setattr(disksleep, "run", no_zfs)

    assert len(disksleep.list_sleep_disks()) == 3


# --- warnings ---------------------------------------------------------------

def test_the_noatime_warning_is_raised_for_a_managed_mount(sandbox, monkeypatch):
    from app.models import State
    from app.storage.models import DiskMount

    st = State()
    st.disk_mounts["toshiba3"] = DiskMount(
        uuid="1234-abcd", fstype="ext4", mountpoint="/mnt/disks/toshiba3")

    env = _env(monkeypatch, st, proc_mounts=(
        "/dev/sdb1 /mnt/disks/toshiba3 ext4 rw,relatime 0 0\n"))
    disk = {"path": "/dev/sdb", "mountpoints": ["/mnt/disks/toshiba3"]}

    warnings = {w["id"]: w for w in disksleep.warnings_for(env, disk)}

    assert warnings["noatime"]["fixable"] is True
    assert warnings["noatime"]["vars"]["name"] == "toshiba3"


def test_a_noatime_mount_raises_nothing(sandbox, monkeypatch):
    from app.models import State
    from app.storage.models import DiskMount

    st = State()
    st.disk_mounts["toshiba3"] = DiskMount(
        uuid="1234-abcd", fstype="ext4", mountpoint="/mnt/disks/toshiba3")

    env = _env(monkeypatch, st, proc_mounts=(
        "/dev/sdb1 /mnt/disks/toshiba3 ext4 rw,noatime 0 0\n"))
    disk = {"path": "/dev/sdb", "mountpoints": ["/mnt/disks/toshiba3"]}

    assert disksleep.warnings_for(env, disk) == []


def test_warnings_are_ordered_by_severity(sandbox, monkeypatch):
    from app.models import State
    from app.storage.models import Branch, Pool

    st = State()
    st.pools["bulk"] = Pool(name="bulk", mountpoint="/mnt/pool/bulk",
                            branches=[Branch(path="/mnt/disks/toshiba3")])
    env = _env(monkeypatch, st, smartd_active=True,
               smartd_conf="DEVICESCAN -d removable -m root\n")
    disk = {"path": "/dev/sdb", "mountpoints": ["/mnt/disks/toshiba3"]}

    ids = [w["id"] for w in disksleep.warnings_for(env, disk)]

    assert ids == ["smartd", "mergerfs_cache"], "crit before warn"


def _env(monkeypatch, state, *, proc_mounts="", smartd_active=False, smartd_conf=""):
    monkeypatch.setattr(disksleep.pools, "systemctl", lambda *a: smartd_active)
    monkeypatch.setattr(disksleep, "_read", lambda path: (
        smartd_conf if path == disksleep.smartd_conf_path()
        else proc_mounts if str(path) == "/proc/mounts"
        else ""
    ))
    return disksleep.Environment(state)
