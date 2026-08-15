"""Wiring tests for the bind mount endpoints.

The mount itself is stubbed out - what matters here is that the validation
refuses the arrangements that would corrupt the presentation tree, that the
unit file lands where the boot depends on it, and that nothing lets a live
share lose its storage.
"""

import json

import pytest

from app.storage import binds as bind_ops
from app.storage import pools as pool_ops

BIND = {
    "name": "kz-fontos",
    "source": "/mnt/fontos/kz",
    "target": "/mnt/family_pool/kz/fontos",
}


@pytest.fixture
def no_systemd(monkeypatch):
    """Pretend systemd accepted every unit operation, and that it is not
    running - so mount_bind takes its documented no-systemd path instead of
    shelling out to systemctl."""
    monkeypatch.setattr(pool_ops, "systemctl", lambda *args: True)
    monkeypatch.setattr(pool_ops, "has_systemd", lambda: False)


@pytest.fixture
def stub_mount(monkeypatch):
    calls = []
    monkeypatch.setattr(bind_ops, "mount_bind", lambda bind: calls.append(bind.name))
    monkeypatch.setattr(bind_ops, "unmount_bind", lambda bind: None)
    return calls


@pytest.fixture
def stored(sandbox):
    """Write state.json directly: going through the API would try to mount."""
    def write(**collections):
        state = {"version": 1, **collections}
        (sandbox["PNAS_STATE_DIR"] / "state.json").write_text(json.dumps(state))
    return write


def read_state(sandbox) -> dict:
    return json.loads((sandbox["PNAS_STATE_DIR"] / "state.json").read_text())


POOL = {
    "name": "bulk",
    "mountpoint": "/mnt/pool/bulk",
    "branches": [{"path": "/mnt/disks/d1", "mode": "RW"}],
}


def test_creating_a_bind_writes_state_and_a_unit(
    auth_client, sandbox, no_systemd, stub_mount
):
    response = auth_client.post("/api/binds", json=BIND)

    assert response.status_code == 200
    assert read_state(sandbox)["bind_mounts"]["kz-fontos"]["source"] == "/mnt/fontos/kz"
    unit = sandbox["PNAS_SYSTEMD_DIR"] / "pnas-bind-kz-fontos.service"
    assert "mount --bind /mnt/fontos/kz /mnt/family_pool/kz/fontos" in unit.read_text()
    assert stub_mount == ["kz-fontos"]


