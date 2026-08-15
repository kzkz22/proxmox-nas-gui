"""diagnostics.py: the checks themselves, and the fix dispatch.

Every check is exercised directly against a constructed State plus real
tmp_path directories - no HTTP involved, matching test_deps.py's pattern.
Mounted/unmounted state is faked via pool_ops.is_mounted rather than a real
mount, the same way test_pools_api.py fakes systemd.
"""

import os

import pytest

from app.core.proc import SystemOpError
from app import diagnostics as diag
from app.models import State
from app.samba.models import Access, Share
from app.storage import binds as bind_ops
from app.storage import pools as pool_ops
from app.storage.models import Branch, BindMount, DiskMount, Pool


def make_pool(name, mountpoint, *branches) -> Pool:
    return Pool(name=name, mountpoint=mountpoint,
                branches=[Branch(path=b) for b in branches])


@pytest.fixture(autouse=True)
def systemd_dir(tmp_path, monkeypatch):
    """Point unit writing at a temp directory for every test in this file.

    Autouse rather than per-test on purpose: several fixes here write a pool
    unit, and a test that forgot to redirect this would write into the real
    /etc/systemd/system - silently succeeding for anyone running the suite as
    root, and failing on CI. Which is exactly what happened once.
    """
    path = tmp_path / "systemd"
    path.mkdir(exist_ok=True)
    monkeypatch.setenv("PNAS_SYSTEMD_DIR", str(path))


@pytest.fixture(autouse=True)
def always_mounted(monkeypatch):
    """Most checks care about filesystem facts, not real mounts - default to
    "everything is mounted" so is_mounted noise does not leak into checks
    that are not about mounting. Individual tests override this."""
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: True)


# --- pools --------------------------------------------------------------

def test_pool_branch_missing_is_crit_and_not_fixable(tmp_path):
    pool = make_pool("bulk", str(tmp_path / "pool"), str(tmp_path / "gone"))
    st = State(pools={"bulk": pool})

    findings = diag._pool_checks(st)

    assert len(findings) == 1
    f = findings[0]
    assert (f["id"], f["severity"], f["fixable"]) == ("pool_branch_missing", "crit", False)
    assert f["entity"] == "bulk"


def test_pool_not_mounted_is_a_fixable_warning(tmp_path, monkeypatch):
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    st = State(pools={"bulk": pool})
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)

    findings = diag._pool_checks(st)

    assert [f["id"] for f in findings] == ["pool_not_mounted"]
    assert findings[0]["fixable"] is True


def test_cache_files_off_is_flagged(tmp_path):
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    pool.cache_files = "off"
    st = State(pools={"bulk": pool})

    findings = diag._pool_checks(st)

    assert [f["id"] for f in findings] == ["pool_cache_files_off"]
    assert (findings[0]["severity"], findings[0]["fixable"]) == ("warn", False)


def test_cache_files_off_via_extra_options_is_flagged_too(tmp_path):
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    pool.extra_options = "cache.files=off"
    st = State(pools={"bulk": pool})

    assert [f["id"] for f in diag._pool_checks(st)] == ["pool_cache_files_off"]


def test_default_cache_settings_are_not_flagged(tmp_path):
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))

    assert diag._pool_checks(State(pools={"bulk": pool})) == []


def test_a_fully_healthy_pool_has_no_findings(tmp_path):
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    st = State(pools={"bulk": pool})

    assert diag._pool_checks(st) == []


# --- binds ----------------------------------------------------------------

def test_bind_source_missing_is_crit_and_fixable(tmp_path):
    bind = BindMount(name="kz-bulk", source=str(tmp_path / "missing"),
                      target=str(tmp_path / "target"))
    st = State(bind_mounts={"kz-bulk": bind})

    findings = diag._bind_checks(st)

    assert [f["id"] for f in findings] == ["bind_source_missing"]
    assert findings[0]["fixable"] is True
    assert findings[0]["command"]


def test_bind_source_wrong_perms_is_flagged_without_stubbing_chown(tmp_path):
    """The check only stats the directory - a plain tmp_path dir the test
    process owns is naturally not nobody:nogroup, no fixture needed."""
    source = tmp_path / "src"
    source.mkdir()
    bind = BindMount(name="kz-bulk", source=str(source), target=str(tmp_path / "tgt"))
    st = State(bind_mounts={"kz-bulk": bind})

    findings = diag._bind_checks(st)

    assert "bind_source_wrong_perms" in [f["id"] for f in findings]


