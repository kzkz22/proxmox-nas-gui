"""The pure half of the disk sleep feature.

Everything here is text in, data out - no devices, no subprocesses - which is
the point of keeping it separate from disksleep.py. The cases are the ones
that actually bite: an lsblk tree with a ZFS root, an hd-idle option string
copied from a real installation, and a drive whose state string is not the
one the happy path expects.
"""

import json

import pytest

from app.storage import sleepconf
from app.storage.models import IDLE_CHOICES


# --- power state ------------------------------------------------------------

@pytest.mark.parametrize("output,expected", [
    ("\n/dev/sdb:\n drive state is:  active/idle\n", sleepconf.ACTIVE),
    ("\n/dev/sdb:\n drive state is:  standby\n", sleepconf.STANDBY),
    ("\n/dev/sdb:\n drive state is:  sleeping\n", sleepconf.SLEEPING),
    ("\n/dev/sdb:\n drive state is:  unknown\n", sleepconf.UNKNOWN),
    ("", sleepconf.UNKNOWN),
    ("SG_IO: bad/missing sense data", sleepconf.UNKNOWN),
])
def test_power_state_is_parsed(output, expected):
    assert sleepconf.parse_power_state(output) == expected


def test_only_the_low_power_states_count_as_asleep():
    assert sleepconf.is_asleep(sleepconf.STANDBY)
    assert sleepconf.is_asleep(sleepconf.SLEEPING)
    assert not sleepconf.is_asleep(sleepconf.ACTIVE)
    # Unknown must not read as asleep: the caller would then skip the disk
    # forever instead of spinning it down.
    assert not sleepconf.is_asleep(sleepconf.UNKNOWN)


# --- diskstats --------------------------------------------------------------

DISKSTATS = """\
   8       0 sda 1234 0 9876 100 567 0 4321 50 0 0 0
   8       1 sda1 12 0 96 1 5 0 43 0 0 0 0
 259       0 nvme0n1 99 0 800 9 88 0 700 8 0 0 0
"""


def test_diskstats_yields_read_and_write_counts():
    stats = sleepconf.parse_diskstats(DISKSTATS)
    assert stats["sda"] == (1234, 567)
    assert stats["nvme0n1"] == (99, 88)


def test_disk_io_converts_sectors_to_bytes():
    """The 512 is a /proc/diskstats interface convention, not the drive's own
    sector size - using the real one would overstate a 4Kn disk fourfold."""
    io = sleepconf.parse_disk_io(DISKSTATS)

    assert io["sda"] == (9876 * 512, 4321 * 512)
    assert io["nvme0n1"] == (800 * 512, 700 * 512)


def test_disk_io_ignores_lines_without_the_sector_columns():
    assert sleepconf.parse_disk_io("8 0 sda 1 0 96 1 5\nnonsense\n") == {}


def test_diskstats_ignores_short_and_broken_lines():
    assert sleepconf.parse_diskstats("8 0 sda 1\nnonsense\n\n") == {}


# --- physical disks ---------------------------------------------------------

def lsblk(*disks) -> str:
    return json.dumps({"blockdevices": list(disks)})


ROOT_ON_LVM = {
    "path": "/dev/sda", "type": "disk", "size": 512 * 10**9, "model": "SYSTEM",
    "rota": False, "children": [
        {"path": "/dev/sda1", "type": "part", "fstype": "vfat",
         "mountpoint": "/boot/efi"},
        {"path": "/dev/sda3", "type": "part", "fstype": "LVM2_member",
         "children": [{"path": "/dev/mapper/pve-root", "type": "lvm",
                       "fstype": "ext4", "mountpoint": "/"}]},
    ],
}
DATA_DISK = {
    "path": "/dev/sdb", "type": "disk", "size": 3 * 10**12,
    "model": "TOSHIBA DT01ACA300", "serial": "334401VAS", "rota": True,
    "children": [{"path": "/dev/sdb1", "type": "part", "fstype": "ext4",
                  "mountpoint": "/mnt/disks/toshiba3"}],
}
ZFS_ROOT_DISK = {
    "path": "/dev/sdc", "type": "disk", "size": 512 * 10**9, "model": "ZFSROOT",
    "rota": True, "children": [
        # The tell-tale shape: a pool member with no mountpoint at all,
        # because the pool is mounted, not the partition.
        {"path": "/dev/sdc3", "type": "part", "fstype": "zfs_member",
         "mountpoint": None},
    ],
}
SWAP_DISK = {
    "path": "/dev/sdd", "type": "disk", "size": 10**11, "rota": True,
    "children": [{"path": "/dev/sdd1", "type": "part", "fstype": "swap",
                  "mountpoint": "[SWAP]"}],
}


