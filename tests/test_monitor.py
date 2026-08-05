"""The monitor loop, one tick at a time.

tick() is synchronous on purpose, so it can be driven directly here with a
fake clock and fake devices. The behaviours worth pinning are the ones that
would be invisible in production until they bit: an idle clock that never
resets, a restart that logs a wave of phantom transitions, and a drive that
refuses to sleep being retried once per tick forever.
"""

import pytest

from app.core import state as state_store
from app.storage import disksleep, eventlog, monitor, sleepconf
from app.storage.models import DiskSleepPolicy

DISK = "ata-TOSHIBA_DT01ACA300_334401VAS"
PATH = "/dev/sdb"


class Fake:
    """The world as one tick sees it: a clock, a power state and counters."""

    def __init__(self):
        self.now = 1_770_000_000.0
        self.state = sleepconf.ACTIVE
        self.counters = (100, 50)
        self.spin_calls = []
        self.spin_result = (True, "hdparm", "hdparm ok")

    def advance(self, seconds):
        self.now += seconds

    def spin_down(self, path, preferred=None):
        self.spin_calls.append((path, preferred))
        if self.spin_result[0]:
            self.state = sleepconf.STANDBY
        return self.spin_result


@pytest.fixture
def world(sandbox, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(monitor, "_tracked", {})
    monkeypatch.setattr(monitor, "_last_prune", fake.now)
    monkeypatch.setattr(monitor.time, "time", lambda: fake.now)
    monkeypatch.setattr(disksleep, "list_sleep_disks", lambda: [{
        "path": PATH, "name": "sdb", "by_id": DISK, "rotational": True,
        "size": 1, "model": "TOSHIBA", "serial": "S", "system": False,
        "mountpoints": [], "fstypes": ["ext4"], "by_id_all": [DISK],
    }])
    monkeypatch.setattr(disksleep, "read_diskstats", lambda: {"sdb": fake.counters})
    monkeypatch.setattr(disksleep, "power_state", lambda path: fake.state)
    monkeypatch.setattr(disksleep, "spin_down", fake.spin_down)
    return fake


def set_policy(idle_seconds, **settings):
    st = state_store.load_state()
    st.disk_sleep[DISK] = DiskSleepPolicy(idle_seconds=idle_seconds)
    for key, value in settings.items():
        setattr(st.disk_sleep_settings, key, value)
    state_store.save_state(st)


# --- the idle clock ---------------------------------------------------------

def test_a_disk_is_spun_down_once_the_idle_time_has_passed(world):
    set_policy(900)

    monitor.tick()
    assert world.spin_calls == [], "the clock starts at the first tick"

    world.advance(901)
    monitor.tick()

    assert world.spin_calls == [(PATH, None)]
    rows, _ = eventlog.query(disk=DISK)
    assert rows[0]["event"] == eventlog.SLEEP
    assert rows[0]["reason"] == eventlog.TIMEOUT
    assert "idle 900s" in rows[0]["detail"]


def test_io_resets_the_idle_clock(world):
    """The counters are the whole idle measurement; a disk that is being read
    must never be spun down just because enough wall time has passed."""
    set_policy(900)
    monitor.tick()

    for _ in range(3):
        world.advance(400)
        world.counters = (world.counters[0] + 1, world.counters[1])
        monitor.tick()

    assert world.spin_calls == []


def test_a_disk_set_to_never_is_left_alone(world):
    set_policy(0)
    monitor.tick()
    world.advance(100_000)
    monitor.tick()

    assert world.spin_calls == []


def test_disabling_the_monitor_stops_spin_downs_but_not_logging(world):
    set_policy(900, enabled=False)
    monitor.tick()
    world.advance(901)
    world.state = sleepconf.STANDBY  # something else put it to sleep
    monitor.tick()

    assert world.spin_calls == []
    rows, _ = eventlog.query(disk=DISK)
    assert rows[0]["reason"] == eventlog.EXTERNAL


# --- transitions ------------------------------------------------------------

def test_the_first_tick_logs_nothing(world):
    """A restart rebuilds the tracker from scratch. Without this the first
    tick after every service restart would log a transition for every disk."""
    world.state = sleepconf.STANDBY
    monitor.tick()

    assert eventlog.query()[1] == 0


def test_a_wake_up_records_the_io_that_came_with_it(world):
    world.state = sleepconf.STANDBY
    monitor.tick()

    world.advance(60)
    world.state = sleepconf.ACTIVE
    world.counters = (100, 146)
    monitor.tick()

    rows, _ = eventlog.query(disk=DISK)
    assert rows[0]["event"] == eventlog.WAKE
    assert rows[0]["reason"] == eventlog.EXTERNAL
    # Pure writes: a snapshot, an atime update or a scrub - never a person
    # browsing a share. That distinction is the point of recording it.
    assert rows[0]["detail"] == "reads=0 writes=96"


def test_a_spin_down_by_the_drive_itself_is_marked_external(world):
    monitor.tick()
    world.advance(60)
    world.state = sleepconf.STANDBY
    monitor.tick()

    rows, _ = eventlog.query(disk=DISK)
    assert (rows[0]["event"], rows[0]["reason"]) == (eventlog.SLEEP, eventlog.EXTERNAL)


def test_our_own_spin_down_is_not_logged_twice(world):
    set_policy(900)
    monitor.tick()
    world.advance(901)
    monitor.tick()          # spins it down, logs timeout
    world.advance(60)
    monitor.tick()          # sees standby - must not log it again

    events = [r["reason"] for r in eventlog.query(disk=DISK)[0]]
    assert events == [eventlog.TIMEOUT]


def test_an_unreadable_state_is_never_reported_as_a_transition(world):
    monitor.tick()
    world.advance(60)
    world.state = sleepconf.UNKNOWN
    monitor.tick()

    assert eventlog.query()[1] == 0


# --- failure handling -------------------------------------------------------

def test_a_drive_that_refuses_to_sleep_is_not_retried_every_tick(world):
    world.spin_result = (False, None, "all three methods failed")
    set_policy(900)
    monitor.tick()
    world.advance(901)
    monitor.tick()

    for _ in range(5):
        world.advance(60)
        monitor.tick()

    assert len(world.spin_calls) == 1, "backed off after the failure"
    rows, total = eventlog.query(disk=DISK)
    assert total == 1 and rows[0]["event"] == eventlog.SLEEP_FAILED

    world.advance(monitor.RETRY_AFTER)
    monitor.tick()
    assert len(world.spin_calls) == 2, "retried once the backoff expired"


def test_the_working_method_is_persisted_for_the_next_attempt(world):
    world.spin_result = (True, "sg_start", "hdparm failed; sg_start ok")
    set_policy(900)
    monitor.tick()
    world.advance(901)
    monitor.tick()

    assert state_store.load_state().disk_sleep[DISK].method == "sg_start"


def test_a_removed_disk_stops_being_tracked(world, monkeypatch):
    monitor.tick()
    assert DISK in monitor.snapshot()

    monkeypatch.setattr(disksleep, "list_sleep_disks", lambda: [])
    monitor.tick()

    assert monitor.snapshot() == {}