def test_a_source_inside_a_managed_pool_is_ordered_after_that_pool(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    stored(pools={"bulk": POOL})

    auth_client.post("/api/binds", json={
        "name": "kz-bulk", "source": "/mnt/pool/bulk/kz",
        "target": "/mnt/family_pool/kz/nemfontos",
    })

    unit = (sandbox["PNAS_SYSTEMD_DIR"] / "pnas-bind-kz-bulk.service").read_text()
    assert "After=pnas-pool-bulk.service" in unit


def test_duplicate_names_are_refused(auth_client, sandbox, no_systemd, stub_mount):
    auth_client.post("/api/binds", json=BIND)
    assert auth_client.post("/api/binds", json=BIND).status_code == 409


def test_two_binds_may_not_share_a_target(
    auth_client, sandbox, no_systemd, stub_mount
):
    auth_client.post("/api/binds", json=BIND)
    response = auth_client.post("/api/binds", json={
        "name": "other", "source": "/mnt/bulk/kz", "target": BIND["target"],
    })
    assert response.status_code == 409
    assert "already used" in response.json()["detail"]


def test_a_target_nested_in_another_target_is_refused(
    auth_client, sandbox, no_systemd, stub_mount
):
    """Nesting would make mount order significant and would break the single
    level of indirection the share dependency check relies on."""
    auth_client.post("/api/binds", json=BIND)
    response = auth_client.post("/api/binds", json={
        "name": "deeper", "source": "/mnt/bulk/kz",
        "target": "/mnt/family_pool/kz/fontos/deeper",
    })
    assert response.status_code == 409
    assert "nested" in response.json()["detail"]


def test_a_target_on_a_pool_mountpoint_is_refused(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    stored(pools={"bulk": POOL})
    response = auth_client.post("/api/binds", json={
        "name": "shadow", "source": "/mnt/fontos/kz", "target": "/mnt/pool/bulk",
    })
    assert response.status_code == 409
    assert "mountpoint of pool" in response.json()["detail"]


def test_a_target_inside_a_pool_is_refused(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    stored(pools={"bulk": POOL})
    response = auth_client.post("/api/binds", json={
        "name": "inside", "source": "/mnt/fontos/kz",
        "target": "/mnt/pool/bulk/presented",
    })
    assert response.status_code == 409
    assert "inside mergerfs pool" in response.json()["detail"]


def test_a_target_on_a_pool_branch_is_refused(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    stored(pools={"bulk": POOL})
    response = auth_client.post("/api/binds", json={
        "name": "onbranch", "source": "/mnt/fontos/kz", "target": "/mnt/disks/d1",
    })
    assert response.status_code == 409
    assert "branch" in response.json()["detail"]


def test_a_bind_into_itself_is_a_422_not_a_mount_loop(auth_client, sandbox):
    response = auth_client.post("/api/binds", json={
        "name": "loop", "source": "/mnt/a", "target": "/mnt/a/inner",
    })
    assert response.status_code == 422


def test_deleting_is_refused_while_a_share_sits_on_the_target(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    stored(
        shares={"fontos": {"name": "fontos", "path": BIND["target"]}},
        bind_mounts={"kz-fontos": BIND},
    )
    response = auth_client.delete("/api/binds/kz-fontos")
    assert response.status_code == 409
    assert "fontos" in response.json()["detail"]


def test_a_share_on_the_presentation_root_only_warns(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    """The intended layout is one share over the whole tree, so treating it as
    a blocker would make every bind permanently undeletable. Removing one bind
    leaves an empty folder in that share - report it, do not refuse it."""
    stored(
        shares={"family": {"name": "family", "path": "/mnt/family_pool"}},
        bind_mounts={"kz-fontos": BIND},
    )
    response = auth_client.delete("/api/binds/kz-fontos")

    assert response.status_code == 200
    assert "family" in response.json()["warning"]
    assert read_state(sandbox)["bind_mounts"] == {}


def test_deleting_succeeds_without_dependents(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    stored(bind_mounts={"kz-fontos": BIND})
    (sandbox["PNAS_SYSTEMD_DIR"] / "pnas-bind-kz-fontos.service").write_text("x")

    assert auth_client.delete("/api/binds/kz-fontos").status_code == 200
    assert read_state(sandbox)["bind_mounts"] == {}
    assert not (sandbox["PNAS_SYSTEMD_DIR"] / "pnas-bind-kz-fontos.service").exists()


def test_unknown_bind_is_a_404(auth_client, sandbox, no_systemd):
    assert auth_client.delete("/api/binds/nope").status_code == 404


def test_a_pool_cannot_be_deleted_while_a_bind_presents_it(
    auth_client, sandbox, stored, no_systemd
):
    stored(
        pools={"bulk": POOL},
        bind_mounts={"kz-bulk": {
            "name": "kz-bulk", "source": "/mnt/pool/bulk/kz",
            "target": "/mnt/family_pool/kz/nemfontos",
        }},
    )
    response = auth_client.delete("/api/pools/bulk")
    assert response.status_code == 409
    assert "kz-bulk" in response.json()["detail"]


def test_a_share_reaching_a_pool_through_a_bind_blocks_deletion(
    auth_client, sandbox, stored, no_systemd
):
    """The share is on the presentation tree and never mentions the pool, but
    deleting the pool would still empty it."""
    stored(
        shares={"family": {"name": "family", "path": "/mnt/family_pool/kz/nemfontos"}},
        pools={"bulk": POOL},
        bind_mounts={"kz-bulk": {
            "name": "kz-bulk", "source": "/mnt/pool/bulk/kz",
            "target": "/mnt/family_pool/kz/nemfontos",
        }},
    )
    assert auth_client.post("/api/pools/bulk/unmount").status_code == 409


def test_a_disk_cannot_be_unmounted_while_a_bind_presents_it(
    auth_client, sandbox, stored, no_systemd
):
    stored(
        disk_mounts={"d9": {
            "uuid": "1234abcd-56ef-78ab-90cd-1234567890ab",
            "fstype": "ext4", "mountpoint": "/mnt/disks/d9",
        }},
        bind_mounts={"kz-d9": {
            "name": "kz-d9", "source": "/mnt/disks/d9/kz",
            "target": "/mnt/family_pool/kz/nemfontos",
        }},
    )
    response = auth_client.delete("/api/disks/mount/d9")
    assert response.status_code == 409
    assert "kz-d9" in response.json()["detail"]


def test_plan_expands_the_template_without_changing_anything(
    auth_client, sandbox, stored
):
    stored(bind_mounts={})
    response = auth_client.post("/api/binds/plan", json={
        "root": "/mnt/family_pool",
        "folders": ["kz", "kzs", "kv"],
        "tiers": [
            {"label": "fontos", "source_root": "/mnt/fontos"},
            {"label": "nemfontos", "source_root": "/mnt/bulk"},
        ],
    })

    rows = response.json()["binds"]
    assert len(rows) == 6
    assert rows[0]["target"] == "/mnt/family_pool/kz/fontos"
    assert all(row["conflict"] is None for row in rows)
    assert read_state(sandbox)["bind_mounts"] == {}


def test_plan_reports_a_conflict_with_an_existing_bind(auth_client, sandbox, stored):
    stored(bind_mounts={"other": {
        "name": "other", "source": "/mnt/elsewhere",
        "target": "/mnt/family_pool/kz/fontos",
    }})
    response = auth_client.post("/api/binds/plan", json={
        "root": "/mnt/family_pool",
        "folders": ["kz"],
        "tiers": [{"label": "fontos", "source_root": "/mnt/fontos"}],
    })
    assert response.json()["binds"][0]["conflict"] is not None


def test_plan_rejects_a_folder_name_that_is_not_a_safe_path_segment(auth_client, sandbox):
    response = auth_client.post("/api/binds/plan", json={
        "root": "/mnt/family_pool",
        "folders": ["../etc"],
        "tiers": [{"label": "fontos", "source_root": "/mnt/fontos"}],
    })
    assert response.status_code == 400


def test_bulk_creates_the_whole_tree(auth_client, sandbox, no_systemd, stub_mount):
    planned = auth_client.post("/api/binds/plan", json={
        "root": "/mnt/family_pool",
        "folders": ["kz", "kzs"],
        "tiers": [
            {"label": "fontos", "source_root": "/mnt/fontos"},
            {"label": "nemfontos", "source_root": "/mnt/bulk"},
        ],
    }).json()["binds"]

    response = auth_client.post("/api/binds/bulk", json={"binds": planned})

    assert response.status_code == 200
    assert len(read_state(sandbox)["bind_mounts"]) == 4
    assert len(stub_mount) == 4


def test_bulk_applies_nothing_when_one_entry_conflicts(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    """Validated up front on purpose: a half-created tree is worse than none,
    because the missing halves look like empty folders to the users."""
    stored(bind_mounts={"other": {
        "name": "other", "source": "/mnt/elsewhere",
        "target": "/mnt/family_pool/kzs/fontos",
    }})
    response = auth_client.post("/api/binds/bulk", json={"binds": [
        {"name": "kz-fontos", "source": "/mnt/fontos/kz",
         "target": "/mnt/family_pool/kz/fontos"},
        {"name": "kzs-fontos", "source": "/mnt/fontos/kzs",
         "target": "/mnt/family_pool/kzs/fontos"},
    ]})

    assert response.status_code == 409
    assert set(read_state(sandbox)["bind_mounts"]) == {"other"}
    assert stub_mount == []


def test_bulk_creates_missing_source_directories(
    auth_client, sandbox, tmp_path, no_systemd, stub_mount
):
    source_root = tmp_path / "fontos"
    source_root.mkdir()

    response = auth_client.post("/api/binds/bulk", json={
        "binds": [{
            "name": "kz-fontos", "source": f"{source_root}/kz",
            "target": f"{tmp_path}/family_pool/kz/fontos",
        }],
        "create_sources": True,
    })

    assert response.status_code == 200
    assert (source_root / "kz").is_dir()


def test_created_source_directories_are_writable_by_the_samba_guest_account(
    auth_client, sandbox, tmp_path, no_systemd, stub_mount
):
    """A plain mkdir is root:root 0755, which Samba's "force user = nobody"
    can list but not write into - so a recreated source must get the same
    nobody:nogroup 0777 ownership every other presentation folder has."""
    import os

    source_root = tmp_path / "fontos"
    source_root.mkdir()

    response = auth_client.post("/api/binds/bulk", json={
        "binds": [{
            "name": "kz-fontos", "source": f"{source_root}/kz",
            "target": f"{tmp_path}/family_pool/kz/fontos",
        }],
        "create_sources": True,
    })

    assert response.status_code == 200
    st = os.stat(source_root / "kz")
    assert oct(st.st_mode)[-3:] == "777"


def test_a_missing_source_directory_is_left_alone_by_default(
    auth_client, sandbox, tmp_path, no_systemd, stub_mount
):
    source_root = tmp_path / "fontos"
    source_root.mkdir()

    auth_client.post("/api/binds/bulk", json={"binds": [{
        "name": "kz-fontos", "source": f"{source_root}/kz",
        "target": f"{tmp_path}/family_pool/kz/fontos",
    }]})

    assert not (source_root / "kz").exists()


def test_the_list_reports_where_the_source_really_lives(
    auth_client, sandbox, stored, no_systemd
):
    stored(pools={"bulk": POOL}, bind_mounts={"kz-bulk": {
        "name": "kz-bulk", "source": "/mnt/pool/bulk/kz",
        "target": "/mnt/family_pool/kz/nemfontos",
    }})

    info = auth_client.get("/api/binds").json()["binds"]["kz-bulk"]

    assert info["backing_pool"] == "bulk"
    assert info["mounted"] is False
    assert info["source_exists"] is False


def test_renaming_through_put_is_refused(auth_client, sandbox, stored, no_systemd):
    stored(bind_mounts={"kz-fontos": BIND})
    response = auth_client.put(
        "/api/binds/kz-fontos", json={**BIND, "name": "renamed"}
    )
    assert response.status_code == 400


def test_updating_remounts_with_the_new_source(
    auth_client, sandbox, stored, no_systemd, stub_mount
):
    stored(bind_mounts={"kz-fontos": BIND})

    response = auth_client.put(
        "/api/binds/kz-fontos", json={**BIND, "source": "/mnt/fontos/kz2"}
    )

    assert response.status_code == 200
    assert read_state(sandbox)["bind_mounts"]["kz-fontos"]["source"] == "/mnt/fontos/kz2"
    unit = (sandbox["PNAS_SYSTEMD_DIR"] / "pnas-bind-kz-fontos.service").read_text()
    assert "/mnt/fontos/kz2" in unit