def test_whole_disks_are_returned_not_partitions():
    disks = sleepconf.parse_physical_disks(lsblk(DATA_DISK))
    assert [d["path"] for d in disks] == ["/dev/sdb"]
    assert disks[0]["name"] == "sdb"
    assert disks[0]["model"] == "TOSHIBA DT01ACA300"
    assert disks[0]["mountpoints"] == ["/mnt/disks/toshiba3"]
    assert disks[0]["rotational"] is True


def test_the_system_disk_is_marked_even_through_lvm():
    disks = {d["path"]: d for d in sleepconf.parse_physical_disks(
        lsblk(ROOT_ON_LVM, DATA_DISK, SWAP_DISK))}
    assert disks["/dev/sda"]["system"] is True
    assert disks["/dev/sdd"]["system"] is True, "a disk holding swap is system"
    assert disks["/dev/sdb"]["system"] is False


def test_a_zfs_root_member_is_not_detectable_from_lsblk_alone():
    """Pins the reason parse_zfs_root_pool exists.

    Nothing in this disk's lsblk output says "system": the partition has no
    mountpoint, so the inherited rule cannot fire. Without the separate
    findmnt/zpool lookup the Proxmox system disk would be offered for
    spin-down on every ZFS-on-root install.
    """
    disk = sleepconf.parse_physical_disks(lsblk(ZFS_ROOT_DISK))[0]
    assert disk["system"] is False
    assert "zfs_member" in disk["fstypes"]


def test_ssds_are_reported_but_flagged():
    disk = sleepconf.parse_physical_disks(lsblk(ROOT_ON_LVM))[0]
    assert disk["rotational"] is False


def test_rota_as_a_string_is_understood():
    """Older lsblk emits "1"/"0" rather than JSON booleans."""
    raw = {**DATA_DISK, "rota": "0"}
    assert sleepconf.parse_physical_disks(lsblk(raw))[0]["rotational"] is False


def test_broken_lsblk_output_is_not_fatal():
    assert sleepconf.parse_physical_disks("not json") == []


# --- ZFS --------------------------------------------------------------------

def test_zfs_root_pool_is_read_from_findmnt():
    assert sleepconf.parse_zfs_root_pool("rpool/ROOT/pve-1 zfs\n") == "rpool"


def test_a_non_zfs_root_yields_no_pool():
    assert sleepconf.parse_zfs_root_pool("/dev/mapper/pve-root ext4\n") is None
    assert sleepconf.parse_zfs_root_pool("") is None


ZPOOL_LIST = """\
fontos\t3.62T\t1.20T\t2.42T\t-\t-\t2%\t33%\t1.00x\tONLINE\t-
\tmirror-0\t3.62T\t1.20T\t2.42T\t-\t-\t2%\t33%\t-\tONLINE
\t\t/dev/sdc1\t-\t-\t-\t-\t-\t-\t-\tONLINE
\t\t/dev/sde1\t-\t-\t-\t-\t-\t-\t-\tONLINE
"""


def test_zpool_member_devices_are_the_paths_only():
    assert sleepconf.parse_zpool_devices(ZPOOL_LIST) == ["/dev/sdc1", "/dev/sde1"]


def test_zfs_property_is_the_bare_value():
    assert sleepconf.parse_zfs_property("on\n") == "on"
    assert sleepconf.parse_zfs_property("") == ""


# --- hd-idle import ---------------------------------------------------------

# Copied from a working installation, including the trailing fragment.
REAL_HD_IDLE = '''
START_HD_IDLE=true
HD_IDLE_OPTS="-i 0 -a /dev/disk/by-id/ata-WDC_WD10SPCX-21KHST0_WD-WX21A44D9471 -i 300 -a /dev/disk/by-id/ata-TOSHIBA_DT01ACA300_334401VAS -i 300"
'''


def test_hd_idle_options_are_imported_per_disk():
    default, per_disk = sleepconf.parse_hd_idle_opts(REAL_HD_IDLE)
    assert default == 0, "the -i before the first -a is the default"
    assert per_disk == {
        "ata-WDC_WD10SPCX-21KHST0_WD-WX21A44D9471": 300,
        "ata-TOSHIBA_DT01ACA300_334401VAS": 300,
    }


def test_hd_idle_import_survives_a_missing_or_broken_line():
    assert sleepconf.parse_hd_idle_opts("START_HD_IDLE=true\n") == (0, {})
    assert sleepconf.parse_hd_idle_opts('HD_IDLE_OPTS="-i"') == (0, {})
    assert sleepconf.parse_hd_idle_opts('HD_IDLE_OPTS="-i abc -a x -i 60"')[1] == {"x": 60}


