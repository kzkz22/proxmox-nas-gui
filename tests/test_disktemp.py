"""Temperature reading, and the guarantee that it never wakes a disk.

Two things are being pinned here. The first is a parsing trap: a drive in
standby still answers with a JSON document, and its temperature is frequently
0 rather than absent. The second is the rule the whole feature rests on - a
SMART read spins a sleeping drive back up, so a sleeping drive must never be
asked.
"""

import json

import pytest

from app.core.proc import run_unchecked
from app.storage import disktemp, sleepconf

TOSHIBA = "ata-TOSHIBA_DT01ACA300_334401VAS"
WD = "ata-WDC_WD10SPCX-21KHST0_WD-WX21A44D9471"


def smart(temperature=None, nvme=None, messages=()):
    doc = {"smartctl": {"version": [7, 3], "messages": [
        {"string": m, "severity": "information"} for m in messages]}}
    if temperature is not None:
        doc["temperature"] = {"current": temperature}
    if nvme is not None:
        doc["nvme_smart_health_information_log"] = {"temperature": nvme}
    return json.dumps(doc)


# --- parsing ----------------------------------------------------------------

def test_a_normal_reading_is_returned():
    assert sleepconf.parse_smart_temperature(smart(38)) == 38


def test_the_nvme_section_is_used_when_the_common_one_is_absent():
    assert sleepconf.parse_smart_temperature(smart(nvme=52)) == 52


def test_a_standby_zero_is_not_a_temperature():
    """The trap. A sleeping drive answers with temperature.current = 0, and
    recording that would put a freezing-point sample into the history of a
    disk that was merely asleep - dragging every average down and drawing a
    cliff on the chart."""
    assert sleepconf.parse_smart_temperature(
        smart(0, messages=["Device is in STANDBY mode, exit(2)"])) is None


@pytest.mark.parametrize("output", [
    "", "not json", "[]", json.dumps({}), smart(), smart(-5), smart(999),
    json.dumps({"temperature": {"current": None}}),
    json.dumps({"temperature": {"current": "warm"}}),
    json.dumps({"temperature": {"current": True}}),
])
def test_everything_doubtful_is_no_reading(output):
    """None rather than a number in every unclear case: the value goes into a
    history that is averaged and charted, where a gap is honest and an
    invented sample is not."""
    assert sleepconf.parse_smart_temperature(output) is None


def test_the_standby_message_is_recognised():
    assert sleepconf.smart_said_standby(smart(0, messages=["Device is in STANDBY mode"]))
    assert not sleepconf.smart_said_standby(smart(38))


@pytest.mark.parametrize("celsius,expected", [
    (30, "ok"), (44, "ok"), (45, "warn"), (54, "warn"), (55, "crit"), (70, "crit"),
])
def test_levels_follow_the_thresholds(celsius, expected):
    assert sleepconf.temperature_level(celsius, 45, 55) == expected


# --- bucket sizing ----------------------------------------------------------

def test_buckets_keep_a_long_window_small():
    """A year of five-minute samples is 105k points; nobody can see that many
    and no browser should be asked to draw them."""
    year = 365 * 86400
    bucket = sleepconf.bucket_seconds(year, 500)

    assert year / bucket <= 500
    assert bucket % 60 == 0, "bucket boundaries land on whole minutes"


def test_a_short_window_is_not_bucketed_below_the_sampling_rate():
    assert sleepconf.bucket_seconds(3600, 500) == 60


# --- the device side --------------------------------------------------------

class FakeSmart:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, cmd, timeout=30):
        self.calls.append(cmd)
        path = cmd[-1]
        if path not in self.answers:
            return 2, smart(0, messages=["Device is in STANDBY mode, exit(2)"]), ""
        return 0, smart(self.answers[path]), ""

    @property
    def paths(self):
        return [c[-1] for c in self.calls]


def test_the_standby_guard_is_always_on_the_command_line(monkeypatch):
    """The second of the two guards. Even though the caller already skips
    sleeping disks, the command itself must refuse to wake one that fell
    asleep in between."""
    fake = FakeSmart({"/dev/sdb": 38})
    monkeypatch.setattr(disktemp, "run_unchecked", fake)

    disktemp.read_temperature("/dev/sdb")

    assert fake.calls[0][:5] == ["smartctl", "-j", "-n", "standby", "-A"]


def test_a_sleeping_disk_is_never_asked(monkeypatch):
    """The guarantee the whole feature rests on: a SMART read spins a sleeping
    drive back up, so the sampler must not issue one."""
    fake = FakeSmart({"/dev/sda": 41, "/dev/sdb": 38})
    monkeypatch.setattr(disktemp, "run_unchecked", fake)
    disks = [
        {"by_id": WD, "path": "/dev/sda"},
        {"by_id": TOSHIBA, "path": "/dev/sdb"},
    ]

    readings = disktemp.sample(disks, lambda d: d["by_id"] != TOSHIBA)

    assert readings == {WD: 41}
    assert fake.paths == ["/dev/sda"], "the sleeping disk was not touched"


def test_a_disk_with_no_readable_temperature_is_simply_absent(monkeypatch):
    monkeypatch.setattr(disktemp, "run_unchecked", FakeSmart({}))

    readings = disktemp.sample([{"by_id": WD, "path": "/dev/sda"}], lambda d: True)

    assert readings == {}


def test_probe_explains_an_empty_reading(monkeypatch):
    monkeypatch.setattr(disktemp, "run_unchecked",
                        lambda cmd, timeout=30: (2, smart(0, messages=["STANDBY"]), ""))
    assert disktemp.probe("/dev/sdb")["reason"] == "standby"

    monkeypatch.setattr(disktemp, "run_unchecked",
                        lambda cmd, timeout=30: (-1, "", "command not found: smartctl"))
    assert disktemp.probe("/dev/sdb")["reason"] == "smartctl_missing"


def test_the_system_disk_is_listed_here_and_marked(monkeypatch):
    """It appears on this list and nowhere else: it never stops spinning, so
    it is usually the hottest drive in the box."""
    monkeypatch.setattr(disktemp.disksleep, "list_sleep_disks", lambda: [
        {"path": "/dev/sdb", "by_id": TOSHIBA, "model": "TOSHIBA", "rotational": True},
    ])
    monkeypatch.setattr(disktemp.disksleep, "lsblk_disks", lambda: [
        {"path": "/dev/sda", "model": "SYSTEM", "rotational": False},
        {"path": "/dev/sdb", "model": "TOSHIBA", "rotational": True},
    ])
    monkeypatch.setattr(disktemp.disksleep.pools, "by_id_map",
                        lambda: {"/dev/sda": ["ata-SYSTEM"], "/dev/sdb": [TOSHIBA]})
    monkeypatch.setattr(disktemp.disksleep, "resolve_device", lambda p: p)

    found = {d["by_id"]: d for d in disktemp.list_temp_disks()}

    assert found[TOSHIBA]["system"] is False
    assert found["ata-SYSTEM"]["system"] is True


# --- run_unchecked ----------------------------------------------------------

def test_a_nonzero_exit_is_data_not_an_error():
    """smartctl returns a bitmask; bit 1 means "in standby", which is the
    answer, and bit 6 means "old errors in the log", which is irrelevant.
    run() would raise on both."""
    code, out, _err = run_unchecked(["sh", "-c", "echo hi; exit 66"])

    assert (code, out.strip()) == (66, "hi")


def test_a_missing_binary_comes_back_as_a_value():
    code, _out, err = run_unchecked(["definitely-not-a-real-binary"])

    assert code == -1
    assert "not found" in err
