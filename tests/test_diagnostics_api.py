"""Wiring tests for the diagnostics endpoints.

The checks themselves are covered directly in test_diagnostics.py; this file
only exercises the HTTP layer - that GET assembles findings/summary/
generated_at, and that POST /fix validates before dispatching.
"""

import json

import pytest

from app.storage import disksleep
from app.storage import pools as pool_ops

POOL = {
    "name": "bulk",
    "mountpoint": "/mnt/pool/bulk",
    "branches": [{"path": "/mnt/disks/d1", "mode": "RW"}],
}


@pytest.fixture
def no_systemd(monkeypatch):
    monkeypatch.setattr(pool_ops, "systemctl", lambda *args: True)
    monkeypatch.setattr(pool_ops, "has_systemd", lambda: False)


@pytest.fixture
def no_disks(monkeypatch):
    """Empty, deterministic disk list - GET /api/diagnostics folds in
    disksleep.describe(), and the real lsblk/hdparm must never run in tests."""
    monkeypatch.setattr(disksleep, "list_sleep_disks", lambda: [])


@pytest.fixture
def stored(sandbox):
    def write(**collections):
        state = {"version": 1, **collections}
        (sandbox["PNAS_STATE_DIR"] / "state.json").write_text(json.dumps(state))
    return write


def test_get_diagnostics_returns_findings_and_summary_counts(
    auth_client, sandbox, stored, no_disks
):
    stored(pools={"bulk": {**POOL, "branches": [{"path": "/mnt/disks/gone", "mode": "RW"}]}})

    body = auth_client.get("/api/diagnostics").json()

    assert body["findings"][0]["id"] == "pool_branch_missing"
    assert body["summary"]["crit"] >= 1
    assert isinstance(body["generated_at"], int)


def test_get_diagnostics_is_empty_for_a_fresh_install(auth_client, sandbox, stored, no_disks):
    stored()

    body = auth_client.get("/api/diagnostics").json()

    assert body["findings"] == []
    assert body["summary"] == {"crit": 0, "warn": 0, "info": 0}


def test_fix_rejects_an_unknown_id_with_400(auth_client, sandbox, stored, no_disks):
    stored()
    response = auth_client.post("/api/diagnostics/fix", json={"id": "no-such-id", "entity": "x"})
    assert response.status_code == 400


def test_fix_rejects_a_non_fixable_id_with_400(auth_client, sandbox, stored, no_disks):
    stored()
    response = auth_client.post(
        "/api/diagnostics/fix", json={"id": "pool_branch_missing", "entity": "bulk"}
    )
    assert response.status_code == 400


def test_fix_returns_409_when_the_entity_no_longer_exists(auth_client, sandbox, stored, no_disks):
    stored()
    response = auth_client.post(
        "/api/diagnostics/fix", json={"id": "pool_not_mounted", "entity": "no-such-pool"}
    )
    assert response.status_code == 409


def test_fix_mounts_a_pool_end_to_end(
    auth_client, sandbox, stored, no_systemd, no_disks, monkeypatch
):
    stored(pools={"bulk": POOL})
    calls = []
    monkeypatch.setattr(pool_ops, "mount_pool", lambda p: calls.append(p.name))

    response = auth_client.post(
        "/api/diagnostics/fix", json={"id": "pool_not_mounted", "entity": "bulk"}
    )

    assert response.status_code == 200
    assert calls == ["bulk"]


def test_fix_creates_a_missing_bind_source_end_to_end(
    auth_client, sandbox, tmp_path, stored, no_systemd, no_disks, stub_chown
):
    source_root = tmp_path / "fontos"
    source_root.mkdir()
    bind = {
        "name": "kz-fontos", "source": f"{source_root}/kz",
        "target": f"{tmp_path}/family_pool/kz/fontos",
    }
    stored(bind_mounts={"kz-fontos": bind})

    response = auth_client.post(
        "/api/diagnostics/fix", json={"id": "bind_source_missing", "entity": "kz-fontos"}
    )

    assert response.status_code == 200
    assert (source_root / "kz").is_dir()


def test_fix_delegates_a_disk_check_to_sleep_end_to_end(auth_client, sandbox, stored, monkeypatch):
    stored()
    monkeypatch.setattr(disksleep, "list_sleep_disks",
                         lambda: [{"by_id": "ata-x", "path": "/dev/sda"}])
    calls = []
    monkeypatch.setattr(disksleep, "apply_fix",
                         lambda state, check, disk: calls.append((check, disk["by_id"])) or "fixed")

    response = auth_client.post(
        "/api/diagnostics/fix", json={"id": "smartd", "entity": "ata-x"}
    )

    assert response.status_code == 200
    assert calls == [("smartd", "ata-x")]


def test_a_config_changing_fix_is_written_back_to_state(
    auth_client, stored, sandbox, no_systemd, monkeypatch
):
    # Enabling passthrough edits the pool itself, so unlike every other fix
    # it has to be persisted - otherwise the pool would revert the next time
    # anything reloaded state.json.
    stored(pools={"bulk": POOL})
    monkeypatch.setattr(pool_ops, "write_pool_unit", lambda _p: None)
    monkeypatch.setattr(pool_ops, "remount_pool", lambda _p: None)
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: True)

    response = auth_client.post(
        "/api/diagnostics/fix",
        json={"id": "pool_passthrough_available", "entity": "bulk"},
    )

    assert response.status_code == 200
    saved = json.loads((sandbox["PNAS_STATE_DIR"] / "state.json").read_text())
    assert saved["pools"]["bulk"]["passthrough"] == "rw"
    assert saved["pools"]["bulk"]["moveonenospc"] is False


def test_a_running_system_fix_leaves_state_untouched(
    auth_client, stored, sandbox, no_systemd, monkeypatch
):
    stored(pools={"bulk": POOL})
    before = (sandbox["PNAS_STATE_DIR"] / "state.json").read_text()
    monkeypatch.setattr(pool_ops, "remount_pool", lambda _p: None)

    response = auth_client.post(
        "/api/diagnostics/fix", json={"id": "pool_needs_remount", "entity": "bulk"}
    )

    assert response.status_code == 200
    assert (sandbox["PNAS_STATE_DIR"] / "state.json").read_text() == before