def test_bind_source_correct_perms_is_not_flagged(tmp_path, stub_nobody):
    source = tmp_path / "src"
    source.mkdir()
    source.chmod(0o777)
    bind = BindMount(name="kz-bulk", source=str(source), target=str(tmp_path / "tgt"))
    st = State(bind_mounts={"kz-bulk": bind})

    findings = diag._bind_checks(st)

    assert "bind_source_wrong_perms" not in [f["id"] for f in findings]


def test_bind_not_mounted_is_a_fixable_warning_when_source_exists(
    tmp_path, monkeypatch, stub_nobody
):
    source = tmp_path / "src"
    source.mkdir()
    source.chmod(0o777)
    bind = BindMount(name="kz-bulk", source=str(source), target=str(tmp_path / "tgt"))
    st = State(bind_mounts={"kz-bulk": bind})
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)

    findings = diag._bind_checks(st)

    ids = [f["id"] for f in findings]
    assert "bind_not_mounted" in ids
    assert next(f for f in findings if f["id"] == "bind_not_mounted")["fixable"] is True


def test_bind_source_single_branch_is_a_non_fixable_warning(tmp_path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d2 / "kz").mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(d1), str(d2))
    bind = BindMount(name="kz-bulk", source=f"{pool.mountpoint}/kz",
                      target=str(tmp_path / "tgt"))
    st = State(pools={"bulk": pool}, bind_mounts={"kz-bulk": bind})

    findings = diag._single_branch_findings(st, "kz-bulk", bind)

    assert len(findings) == 1
    f = findings[0]
    assert (f["id"], f["severity"], f["fixable"]) == ("bind_source_single_branch", "warn", False)
    assert f["vars"]["pool"] == "bulk"
    assert f["vars"]["branch"] == str(d2)


def test_bind_source_present_on_all_branches_is_not_flagged(tmp_path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d1 / "kz").mkdir()
    (d2 / "kz").mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(d1), str(d2))
    bind = BindMount(name="kz-bulk", source=f"{pool.mountpoint}/kz",
                      target=str(tmp_path / "tgt"))
    st = State(pools={"bulk": pool}, bind_mounts={"kz-bulk": bind})

    assert diag._single_branch_findings(st, "kz-bulk", bind) == []


def test_bind_source_single_branch_needs_at_least_two_branches(tmp_path):
    d1 = tmp_path / "d1"
    d1.mkdir()
    (d1 / "kz").mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(d1))
    bind = BindMount(name="kz-bulk", source=f"{pool.mountpoint}/kz",
                      target=str(tmp_path / "tgt"))
    st = State(pools={"bulk": pool}, bind_mounts={"kz-bulk": bind})

    assert diag._single_branch_findings(st, "kz-bulk", bind) == []


# --- shares -----------------------------------------------------------------

def test_share_path_missing_is_crit_not_fixable(tmp_path):
    share = Share(name="media", path=str(tmp_path / "gone"))
    st = State(shares={"media": share})

    findings = diag._share_checks(st)

    assert [(f["id"], f["severity"], f["fixable"]) for f in findings] == [
        ("share_path_missing", "crit", False)
    ]


def test_share_wrong_perms_is_fixable_warning(tmp_path):
    path = tmp_path / "media"
    path.mkdir()
    share = Share(name="media", path=str(path))
    st = State(shares={"media": share})

    findings = diag._share_checks(st)

    assert "share_wrong_perms" in [f["id"] for f in findings]


def test_share_correct_perms_is_not_flagged(tmp_path, stub_nobody):
    path = tmp_path / "media"
    path.mkdir()
    path.chmod(0o777)
    share = Share(name="media", path=str(path))
    st = State(shares={"media": share})

    findings = diag._share_checks(st)

    assert "share_wrong_perms" not in [f["id"] for f in findings]


def test_share_references_unknown_user_is_warned_and_not_fixable(tmp_path, stub_nobody):
    path = tmp_path / "media"
    path.mkdir()
    path.chmod(0o777)
    share = Share(name="media", path=str(path), user_access={"ghost": Access.READ})
    st = State(shares={"media": share})

    findings = diag._share_checks(st)

    assert [(f["id"], f["fixable"], f["vars"]["user"]) for f in findings] == [
        ("share_unknown_user", False, "ghost")
    ]


