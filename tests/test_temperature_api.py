"""Temperature storage, the history endpoints, and the sampling round.

The storage half is deliberately separate from the event log's: these are
samples on a cadence, read as a series, with their own retention. What is
worth pinning is that gaps survive - a stretch with no samples is a stretch
where the disk was asleep, and any code that quietly fills it in is drawing a
temperature the disk never had.
"""

import json

import pytest

from app.storage import disksleep, disktemp, eventlog, monitor, sleepconf

TOSHIBA = "ata-TOSHIBA_DT01ACA300_334401VAS"
WD = "ata-WDC_WD10SPCX-21KHST0_WD-WX21A44D9471"
SYSTEM = "ata-SYSTEM_DISK"
NOW = 1_770_000_000


# --- storage ----------------------------------------------------------------

def test_readings_round_trip(sandbox):
    eventlog.record_temperature(TOSHIBA, 38, ts=NOW)
    eventlog.record_temperatures({WD: 41, TOSHIBA: 39}, ts=NOW + 300)

    latest = eventlog.latest_temperatures()

    assert latest[TOSHIBA] == {"celsius": 39, "ts": NOW + 300}
    assert latest[WD] == {"celsius": 41, "ts": NOW + 300}


def test_a_repeated_sample_for_one_second_replaces_rather_than_fails(sandbox):
    eventlog.record_temperature(TOSHIBA, 38, ts=NOW)
    eventlog.record_temperature(TOSHIBA, 40, ts=NOW)

    assert eventlog.latest_temperatures()[TOSHIBA]["celsius"] == 40


def test_the_series_is_averaged_into_buckets(sandbox):
    for i in range(6):
        eventlog.record_temperature(TOSHIBA, 30 + i, ts=NOW + i * 60)

    series = eventlog.temperature_series(NOW, NOW + 600, bucket=300)

    # Two buckets: minutes 0-4 average 32, minute 5 alone is 35.
    assert series[TOSHIBA] == [[NOW - NOW % 300, 32.0], [NOW - NOW % 300 + 300, 35.0]]


def test_gaps_are_preserved_not_filled(sandbox):
    """A stretch with no samples is a stretch where the disk was asleep.
    Filling it in would draw a temperature the disk never had."""
    eventlog.record_temperature(TOSHIBA, 38, ts=NOW)
    eventlog.record_temperature(TOSHIBA, 25, ts=NOW + 7200)

    series = eventlog.temperature_series(NOW, NOW + 7200, bucket=300)

    assert len(series[TOSHIBA]) == 2, "no invented points across the gap"


def test_stats_cover_the_window(sandbox):
    for celsius in (30, 42, 36):
        eventlog.record_temperature(TOSHIBA, celsius, ts=NOW + celsius)

    stats = eventlog.temperature_stats(NOW, NOW + 100)[TOSHIBA]

    assert (stats["min"], stats["max"], stats["samples"]) == (30, 42, 3)
    assert stats["avg"] == 36.0


def test_temperatures_have_their_own_retention(sandbox):
    """A year of readings is a few megabytes and makes summer comparable with
    winter; a year of event rows is just a long list."""
    import time
    now = int(time.time())
    eventlog.record_temperature(TOSHIBA, 38, ts=now - 400 * 86400)
    eventlog.record_temperature(TOSHIBA, 39, ts=now - 10 * 86400)
    eventlog.record(TOSHIBA, eventlog.WAKE, eventlog.EXTERNAL, ts=now - 100 * 86400)

    assert eventlog.prune_temperatures(365) == 1
    assert eventlog.query()[1] == 1, "the event log is untouched"


def test_raw_rows_are_what_the_export_writes(sandbox):
    eventlog.record_temperature(TOSHIBA, 38, ts=NOW)
    eventlog.record_temperature(WD, 41, ts=NOW)

    rows = eventlog.temperature_rows(NOW - 1, NOW + 1)

    assert rows == [(NOW, TOSHIBA, 38), (NOW, WD, 41)]


# --- the sampling round -----------------------------------------------------

@pytest.fixture
def sampling(sandbox, monkeypatch):
    """Three disks: one awake, one asleep, and the untracked system disk."""
    disks = [
        {"by_id": WD, "path": "/dev/sda", "rotational": True, "system": False},
        {"by_id": TOSHIBA, "path": "/dev/sdb", "rotational": True, "system": False},
        {"by_id": SYSTEM, "path": "/dev/sdc", "rotational": False, "system": True},
    ]
    monkeypatch.setattr(disktemp, "list_temp_disks", lambda: disks)
    monkeypatch.setattr(monitor, "_tracked", {
        WD: {"state": sleepconf.ACTIVE},
        TOSHIBA: {"state": sleepconf.STANDBY},
    })
    monkeypatch.setattr(monitor, "_hot", {})
    asked = []
    monkeypatch.setattr(disktemp, "read_temperature",
                        lambda path: asked.append(path) or {"/dev/sda": 41,
                                                            "/dev/sdc": 62}.get(path))
    return asked


