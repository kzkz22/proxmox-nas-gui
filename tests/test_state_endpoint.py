"""GET /api/state is assembled from two independent halves, so its shape is
pinned here: the frontend reads every one of these keys, and a half silently
dropping out of the merge would surface as undefined in the UI rather than as
an error.
"""

import json
import shutil
from pathlib import Path

from app.models import State
from app.samba.state_view import state_view as samba_view
from app.storage.state_view import state_view as storage_view

EXPECTED_KEYS = {
    "settings",
    "shares",
    "users",
    "groups",
    "service",
    "pools",
    "disk_mounts",
    "bind_mounts",
}

FIXTURE = Path(__file__).parent / "fixtures" / "state_v1.json"


def test_the_two_halves_do_not_overlap():
    """The merge is a plain dict update, so an overlapping key would drop one
    half's value. The endpoint raises on this; the halves must not collide in
    the first place."""
    st = State.model_validate(json.loads(FIXTURE.read_text()))
    assert samba_view(st).keys() & storage_view(st).keys() == set()


def test_the_halves_together_cover_the_payload():
    st = State()
    assert samba_view(st).keys() | storage_view(st).keys() == EXPECTED_KEYS


def test_empty_state_still_returns_every_key(auth_client, sandbox):
    response = auth_client.get("/api/state")
    assert response.status_code == 200
    assert set(response.json()) == EXPECTED_KEYS


def test_stored_state_is_reported(auth_client, sandbox):
    shutil.copy(FIXTURE, sandbox["PNAS_STATE_DIR"] / "state.json")

    body = auth_client.get("/api/state").json()

    assert set(body) == EXPECTED_KEYS
    assert set(body["shares"]) == {"media", "public"}
    assert body["shares"]["media"]["path"] == "/mnt/pool/media"
    assert set(body["users"]) == {"alice", "bob"}
    assert set(body["groups"]) == {"family"}
    assert set(body["pools"]) == {"media"}
    assert set(body["disk_mounts"]) == {"d1", "d2"}
    assert set(body["bind_mounts"]) == {"kz-fontos", "kz-nemfontos"}
    assert body["settings"]["workgroup"] == "HOMELAB"


def test_pools_are_reported_with_live_mount_state(auth_client, sandbox):
    """pool_info decorates the stored pool with facts read from the running
    system, which are not in state.json."""
    shutil.copy(FIXTURE, sandbox["PNAS_STATE_DIR"] / "state.json")

    pool = auth_client.get("/api/state").json()["pools"]["media"]

    assert pool["mounted"] is False          # nothing is mounted under tmp_path
    assert [b["path"] for b in pool["branch_usage"]] == [
        "/mnt/disks/d1", "/mnt/disks/d2", "/mnt/disks/d3",
    ]
    assert pool["create_policy"] == "epmfs"