def test_share_references_unknown_group_is_warned_and_not_fixable(tmp_path, stub_nobody):
    path = tmp_path / "media"
    path.mkdir()
    path.chmod(0o777)
    share = Share(name="media", path=str(path), group_access={"ghosts": Access.READ})
    st = State(shares={"media": share})

    findings = diag._share_checks(st)

    assert [(f["id"], f["fixable"], f["vars"]["group"]) for f in findings] == [
        ("share_unknown_group", False, "ghosts")
    ]


def test_share_known_user_is_not_flagged(tmp_path, stub_nobody):
    from app.samba.models import UserInfo
    path = tmp_path / "media"
    path.mkdir()
    path.chmod(0o777)
    share = Share(name="media", path=str(path), user_access={"alice": Access.READ})
    st = State(shares={"media": share}, users={"alice": UserInfo()})

    assert diag._share_checks(st) == []


# --- mounts -------------------------------------------------------------

def test_disk_mount_uuid_no_longer_resolves_is_crit(monkeypatch):
    dm = DiskMount(uuid="1234abcd-56ef-78ab-90cd-1234567890ab",
                    fstype="ext4", mountpoint="/mnt/disks/d1")
    st = State(disk_mounts={"d1": dm})
    monkeypatch.setattr(pool_ops, "list_block_devices", lambda: [])

    findings = diag._mount_checks(st)

    assert [(f["id"], f["severity"], f["fixable"]) for f in findings] == [
        ("disk_mount_uuid_missing", "crit", False)
    ]


def test_disk_mount_not_mounted_is_a_fixable_warning(monkeypatch):
    dm = DiskMount(uuid="1234abcd-56ef-78ab-90cd-1234567890ab",
                    fstype="ext4", mountpoint="/mnt/disks/d1")
    st = State(disk_mounts={"d1": dm})
    monkeypatch.setattr(pool_ops, "list_block_devices", lambda: [{"uuid": dm.uuid}])
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)

    findings = diag._mount_checks(st)

    assert [(f["id"], f["fixable"]) for f in findings] == [("disk_mount_not_mounted", True)]


def test_disk_mount_present_and_mounted_is_not_flagged(monkeypatch):
    dm = DiskMount(uuid="1234abcd-56ef-78ab-90cd-1234567890ab",
                    fstype="ext4", mountpoint="/mnt/disks/d1")
    st = State(disk_mounts={"d1": dm})
    monkeypatch.setattr(pool_ops, "list_block_devices", lambda: [{"uuid": dm.uuid}])

    assert diag._mount_checks(st) == []


# --- units --------------------------------------------------------------

def test_pool_unit_missing_is_crit_and_fixable(tmp_path, monkeypatch):
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    st = State(pools={"bulk": pool})

    findings = diag._unit_checks(st)

    assert [(f["id"], f["severity"], f["fixable"]) for f in findings] == [
        ("pool_unit_missing", "crit", True)
    ]


def test_pool_unit_matching_generated_text_is_not_flagged(tmp_path, monkeypatch):
    from app.storage import poolconf
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    (tmp_path / "systemd" / poolconf.pool_unit_name("bulk")).write_text(poolconf.pool_unit(pool))
    st = State(pools={"bulk": pool})

    assert diag._unit_checks(st) == []


def test_pool_unit_drift_is_warn_and_fixable(tmp_path, monkeypatch):
    from app.storage import poolconf
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    (tmp_path / "systemd" / poolconf.pool_unit_name("bulk")).write_text("# hand-edited\n")
    st = State(pools={"bulk": pool})

    findings = diag._unit_checks(st)

    assert [(f["id"], f["severity"], f["fixable"]) for f in findings] == [
        ("pool_unit_drift", "warn", True)
    ]


def test_bind_unit_missing_is_crit_and_fixable(tmp_path, monkeypatch):
    bind = BindMount(name="kz-bulk", source="/mnt/a", target="/mnt/b")
    st = State(bind_mounts={"kz-bulk": bind})

    findings = diag._unit_checks(st)

    assert [(f["id"], f["severity"], f["fixable"]) for f in findings] == [
        ("bind_unit_missing", "crit", True)
    ]


def test_a_hand_edited_bind_unit_is_not_flagged_as_drifted(tmp_path, monkeypatch):
    """Bind units are existence-only: bindconf.bind_unit() bakes in the
    live mount_root() answer, which would false-positive whenever the
    backing disk is transiently unmounted at check time."""
    bind = BindMount(name="kz-bulk", source="/mnt/a", target="/mnt/b")
    from app.storage import bindconf
    (tmp_path / "systemd" / bindconf.bind_unit_name("kz-bulk")).write_text("# hand-edited\n")
    st = State(bind_mounts={"kz-bulk": bind})

    assert diag._unit_checks(st) == []