def test_imported_timings_snap_onto_the_offered_choices():
    # 300s has no exact match; 15 minutes is the closest one offered.
    assert sleepconf.nearest_idle_choice(300, IDLE_CHOICES) == 900
    assert sleepconf.nearest_idle_choice(1800, IDLE_CHOICES) == 1800
    assert sleepconf.nearest_idle_choice(0, IDLE_CHOICES) == 0
    assert sleepconf.nearest_idle_choice(99999, IDLE_CHOICES) == 21600


# --- smartd -----------------------------------------------------------------

SMARTD_DEFAULT = """\
# /etc/smartd.conf
DEVICESCAN -d removable -n standby -m root -M exec /usr/share/smartmontools/smartd-runner
"""
SMARTD_WAKING = """\
# /etc/smartd.conf
# DEVICESCAN -d removable -m root
DEVICESCAN -d removable -m root -M exec /usr/share/smartmontools/smartd-runner
/dev/sdb -a -o on
"""


def test_a_config_that_already_skips_standby_is_left_alone():
    assert sleepconf.smartd_lines_without_standby(SMARTD_DEFAULT) == []
    assert sleepconf.fix_smartd_conf(SMARTD_DEFAULT) == SMARTD_DEFAULT


def test_device_lines_without_a_power_guard_are_found_and_fixed():
    offenders = sleepconf.smartd_lines_without_standby(SMARTD_WAKING)
    assert len(offenders) == 2, "the commented-out line does not count"

    fixed = sleepconf.fix_smartd_conf(SMARTD_WAKING)
    assert fixed.splitlines()[1] == "# DEVICESCAN -d removable -m root"
    assert all(
        line.endswith("-n standby,q")
        for line in fixed.splitlines() if not line.startswith("#")
    )
    assert sleepconf.smartd_lines_without_standby(fixed) == []


def test_fixing_is_idempotent():
    once = sleepconf.fix_smartd_conf(SMARTD_WAKING)
    assert sleepconf.fix_smartd_conf(once) == once


# --- mounts -----------------------------------------------------------------

PROC_MOUNTS = """\
/dev/sdb1 /mnt/disks/toshiba3 ext4 rw,relatime 0 0
/dev/sdc1 /mnt/disks/media2 ext4 rw,noatime 0 0
"""


def test_mount_options_are_read_for_the_exact_mountpoint():
    assert "relatime" in sleepconf.mount_options(PROC_MOUNTS, "/mnt/disks/toshiba3")
    assert "noatime" in sleepconf.mount_options(PROC_MOUNTS, "/mnt/disks/media2")
    assert sleepconf.mount_options(PROC_MOUNTS, "/mnt/disks/nope") is None


def test_noatime_replaces_the_contradicting_options():
    line = "UUID=abc /mnt/disks/d1 ext4 defaults,relatime,nofail 0 2 # pnas:disk:d1"
    fixed = sleepconf.add_noatime(line)
    assert fixed == (
        "UUID=abc /mnt/disks/d1 ext4 defaults,nofail,noatime 0 2 # pnas:disk:d1"
    )
    assert sleepconf.add_noatime(fixed) == fixed, "idempotent"


# --- PVE and updatedb -------------------------------------------------------

STORAGE_CFG = """\
dir: local
\tpath /var/lib/vz
\tcontent iso,vztmpl,backup

dir: fontos-backup
\tpath /mnt/fontos/backup
\tcontent backup

zfspool: local-zfs
\tpool rpool/data
"""


def test_path_based_pve_storages_are_found():
    assert sleepconf.parse_pve_storage_paths(STORAGE_CFG) == {
        "local": "/var/lib/vz",
        "fontos-backup": "/mnt/fontos/backup",
    }


def test_prune_paths_are_read():
    conf = 'PRUNE_BIND_MOUNTS="yes"\nPRUNEPATHS="/tmp /var/spool /mnt/bulk"\n'
    assert sleepconf.parse_prune_paths(conf) == ["/tmp", "/var/spool", "/mnt/bulk"]
    assert sleepconf.parse_prune_paths("") == []


def test_path_coverage_is_not_a_prefix_test():
    assert sleepconf.path_is_covered(["/mnt/disks/media"], "/mnt/disks/media/sub")
    assert sleepconf.path_is_covered(["/mnt/disks/media"], "/mnt/disks/media")
    # The trap: same characters, different directory.
    assert not sleepconf.path_is_covered(["/mnt/disks/media"], "/mnt/disks/media2")
