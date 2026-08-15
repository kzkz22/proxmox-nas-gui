"""The deletion/unmount guard, exercised through HTTP.

test_deps.py covers the matching logic; this covers the wiring - that all
three call sites in the pools router actually consult it and turn a hit into
a 409 instead of pulling the filesystem out from under a live share.
"""

import json

import pytest

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


# --- remount-only options -----------------------------------------------

def make_pool(**kwargs) -> Pool:
    return Pool(name="media", mountpoint="/mnt/pool/media",
                branches=[Branch(path="/mnt/disks/d1")], **kwargs)


def test_changing_writeback_warns_that_a_remount_is_needed():
    # cache.writeback is negotiated when the FUSE connection is set up, so a
    # save cannot apply it to a running pool - saying nothing would leave the
    # user benchmarking a setting they are not actually running.
    warning = pool_ops.remount_only_warning(
        make_pool(cache_writeback=True), make_pool(), "/nonexistent/.mergerfs"
    )

    assert warning is not None
    assert "cache.writeback=true" in warning


def test_changed_extra_options_warn_too():
    warning = pool_ops.remount_only_warning(
        make_pool(extra_options="func.getattr=newest"), make_pool(),
        "/nonexistent/.mergerfs",
    )

    assert warning is not None
    assert "func.getattr=newest" in warning


def test_untouched_options_do_not_warn():
    # A save that leaves the remount-only fields alone has nothing to report,
    # however they happen to be set - otherwise every single save of a pool
    # with extra options would nag.
    pool = make_pool(cache_writeback=True, extra_options="func.getattr=newest")

    assert pool_ops.remount_only_warning(pool, pool, "/nonexistent/.mergerfs") is None


def test_runtime_update_on_an_unmounted_pool_is_a_no_op(monkeypatch):
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)

    assert pool_ops.runtime_update(make_pool(cache_writeback=True), make_pool()) is None


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