# --- disks ----------------------------------------------------------------

def test_disk_warnings_are_folded_in_under_the_disks_category(monkeypatch):
    monkeypatch.setattr(diag.disksleep, "list_sleep_disks",
                         lambda: [{"by_id": "ata-x", "rotational": True}])
    monkeypatch.setattr(diag.disksleep, "describe", lambda state, disks: {
        "ata-x": {"warnings": [{"id": "smartd", "severity": "crit",
                                 "fixable": True, "vars": {}, "command": "x"}],
                   "zfs_pool": None},
    })

    findings = diag._disk_checks(State())

    assert findings == [{"id": "smartd", "severity": "crit", "fixable": True,
                          "vars": {}, "command": "x", "category": "disks",
                          "entity": "ata-x"}]


def test_non_rotational_disks_are_excluded(monkeypatch):
    monkeypatch.setattr(diag.disksleep, "list_sleep_disks",
                         lambda: [{"by_id": "nvme-x", "rotational": False}])
    called = []
    monkeypatch.setattr(diag.disksleep, "describe",
                         lambda state, disks: called.append(disks) or {})

    diag._disk_checks(State())

    assert called == [[]]


# --- run_all ----------------------------------------------------------------

def test_run_all_sorts_by_severity_then_category_then_entity(tmp_path, monkeypatch):
    """Isolates the pool/share checks from units and disks, which would
    otherwise add their own noise (no systemd dir, no real disks here)."""
    monkeypatch.setattr(diag, "_unit_checks", lambda state: [])
    monkeypatch.setattr(diag, "_disk_checks", lambda state: [])
    unmounted_pool = make_pool("zzz", str(tmp_path / "pool2"), str(tmp_path / "d1"))
    (tmp_path / "d1").mkdir()
    broken_pool = make_pool("aaa", str(tmp_path / "pool"), str(tmp_path / "gone"))
    missing_share = Share(name="s", path=str(tmp_path / "also-gone"))
    st = State(pools={"zzz": unmounted_pool, "aaa": broken_pool}, shares={"s": missing_share})
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)

    findings = diag.run_all(st)

    severities = [f["severity"] for f in findings]
    assert severities == sorted(severities, key=lambda s: diag._SEVERITY_ORDER[s])
    assert severities[0] == "crit" and severities[-1] == "warn"
    assert {f["id"] for f in findings} == {
        "pool_branch_missing", "share_path_missing", "pool_not_mounted",
    }


def test_run_all_is_empty_for_a_fully_healthy_state(tmp_path, monkeypatch):
    monkeypatch.setattr(diag.disksleep, "list_sleep_disks", lambda: [])

    assert diag.run_all(State()) == []


# --- apply_fix ----------------------------------------------------------

def test_is_fixable_covers_both_tables():
    assert diag.is_fixable("pool_not_mounted") is True
    assert diag.is_fixable("smartd") is True  # disksleep.FIXABLE
    assert diag.is_fixable("pool_branch_missing") is False
    assert diag.is_fixable("no-such-id") is False


def test_apply_fix_rejects_an_unknown_id():
    with pytest.raises(SystemOpError):
        diag.apply_fix(State(), "no-such-id", "whatever")


def test_apply_fix_mounts_a_pool(monkeypatch):
    pool = make_pool("bulk", "/mnt/pool/bulk", "/mnt/disks/d1")
    st = State(pools={"bulk": pool})
    calls = []
    monkeypatch.setattr(pool_ops, "mount_pool", lambda p: calls.append(p.name))

    detail = diag.apply_fix(st, "pool_not_mounted", "bulk")

    assert calls == ["bulk"]
    assert "bulk" in detail


def test_apply_fix_creates_a_missing_bind_source(tmp_path, stub_chown):
    bind = BindMount(name="kz-bulk", source=str(tmp_path / "src" / "kz"),
                      target=str(tmp_path / "tgt"))
    (tmp_path / "src").mkdir()
    st = State(bind_mounts={"kz-bulk": bind})

    diag.apply_fix(st, "bind_source_missing", "kz-bulk")

    assert (tmp_path / "src" / "kz").is_dir()


