"""The disk sleep endpoints.

Following test_disks_api.py: the functions that shell out to real devices are
replaced at the module boundary, and everything below them - validation,
persistence, the event log - runs for real against the sandbox.

The cases worth having are the ones where being wrong costs something: the
system disk leaking into the list, the spin-down chain giving up after the
first method, and a fix running that was never on the whitelist.
"""

import json

import pytest

from app.core.proc import SystemOpError
from app.storage import disksleep, eventlog, monitor, sleepconf

TOSHIBA = "ata-TOSHIBA_DT01ACA300_334401VAS"
WD = "ata-WDC_WD10SPCX-21KHST0_WD-WX21A44D9471"
SSD = "ata-Samsung_SSD_860_EVO_500GB_S3Z2NB0K"


def disk(by_id, path, *, rotational=True, mountpoints=(), model="DISK"):
    return {
        "path": path, "name": path.rsplit("/", 1)[-1], "size": 3 * 10**12,
        "model": model, "serial": "SER", "rotational": rotational,
        "system": False, "mountpoints": list(mountpoints), "fstypes": ["ext4"],
        "by_id": by_id, "by_id_all": [by_id],
    }


DISKS = [
    disk(TOSHIBA, "/dev/sdb", mountpoints=["/mnt/disks/toshiba3"]),
    disk(WD, "/dev/sda", mountpoints=["/mnt/disks/wd1tb"]),
    disk(SSD, "/dev/sdc", rotational=False),
]


@pytest.fixture
def disks(monkeypatch):
    """A stable fake set of disks, with no warnings and nothing asleep."""
    monkeypatch.setattr(disksleep, "list_sleep_disks", lambda: [dict(d) for d in DISKS])
    monkeypatch.setattr(disksleep, "power_state", lambda path: sleepconf.ACTIVE)
    monkeypatch.setattr(disksleep, "describe", lambda st, ds: {
        d["by_id"]: {"warnings": [], "zfs_pool": None} for d in ds
    })
    monkeypatch.setattr(disksleep, "hd_idle_running", lambda: False)
    monkeypatch.setattr(monitor, "_tracked", {})
    return DISKS


# --- listing ----------------------------------------------------------------

def test_rotating_and_non_rotating_devices_are_separated(auth_client, sandbox, disks):
    body = auth_client.get("/api/sleep").json()

    assert {d["by_id"] for d in body["disks"]} == {TOSHIBA, WD}
    assert [d["by_id"] for d in body["other"]] == [SSD]
    assert body["other"][0]["reason"] == "not_rotational"


def test_unconfigured_disks_report_the_safe_default(auth_client, sandbox, disks):
    body = auth_client.get("/api/sleep").json()

    assert all(d["idle_seconds"] == 0 for d in body["disks"])
    assert all(d["configured"] is False for d in body["disks"])


def test_the_offered_idle_times_are_the_ones_asked_for(auth_client, sandbox, disks):
    choices = auth_client.get("/api/sleep").json()["idle_choices"]

    assert choices == [0, 900, 1800, 2700, 3600, 7200, 10800, 14400, 18000, 21600]


def test_the_state_falls_back_to_a_live_read_without_the_monitor(
    auth_client, sandbox, disks, monkeypatch
):
    """With the monitor disabled there is no snapshot to serve from, and a
    page reporting every disk as unknown would be useless."""
    monkeypatch.setattr(disksleep, "power_state", lambda path: sleepconf.STANDBY)

    body = auth_client.get("/api/sleep").json()

    assert all(d["asleep"] is True for d in body["disks"])


# --- policies ---------------------------------------------------------------

def test_an_idle_time_is_stored_against_the_by_id_name(auth_client, sandbox, disks):
    response = auth_client.put(f"/api/sleep/policy/{TOSHIBA}",
                               json={"idle_seconds": 1800})

    assert response.status_code == 200
    stored = json.loads((sandbox["PNAS_STATE_DIR"] / "state.json").read_text())
    assert stored["disk_sleep"][TOSHIBA]["idle_seconds"] == 1800


def test_an_idle_time_outside_the_offered_set_is_refused(auth_client, sandbox, disks):
    response = auth_client.put(f"/api/sleep/policy/{TOSHIBA}",
                               json={"idle_seconds": 137})
    assert response.status_code == 400


def test_a_policy_cannot_be_set_on_an_unknown_disk(auth_client, sandbox, disks):
    response = auth_client.put("/api/sleep/policy/ata-NOT-INSTALLED",
                               json={"idle_seconds": 900})
    assert response.status_code == 404


def test_an_ssd_cannot_be_given_an_idle_time(auth_client, sandbox, disks):
    response = auth_client.put(f"/api/sleep/policy/{SSD}", json={"idle_seconds": 900})
    assert response.status_code == 409


# --- manual spin-down -------------------------------------------------------

def test_a_manual_spin_down_is_logged_with_the_user(
    auth_client, sandbox, disks, monkeypatch
):
    monkeypatch.setattr(disksleep, "spin_down",
                        lambda path, preferred=None: (True, "sg_start", "hdparm failed; sg_start ok"))

    response = auth_client.post(f"/api/sleep/spindown/{TOSHIBA}")

    assert response.status_code == 200
    assert response.json()["method"] == "sg_start"
    rows, _ = eventlog.query(disk=TOSHIBA)
    assert rows[0]["event"] == eventlog.SLEEP
    assert rows[0]["reason"] == eventlog.MANUAL
    assert rows[0]["actor"] == "root"


