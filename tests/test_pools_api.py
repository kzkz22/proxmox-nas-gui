"""The deletion/unmount guard, exercised through HTTP.

test_deps.py covers the matching logic; this covers the wiring - that all
three call sites in the pools router actually consult it and turn a hit into
a 409 instead of pulling the filesystem out from under a live share.
"""

import json

import pytest

from app.storage import binds as bind_ops
from app.storage import pools as pool_ops
from app.storage.models import Branch, Pool

POOL = {
    "name": "media",
    "mountpoint": "/mnt/pool/media",
    "branches": [{"path": "/mnt/disks/d1", "mode": "RW"}],
}


@pytest.fixture
def no_systemd(monkeypatch):
    """Pretend systemd accepted every unit operation, so the tests never shell
    out to systemctl or mergerfs."""
    monkeypatch.setattr(pool_ops, "systemctl", lambda *args: True)


@pytest.fixture
def stored(sandbox):
    """Write state.json directly: creating the pool through the API would try
    to mount it."""
    def write(shares=None, pools=None, bind_mounts=None):
        state = {
            "version": 1,
            "shares": shares or {},
            "pools": pools or {},
            "bind_mounts": bind_mounts or {},
        }
        (sandbox["PNAS_STATE_DIR"] / "state.json").write_text(json.dumps(state))
    return write


def share_at(path: str) -> dict:
    return {"media": {"name": "media", "path": path}}


def make_pool(**kwargs) -> Pool:
    return Pool(name="media", mountpoint="/mnt/pool/media",
                branches=[Branch(path="/mnt/disks/d1")], **kwargs)


def fake_live(monkeypatch, **overrides):
    """Stand in for what the running mergerfs reports, defaulting to a pool
    running the GUI defaults. Keyed by option so passthrough does not read
    back as "false" - a pool that answers the wrong shape for one option
    would otherwise look like drift on every check."""
    live = {"cache.files": "auto-full", "cache.writeback": "false",
            "dropcacheonclose": "false", "passthrough": "off"}
    live.update(overrides)
    monkeypatch.setattr(pool_ops, "live_option", lambda _mp, key: live.get(key))


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