def test_apply_fix_rewrites_a_drifted_pool_unit(tmp_path, monkeypatch):
    from app.storage import poolconf
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    unit_path = tmp_path / "systemd" / poolconf.pool_unit_name("bulk")
    unit_path.write_text("# hand-edited\n")
    st = State(pools={"bulk": pool})
    monkeypatch.setattr(pool_ops, "systemctl", lambda *a: True)
    monkeypatch.setattr(pool_ops, "mount_pool", lambda p: None)

    diag.apply_fix(st, "pool_unit_drift", "bulk")

    assert unit_path.read_text() == poolconf.pool_unit(pool)


def test_apply_fix_delegates_disk_ids_to_disksleep(monkeypatch):
    st = State()
    monkeypatch.setattr(diag.disksleep, "list_sleep_disks",
                         lambda: [{"by_id": "ata-x", "path": "/dev/sda"}])
    calls = []
    monkeypatch.setattr(diag.disksleep, "apply_fix",
                         lambda state, check, disk: calls.append((check, disk["by_id"])) or "ok")

    detail = diag.apply_fix(st, "smartd", "ata-x")

    assert calls == [("smartd", "ata-x")]
    assert detail == "ok"


def test_apply_fix_disk_id_rejects_an_unknown_disk(monkeypatch):
    monkeypatch.setattr(diag.disksleep, "list_sleep_disks", lambda: [])
    with pytest.raises(SystemOpError):
        diag.apply_fix(State(), "smartd", "no-such-disk")


def test_diagnostics_ids_never_collide_with_disksleep_ids():
    assert not (set(diag.FIXABLE) & set(diag.disksleep.FIXABLE))


# --- mergerfs capability checks -----------------------------------------

@pytest.fixture
def caps(monkeypatch):
    """Fake the machine's mergerfs/kernel capability probe. Defaults to the
    common Debian case: kernel new enough, packaged mergerfs is not."""
    def setup(passthrough=False, missing="mergerfs", version="2.40.2"):
        monkeypatch.setattr(diag.mergerfs_env, "capabilities", lambda: {
            "mergerfs_version": version, "kernel_version": "7.0.2",
            "passthrough": passthrough, "passthrough_missing": missing,
        })
    setup()
    return setup


@pytest.fixture(autouse=True)
def no_drift(monkeypatch):
    """Pool option drift needs a real mergerfs control file to read; default
    to "the mount runs what is configured" so it stays out of other checks."""
    monkeypatch.setattr(pool_ops, "option_drift", lambda _pool: [])


def test_outdated_mergerfs_is_reported_when_the_kernel_could_do_better(
    tmp_path, caps
):
    branch = tmp_path / "d1"
    branch.mkdir()
    st = State(pools={"bulk": make_pool("bulk", str(tmp_path / "pool"), str(branch))})

    findings = diag._mergerfs_checks(st)

    assert [f["id"] for f in findings] == ["mergerfs_outdated"]
    assert (findings[0]["severity"], findings[0]["fixable"]) == ("info", False)
    assert findings[0]["vars"]["installed"] == "2.40.2"


def test_an_old_kernel_is_not_blamed_on_mergerfs(tmp_path, caps):
    caps(missing="kernel")
    branch = tmp_path / "d1"
    branch.mkdir()
    st = State(pools={"bulk": make_pool("bulk", str(tmp_path / "pool"), str(branch))})

    assert diag._mergerfs_checks(st) == []


def test_no_pools_means_no_upgrade_advice(caps):
    assert diag._mergerfs_checks(State()) == []


def test_passthrough_is_offered_when_available(tmp_path, caps):
    caps(passthrough=True, missing=None, version="2.42.0")
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    st = State(pools={"bulk": pool})

    findings = diag._pool_checks(st)

    assert [f["id"] for f in findings] == ["pool_passthrough_available"]
    assert findings[0]["fixable"] is True


def test_passthrough_is_not_offered_twice(tmp_path, caps):
    caps(passthrough=True, missing=None, version="2.42.0")
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    pool.passthrough = "rw"
    pool.moveonenospc = False

    assert diag._pool_checks(State(pools={"bulk": pool})) == []


def test_drift_is_a_fixable_warning(tmp_path, caps, monkeypatch):
    branch = tmp_path / "d1"
    branch.mkdir()
    pool = make_pool("bulk", str(tmp_path / "pool"), str(branch))
    monkeypatch.setattr(
        pool_ops, "option_drift", lambda _pool: ["cache.writeback: false -> true"]
    )

    findings = diag._pool_checks(State(pools={"bulk": pool}))

    assert [f["id"] for f in findings] == ["pool_needs_remount"]
    assert findings[0]["fixable"] is True
    assert "cache.writeback" in findings[0]["vars"]["options"]


