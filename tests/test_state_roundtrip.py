"""Pins the on-disk shape of /etc/proxmox-nas-gui/state.json.

state.json is the source of truth for both the generated Samba config and the
mergerfs pool units, and it is never migrated on read (state.load_state just
validates it), so any accidental change to a field name or a default would
silently drop a user's configuration. The golden fixture covers every field of
every model, so moving model definitions between modules cannot change the
serialised form without failing here.
"""

import json
from pathlib import Path

from app.models import State
from app.samba.models import GlobalSettings, GroupInfo, Share, UserInfo
from app.storage.models import (
    BindMount,
    Branch,
    DiskMount,
    DiskSleepPolicy,
    DiskSleepSettings,
    Pool,
)

FIXTURE = Path(__file__).parent / "fixtures" / "state_v1.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_fixture_round_trips_unchanged():
    raw = load_fixture()
    assert State.model_validate(raw).model_dump(mode="json") == raw


def test_fixture_covers_every_state_field():
    """Guards the guard: a new top-level field must be added to the fixture,
    otherwise this test would keep passing while covering nothing."""
    assert set(load_fixture()) == set(State.model_fields)


def test_fixture_covers_every_field_of_every_nested_model():
    raw = load_fixture()

    assert set(raw["settings"]) == set(GlobalSettings.model_fields)
    assert set(raw["shares"]["media"]) == set(Share.model_fields)
    assert set(raw["users"]["alice"]) == set(UserInfo.model_fields)
    assert set(raw["groups"]["family"]) == set(GroupInfo.model_fields)
    assert set(raw["pools"]["media"]) == set(Pool.model_fields)
    assert set(raw["pools"]["media"]["branches"][0]) == set(Branch.model_fields)
    assert set(raw["disk_mounts"]["d1"]) == set(DiskMount.model_fields)
    assert set(raw["bind_mounts"]["kz-fontos"]) == set(BindMount.model_fields)
    assert set(raw["disk_sleep"]["ata-TOSHIBA_DT01ACA300_334401VAS"]) == set(
        DiskSleepPolicy.model_fields
    )
    assert set(raw["disk_sleep_settings"]) == set(DiskSleepSettings.model_fields)


def test_empty_state_serialises_with_all_keys():
    """A fresh install writes State() straight out; it must still be a
    complete document rather than an empty object."""
    assert set(State().model_dump(mode="json")) == set(State.model_fields)


def test_defaults_are_not_silently_changed():
    st = State()
    assert st.version == 1
    assert st.settings.workgroup == "WORKGROUP"
    assert st.settings.min_protocol == "SMB2"
    assert st.shares == {} and st.pools == {} and st.disk_mounts == {}
    assert st.bind_mounts == {}
    # A disk nobody configured keeps spinning: the monitor must never spin a
    # disk down because a default said so.
    assert st.disk_sleep == {}
    assert st.disk_sleep_settings.default_idle_seconds == 0
