"""The disk power-state event log.

The log is the feature: hd-idle could already spin disks down, but it could
never answer "what woke this disk at 04:41, three nights running". These
tests cover the two things that makes possible - filtered paging over the
rows, and turning the transitions back into a timeline.
"""

import time

import pytest

from app.storage import eventlog

DISK = "ata-TOSHIBA_DT01ACA300_334401VAS"
OTHER = "ata-WDC_WD10SPCX-21KHST0_WD-WX21A44D9471"
NOW = 1_770_000_000


@pytest.fixture
def log(sandbox):
    """An empty database under tmp_path. sandbox points PNAS_LOG_DB there."""
    return eventlog


def test_events_come_back_newest_first(log):
    log.record(DISK, log.SLEEP, log.TIMEOUT, ts=NOW - 100)
    log.record(DISK, log.WAKE, log.EXTERNAL, ts=NOW)

    rows, total = log.query()

    assert total == 2
    assert [r["event"] for r in rows] == [log.WAKE, log.SLEEP]


def test_every_filter_narrows_the_result(log):
    log.record(DISK, log.SLEEP, log.MANUAL, actor="root", detail="hdparm ok", ts=NOW)
    log.record(DISK, log.WAKE, log.EXTERNAL, detail="reads=1284 writes=0", ts=NOW + 1)
    log.record(OTHER, log.SLEEP, log.TIMEOUT, detail="sg_start ok", ts=NOW + 2)

    assert log.query(disk=OTHER)[1] == 1
    assert log.query(event=log.SLEEP)[1] == 2
    assert log.query(reason=log.MANUAL)[1] == 1
    assert log.query(since=NOW + 2)[1] == 1
    assert log.query(until=NOW)[1] == 1
    assert log.query(text="sg_start")[1] == 1
    assert log.query(text="root")[1] == 1, "the actor is searchable too"
    assert log.query(disk=DISK, event=log.SLEEP)[1] == 1, "filters combine"


def test_paging_reports_the_full_total(log):
    for i in range(30):
        log.record(DISK, log.WAKE, log.EXTERNAL, ts=NOW + i)

    rows, total = log.query(limit=25, offset=25)

    assert total == 30, "the total is the match count, not the page size"
    assert len(rows) == 5


def test_disks_seen_includes_ones_no_longer_installed(log):
    log.record("ata-PULLED-DISK", log.SLEEP, log.TIMEOUT, ts=NOW)
    assert log.disks_seen() == ["ata-PULLED-DISK"]


def test_the_timeline_is_built_from_transitions(log):
    log.record(DISK, log.SLEEP, log.TIMEOUT, ts=NOW + 100)
    log.record(DISK, log.WAKE, log.EXTERNAL, ts=NOW + 400)

    segments = log.timeline(DISK, NOW, NOW + 500)

    assert [(s["state"], s["end"] - s["start"]) for s in segments] == [
        ("unknown", 100),   # nothing known before the first event
        ("asleep", 300),
        ("awake", 100),
    ]


def test_the_timeline_carries_state_in_from_before_the_window(log):
    log.record(DISK, log.SLEEP, log.TIMEOUT, ts=NOW - 5000)

    segments = log.timeline(DISK, NOW, NOW + 500)

    assert [s["state"] for s in segments] == ["asleep"]
    assert segments[0]["end"] - segments[0]["start"] == 500


def test_an_empty_log_falls_back_to_the_current_state(log):
    segments = log.timeline(DISK, NOW, NOW + 500, current="awake")
    assert [s["state"] for s in segments] == ["awake"]


def test_the_summary_adds_up_asleep_time_across_disks(log):
    log.record(DISK, log.SLEEP, log.TIMEOUT, ts=NOW)
    log.record(OTHER, log.SLEEP, log.TIMEOUT, ts=NOW)
    log.record(OTHER, log.WAKE, log.EXTERNAL, ts=NOW + 200)

    summary = log.summary(NOW, NOW + 400, {
        d: log.timeline(d, NOW, NOW + 400) for d in (DISK, OTHER)
    })

    assert summary["asleep_seconds"] == 400 + 200
    assert summary["wake_count"] == 1


def test_the_summary_of_no_disks_is_not_an_error(log):
    assert log.summary(NOW, NOW + 400, {}) == {"asleep_seconds": 0, "wake_count": 0}


def test_pruning_drops_only_the_old_rows(log):
    now = int(time.time())
    log.record(DISK, log.WAKE, log.EXTERNAL, ts=now - 100 * 86400)
    log.record(DISK, log.WAKE, log.EXTERNAL, ts=now - 10 * 86400)

    assert log.prune(90) == 1
    assert log.query()[1] == 1
