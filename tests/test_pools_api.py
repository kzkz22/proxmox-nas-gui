"""The deletion/unmount guard, exercised through HTTP.

test_deps.py covers the matching logic; this covers the wiring - that all
three call sites in the pools router actually consult it and turn a hit into
a 409 instead of pulling the filesystem out from under a live share.
"""

import json

import pytest

from app.storage import pools as pool_ops

POOL = {
    "name": "media",
    "mountpoint": "/mnt/pool/media",
    "branches": [{"path": "/mnt/disks/d1", "mode": "RW"}],
}


@pytest.fixture
def no_systemd(monkeypatch):
    """Pretend systemd accepted every unit operation, so the tests never shell
    out to systemctl or mergerfs."""
    monkeypatch.setattr(pool_ops, "_systemctl", lambda *args: True)


@pytest.fixture
def stored(sandbox):
    """Write state.json directly: creating the pool through the API would try
    to mount it."""
    def write(shares=None, pools=None):
        state = {
            "version": 1,
            "shares": shares or {},
            "pools": pools or {},
        }
        (sandbox["PSG_STATE_DIR"] / "state.json").write_text(json.dumps(state))
    return write


def share_at(path: str) -> dict:
    return {"media": {"name": "media", "path": path}}


def test_delete_is_refused_while_a_share_points_at_the_pool(
    auth_client, stored, no_systemd
):
    stored(shares=share_at("/mnt/pool/media"), pools={"media": POOL})

    response = auth_client.delete("/api/pools/media")

    assert response.status_code == 409
    assert "media" in response.json()["detail"]


def test_delete_is_refused_for_a_share_below_the_mountpoint(
    auth_client, stored, no_systemd
):
    stored(shares=share_at("/mnt/pool/media/movies"), pools={"media": POOL})

    assert auth_client.delete("/api/pools/media").status_code == 409


def test_delete_succeeds_without_dependents(auth_client, stored, no_systemd):
    stored(shares=share_at("/srv/elsewhere"), pools={"media": POOL})

    assert auth_client.delete("/api/pools/media").status_code == 200
    assert auth_client.get("/api/pools").json()["pools"] == {}


def test_a_sibling_path_does_not_block_deletion(auth_client, stored, no_systemd):
    """/mnt/pool/media2 only shares a prefix with the mountpoint."""
    stored(shares=share_at("/mnt/pool/media2"), pools={"media": POOL})

    assert auth_client.delete("/api/pools/media").status_code == 200


def test_unmount_is_refused_while_a_share_depends_on_the_pool(
    auth_client, stored, no_systemd
):
    stored(shares=share_at("/mnt/pool/media"), pools={"media": POOL})

    response = auth_client.post("/api/pools/media/unmount")

    assert response.status_code == 409


def test_moving_the_mountpoint_is_refused_while_a_share_depends_on_it(
    auth_client, stored, no_systemd
):
    stored(shares=share_at("/mnt/pool/media"), pools={"media": POOL})

    response = auth_client.put(
        "/api/pools/media", json={**POOL, "mountpoint": "/mnt/pool/elsewhere"}
    )

    assert response.status_code == 409
    assert "mountpoint" in response.json()["detail"]


def test_unknown_pool_is_a_404_not_a_409(auth_client, stored, no_systemd):
    stored()
    assert auth_client.delete("/api/pools/nope").status_code == 404