def test_the_working_method_is_remembered_for_next_time(
    auth_client, sandbox, disks, monkeypatch
):
    """The whole point of the chain: a disk hdparm cannot sleep should not pay
    for the hdparm attempt on every later spin-down."""
    monkeypatch.setattr(disksleep, "spin_down",
                        lambda path, preferred=None: (True, "sdparm", "ok"))

    auth_client.post(f"/api/sleep/spindown/{TOSHIBA}")

    stored = json.loads((sandbox["PNAS_STATE_DIR"] / "state.json").read_text())
    assert stored["disk_sleep"][TOSHIBA]["method"] == "sdparm"


def test_a_failed_spin_down_is_reported_and_logged(
    auth_client, sandbox, disks, monkeypatch
):
    monkeypatch.setattr(disksleep, "spin_down",
                        lambda path, preferred=None: (False, None, "all three methods failed"))

    response = auth_client.post(f"/api/sleep/spindown/{TOSHIBA}")

    assert response.status_code == 409
    assert "all three methods failed" in response.json()["detail"]
    rows, _ = eventlog.query(disk=TOSHIBA)
    assert rows[0]["event"] == eventlog.SLEEP_FAILED


# --- the event log endpoint -------------------------------------------------

def test_events_are_served_with_filters_and_paging(auth_client, sandbox, disks):
    for i in range(4):
        eventlog.record(TOSHIBA, eventlog.WAKE, eventlog.EXTERNAL, ts=1_770_000_000 + i)
    eventlog.record(WD, eventlog.SLEEP, eventlog.MANUAL, ts=1_770_000_000)

    body = auth_client.get(f"/api/sleep/events?disk={TOSHIBA}&limit=2").json()

    assert body["total"] == 4
    assert len(body["events"]) == 2
    assert set(body["disks"]) == {TOSHIBA, WD}


def test_an_unknown_event_filter_is_refused(auth_client, sandbox, disks):
    assert auth_client.get("/api/sleep/events?event=exploded").status_code == 400
    assert auth_client.get("/api/sleep/events?reason=because").status_code == 400


# --- fixes ------------------------------------------------------------------

def test_only_whitelisted_checks_can_be_run(auth_client, sandbox, disks):
    """The informational checks name commands that change Proxmox or ZFS
    policy. Those are shown, never executed."""
    for check in ("mergerfs_cache", "pve_storage", "zfs_autosnapshot", "rm -rf /"):
        response = auth_client.post("/api/sleep/fix",
                                    json={"disk": TOSHIBA, "check": check})
        assert response.status_code == 400, check


def test_the_smartd_fix_rewrites_the_config_and_backs_it_up(
    auth_client, sandbox, disks, monkeypatch
):
    conf = sandbox["PNAS_SMARTD_CONF"]
    conf.write_text("DEVICESCAN -d removable -m root\n")
    restarted = []
    monkeypatch.setattr("app.storage.pools.systemctl",
                        lambda *args: restarted.append(args) or True)

    response = auth_client.post("/api/sleep/fix",
                                json={"disk": TOSHIBA, "check": "smartd"})

    assert response.status_code == 200
    assert conf.read_text().strip().endswith("-n standby,q")
    assert conf.with_name(conf.name + ".pnas-backup").exists()
    assert ("restart", "smartd") in restarted


def test_a_fix_that_does_not_apply_to_the_disk_fails_cleanly(
    auth_client, sandbox, disks, monkeypatch
):
    monkeypatch.setattr(disksleep, "apply_fix", _raise_not_applicable)

    response = auth_client.post("/api/sleep/fix",
                                json={"disk": TOSHIBA, "check": "zfs_atime"})

    assert response.status_code == 409
    assert "not a member" in response.json()["detail"]


def _raise_not_applicable(*args, **kwargs):
    raise SystemOpError("this disk is not a member of a ZFS pool")


# --- hd-idle takeover -------------------------------------------------------

def test_the_takeover_stops_hd_idle_and_imports_its_timings(
    auth_client, sandbox, disks, monkeypatch
):
    sandbox["PNAS_HD_IDLE_CONF"].write_text(
        'HD_IDLE_OPTS="-i 0 -a /dev/disk/by-id/%s -i 1800 -a /dev/disk/by-id/%s -i 3600"\n'
        % (TOSHIBA, WD)
    )
    stopped = []
    monkeypatch.setattr("app.storage.pools.systemctl",
                        lambda *args: stopped.append(args) or True)

    body = auth_client.post("/api/sleep/takeover").json()

    assert ("disable", "--now", "hd-idle") in stopped
    assert body["imported"] == 2
    stored = json.loads((sandbox["PNAS_STATE_DIR"] / "state.json").read_text())
    assert stored["disk_sleep"][TOSHIBA]["idle_seconds"] == 1800
    assert stored["disk_sleep"][WD]["idle_seconds"] == 3600


def test_the_takeover_reports_disks_it_could_not_match(
    auth_client, sandbox, disks, monkeypatch
):
    sandbox["PNAS_HD_IDLE_CONF"].write_text(
        'HD_IDLE_OPTS="-a /dev/disk/by-id/ata-PULLED-DISK -i 1800"\n'
    )
    monkeypatch.setattr("app.storage.pools.systemctl", lambda *args: True)

    body = auth_client.post("/api/sleep/takeover").json()

    assert body["imported"] == 0
    assert any("ata-PULLED-DISK" in note for note in body["notes"])