# --- the new fixes ------------------------------------------------------

def test_passthrough_fix_turns_off_what_it_conflicts_with(tmp_path, monkeypatch):
    pool = make_pool("bulk", str(tmp_path / "pool"), str(tmp_path / "d1"))
    pool.cache_writeback = True
    st = State(pools={"bulk": pool})
    monkeypatch.setattr(pool_ops, "write_pool_unit", lambda _p: None)
    monkeypatch.setattr(pool_ops, "remount_pool", lambda _p: None)

    detail = diag.apply_fix(st, "pool_passthrough_available", "bulk")

    assert pool.passthrough == "rw"
    # Both are promises mergerfs cannot keep once the kernel owns the IO;
    # leaving them set would show settings the pool is not running.
    assert pool.cache_writeback is False
    assert pool.moveonenospc is False
    assert "passthrough=rw" in detail


def test_passthrough_fix_mounts_a_pool_that_was_down(tmp_path, monkeypatch):
    pool = make_pool("bulk", str(tmp_path / "pool"), str(tmp_path / "d1"))
    monkeypatch.setattr(pool_ops, "is_mounted", lambda _mp: False)
    monkeypatch.setattr(pool_ops, "write_pool_unit", lambda _p: None)
    mounted = []
    monkeypatch.setattr(pool_ops, "mount_pool", lambda p: mounted.append(p.name))

    diag.apply_fix(State(pools={"bulk": pool}), "pool_passthrough_available", "bulk")

    assert mounted == ["bulk"]


def test_remount_fix_brings_the_binds_back_up(tmp_path, monkeypatch):
    # Bind units declare Requires= on the pool service, so stopping the pool
    # stops them too. A remount that left them down would hand Samba an empty
    # directory over what still looks like a working share.
    mountpoint = str(tmp_path / "pool")
    pool = make_pool("bulk", mountpoint, str(tmp_path / "d1"))
    bind = BindMount(name="kz", source=f"{mountpoint}/kz", target="/mnt/tree/kz")
    st = State(pools={"bulk": pool}, bind_mounts={"kz": bind})
    remounted, rebound = [], []
    monkeypatch.setattr(pool_ops, "remount_pool", lambda p: remounted.append(p.name))
    monkeypatch.setattr(bind_ops, "mount_bind", lambda b: rebound.append(b.name))

    detail = diag.apply_fix(st, "pool_needs_remount", "bulk")

    assert remounted == ["bulk"]
    assert rebound == ["kz"]
    assert "kz" in detail


def test_remount_fix_leaves_other_pools_binds_alone(tmp_path, monkeypatch):
    pool = make_pool("bulk", str(tmp_path / "bulk"), str(tmp_path / "d1"))
    other = make_pool("fast", str(tmp_path / "fast"), str(tmp_path / "d2"))
    bind = BindMount(name="kz", source=f"{tmp_path / 'fast'}/kz", target="/mnt/tree/kz")
    st = State(pools={"bulk": pool, "fast": other}, bind_mounts={"kz": bind})
    monkeypatch.setattr(pool_ops, "remount_pool", lambda _p: None)
    rebound = []
    monkeypatch.setattr(bind_ops, "mount_bind", lambda b: rebound.append(b.name))

    diag.apply_fix(st, "pool_needs_remount", "bulk")

    assert rebound == []


def test_only_the_passthrough_fix_is_marked_as_changing_config():
    # The HTTP layer takes the state lock and saves for these; a fix wrongly
    # listed here would write state.json on every click.
    assert diag.mutates_state("pool_passthrough_available") is True
    assert diag.mutates_state("pool_needs_remount") is False
    assert diag.mutates_state("pool_not_mounted") is False


def test_remount_fix_rewrites_the_unit_before_mounting(tmp_path, monkeypatch):
    # Remounting starts the systemd unit, so a stale unit on disk would be
    # reapplied verbatim. Order matters, and it must not depend on the user
    # pressing pool_unit_drift first.
    pool = make_pool("bulk", str(tmp_path / "pool"), str(tmp_path / "d1"))
    calls = []
    monkeypatch.setattr(pool_ops, "write_pool_unit", lambda p: calls.append("write"))
    monkeypatch.setattr(pool_ops, "remount_pool", lambda p: calls.append("remount"))

    diag.apply_fix(State(pools={"bulk": pool}), "pool_needs_remount", "bulk")

    assert calls == ["write", "remount"]