def test_removing_a_branch_warns_when_it_orphans_a_bind_source(
    auth_client, sandbox, stored, no_systemd, tmp_path
):
    """kz lives only on d2 (e.g. mergerfs's epmfs create policy put it there
    when d2 had more free space); dropping d2 makes the union - and the bind
    mount that presents it - lose that folder, so the edit should say so."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d2 / "kz").mkdir()
    pool = {
        "name": "bulk",
        "mountpoint": str(tmp_path / "pool" / "bulk"),
        "branches": [{"path": str(d1), "mode": "RW"}, {"path": str(d2), "mode": "RW"}],
    }
    stored(pools={"bulk": pool}, bind_mounts={"kz-bulk": {
        "name": "kz-bulk", "source": f"{pool['mountpoint']}/kz",
        "target": "/mnt/family_pool/kz/bulk",
    }})

    response = auth_client.put(
        "/api/pools/bulk", json={**pool, "branches": [{"path": str(d1), "mode": "RW"}]}
    )

    assert response.status_code == 200
    assert "kz-bulk" in response.json()["warning"]


def test_removing_a_branch_does_not_warn_when_the_folder_survives(
    auth_client, sandbox, stored, no_systemd, tmp_path
):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d1 / "kz").mkdir()
    (d2 / "kz").mkdir()
    pool = {
        "name": "bulk",
        "mountpoint": str(tmp_path / "pool" / "bulk"),
        "branches": [{"path": str(d1), "mode": "RW"}, {"path": str(d2), "mode": "RW"}],
    }
    stored(pools={"bulk": pool}, bind_mounts={"kz-bulk": {
        "name": "kz-bulk", "source": f"{pool['mountpoint']}/kz",
        "target": "/mnt/family_pool/kz/bulk",
    }})

    response = auth_client.put(
        "/api/pools/bulk", json={**pool, "branches": [{"path": str(d1), "mode": "RW"}]}
    )

    assert response.status_code == 200
    assert response.json()["warning"] is None


# --- what the running pool could not adopt ------------------------------

def test_nothing_to_report_when_the_pool_is_not_mounted(monkeypatch):
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)

    result = pool_ops.runtime_update(make_pool(cache_writeback=True))

    assert result == {"warning": None, "remount_needed": []}


def test_a_mount_time_option_the_pool_is_not_running_is_reported(monkeypatch):
    # cache.writeback is negotiated at FUSE connection setup, so a save
    # cannot apply it - and staying quiet would leave someone benchmarking a
    # setting they are not running.
    fake_live(monkeypatch)

    stale = pool_ops.remount_needed(make_pool(cache_writeback=True), "/ctl")

    assert stale == ["cache.writeback=true"]


def test_a_mount_time_option_already_running_is_not_reported(monkeypatch):
    fake_live(monkeypatch)

    assert pool_ops.remount_needed(make_pool(), "/ctl") == []


def test_extra_options_the_pool_accepts_are_not_reported(monkeypatch, tmp_path):
    # cache.attr and friends apply instantly through the control file.
    # Telling the user to remount for them would be simply untrue, so they
    # are pushed and only reported if the push is refused.
    fake_live(monkeypatch)
    pushed = {}
    monkeypatch.setattr(pool_ops.os, "setxattr",
                        lambda _ctl, key, value: pushed.__setitem__(key, value))
    pool = make_pool(extra_options="cache.statfs=60,cache.attr=300")

    assert pool_ops.remount_needed(pool, "/ctl") == []
    assert pushed == {
        "user.mergerfs.cache.statfs": b"60",
        "user.mergerfs.cache.attr": b"300",
    }


def test_extra_options_the_pool_refuses_are_reported(monkeypatch):
    fake_live(monkeypatch)

    def refuse(_ctl, _key, _value):
        raise OSError("not a runtime option")
    monkeypatch.setattr(pool_ops.os, "setxattr", refuse)

    stale = pool_ops.remount_needed(make_pool(extra_options="fuse_msg_size=1M"), "/ctl")

    assert stale == ["fuse_msg_size=1M"]


def test_extra_options_reported_when_unchanged_too(monkeypatch):
    # Deliberately not "only report what this save changed": a pool that has
    # been running the wrong setting for a week should say so every time,
    # now that the client offers to remount.
    fake_live(monkeypatch)

    stale = pool_ops.remount_needed(make_pool(passthrough="rw"), "/ctl")

    assert stale == ["passthrough=rw"]


# --- option drift against the running mount -----------------------------

def test_no_drift_when_the_pool_is_not_mounted(monkeypatch):
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)

    assert pool_ops.option_drift(make_pool(cache_writeback=True)) == []


def _fake_live(monkeypatch, values):
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: True)
    monkeypatch.setattr(pool_ops, "live_option",
                        lambda _mp, key: values.get(key))


def test_drift_reports_what_the_mount_is_actually_running(monkeypatch):
    # The case this exists for: writeback was saved, but mergerfs can only
    # take it at mount time, so the pool is still running without it.
    _fake_live(monkeypatch, {
        "cache.files": "auto-full", "cache.writeback": "false",
        "dropcacheonclose": "false",
    })

    drift = pool_ops.option_drift(make_pool(cache_writeback=True))

    assert drift == ["cache.writeback: false -> true"]


def test_matching_options_are_not_drift(monkeypatch):
    _fake_live(monkeypatch, {
        "cache.files": "auto-full", "cache.writeback": "false",
        "dropcacheonclose": "false",
    })

    assert pool_ops.option_drift(make_pool()) == []


def test_options_the_mount_will_not_report_are_skipped(monkeypatch):
    # passthrough reads back as None on mergerfs older than 2.41. That is not
    # drift - there is nothing to remount into - and reporting it would give
    # every 2.40 user a permanent unfixable warning.
    _fake_live(monkeypatch, {
        "cache.files": "auto-full", "cache.writeback": "false",
        "dropcacheonclose": "false", "passthrough": None,
    })

    assert pool_ops.option_drift(make_pool()) == []


def test_extra_options_win_over_the_field_in_drift(monkeypatch):
    # The pool field says auto-full, extra_options overrides it to partial;
    # a mount running partial is correct and must not be reported.
    _fake_live(monkeypatch, {
        "cache.files": "partial", "cache.writeback": "false",
        "dropcacheonclose": "false",
    })

    assert pool_ops.option_drift(make_pool(extra_options="cache.files=partial")) == []


# --- the remount endpoint -----------------------------------------------

def test_remount_restarts_the_pools_bind_mounts(
    auth_client, stored, no_systemd, monkeypatch, tmp_path
):
    # The bind units declare Requires= on the pool service, so stopping the
    # pool stops them. A remount that left them down would hand Samba an
    # empty directory over what still looks like a working share.
    mountpoint = str(tmp_path / "pool" / "bulk")
    pool = {"name": "bulk", "mountpoint": mountpoint,
            "branches": [{"path": str(tmp_path / "d1"), "mode": "RW"}]}
    stored(pools={"bulk": pool}, bind_mounts={"kz": {
        "name": "kz", "source": f"{mountpoint}/kz", "target": "/mnt/tree/kz",
    }})
    monkeypatch.setattr(pool_ops, "remount_pool", lambda _p: None)
    monkeypatch.setattr(pool_ops, "write_pool_unit", lambda _p: None)
    monkeypatch.setattr(bind_ops, "mount_bind", lambda _b: None)

    response = auth_client.post("/api/pools/bulk/remount")

    assert response.status_code == 200
    assert response.json()["binds_restarted"] == ["kz"]


def test_remounting_an_unknown_pool_is_a_404(auth_client, stored, no_systemd):
    stored()
    assert auth_client.post("/api/pools/nope/remount").status_code == 404