def test_the_round_skips_sleeping_disks_and_includes_the_system_disk(sampling):
    from app.storage.models import DiskSleepSettings

    monitor._sample_temperatures(DiskSleepSettings())

    assert sampling == ["/dev/sda", "/dev/sdc"], "the sleeping disk was never asked"
    stored = eventlog.latest_temperatures()
    assert set(stored) == {WD, SYSTEM}


def test_a_hot_disk_is_logged_once_not_every_round(sampling, monkeypatch):
    from app.storage.models import DiskSleepSettings

    settings = DiskSleepSettings(temp_warn_celsius=35, temp_crit_celsius=40)
    for _ in range(3):
        monitor._sample_temperatures(settings)

    rows, total = eventlog.query(disk=WD, event=eventlog.TEMP_HIGH)
    assert total == 1, "one crossing, not one row per sample"
    assert rows[0]["detail"] == "41 C"


def test_a_non_spinning_device_gets_the_shifted_threshold(sampling):
    """62 C is alarming on a platter drive and unremarkable on an NVMe."""
    from app.storage.models import DiskSleepSettings

    monitor._sample_temperatures(DiskSleepSettings())

    assert eventlog.query(disk=SYSTEM, event=eventlog.TEMP_HIGH)[1] == 0
    assert eventlog.latest_temperatures()[SYSTEM]["celsius"] == 62


# --- the endpoints ----------------------------------------------------------

@pytest.fixture
def api_disks(monkeypatch):
    monkeypatch.setattr(disktemp, "list_temp_disks", lambda: [
        {"by_id": WD, "path": "/dev/sda", "model": "WDC", "rotational": True,
         "system": False},
        {"by_id": SYSTEM, "path": "/dev/sdc", "model": "SYSTEM", "rotational": False,
         "system": True},
    ])
    monkeypatch.setattr(disksleep, "list_sleep_disks", lambda: [])
    monkeypatch.setattr(disksleep, "describe", lambda st, ds: {})
    monkeypatch.setattr(disksleep, "hd_idle_running", lambda: False)


def test_current_temperatures_are_served_without_probing(
    auth_client, sandbox, api_disks, monkeypatch
):
    """Opening the page must not issue a SMART command - that is the whole
    reason the readings are stored rather than read on demand."""
    def explode(*args, **kwargs):
        raise AssertionError("the page probed a device")

    monkeypatch.setattr(disktemp, "read_temperature", explode)
    monkeypatch.setattr(disktemp, "run_unchecked", explode)
    eventlog.record_temperature(WD, 47)

    body = auth_client.get("/api/sleep/temps").json()

    disks = {d["by_id"]: d for d in body["disks"]}
    assert disks[WD]["celsius"] == 47
    assert disks[WD]["level"] == "warn"
    assert disks[SYSTEM]["celsius"] is None, "no reading yet, and no probe to get one"
    assert disks[SYSTEM]["system"] is True


def test_history_buckets_and_summarises(auth_client, sandbox, api_disks):
    import time
    now = int(time.time())
    for i in range(20):
        eventlog.record_temperature(WD, 30 + i % 5, ts=now - i * 300)

    body = auth_client.get("/api/sleep/temps/history?window=24h").json()

    assert body["window"] == "24h"
    assert body["bucket"] >= 60
    assert WD in body["series"]
    assert body["stats"][WD]["samples"] == 20


def test_an_unknown_window_is_refused(auth_client, sandbox, api_disks):
    assert auth_client.get("/api/sleep/temps/history?window=forever").status_code == 400


def test_the_csv_export_is_raw_samples(auth_client, sandbox, api_disks):
    """An export exists so the data can be analysed elsewhere, which the
    bucket averaging would foreclose."""
    import time
    now = int(time.time())
    eventlog.record_temperature(WD, 38, ts=now - 60)
    eventlog.record_temperature(WD, 39, ts=now - 30)

    response = auth_client.get("/api/sleep/temps/history?window=24h&format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0] == "timestamp,iso,disk,celsius"
    assert len(lines) == 3, "both raw samples, not one averaged bucket"
    assert lines[1].endswith(f"{WD},38")


def test_the_disks_tab_carries_the_temperature_too(
    auth_client, sandbox, monkeypatch, api_disks
):
    """So the cards render from the one request they already make."""
    monkeypatch.setattr(disksleep, "list_sleep_disks", lambda: [{
        "path": "/dev/sda", "name": "sda", "by_id": WD, "by_id_all": [WD],
        "size": 1, "model": "WDC", "serial": "S", "rotational": True,
        "system": False, "mountpoints": [], "fstypes": ["ext4"],
    }])
    monkeypatch.setattr(disksleep, "describe", lambda st, ds: {
        d["by_id"]: {"warnings": [], "zfs_pool": None} for d in ds})
    monkeypatch.setattr(disksleep, "power_state", lambda path: sleepconf.ACTIVE)
    eventlog.record_temperature(WD, 58)

    body = auth_client.get("/api/sleep").json()

    assert body["disks"][0]["temperature"]["celsius"] == 58
    assert body["disks"][0]["temperature"]["level"] == "crit"
