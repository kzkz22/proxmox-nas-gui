"""mergerfs_env.py: what the installed mergerfs and the running kernel can do.

Both probes shell out, so every test here fakes run_unchecked/os.uname. The
cases that matter are the ambiguous ones: a package version with a Debian
revision suffix, a tarball build that prints "vunknown", and a machine where
nothing can be determined - the last of which must produce "cannot tell"
rather than a guess, because a wrong guess turns into upgrade advice.
"""

import pytest

from app.storage import mergerfs_env


@pytest.fixture
def probes(monkeypatch):
    """Fake both version sources. Pass dpkg=None to make the package query
    fail the way it does on a non-dpkg system."""
    def setup(dpkg="2.40.2-5", binary="mergerfs vunknown", kernel="6.8.12-4-pve"):
        def fake_run(cmd, timeout=30):
            if cmd[0] == "dpkg-query":
                return (0, dpkg, "") if dpkg is not None else (1, "", "no packages")
            return (0, binary, "") if binary is not None else (-1, "", "not found")
        monkeypatch.setattr(mergerfs_env, "run_unchecked", fake_run)
        monkeypatch.setattr(
            mergerfs_env.os, "uname",
            lambda: type("U", (), {"release": kernel})(),
        )
    return setup


def test_parses_a_debian_package_version():
    assert mergerfs_env.parse_version("2.40.2-5") == (2, 40, 2)


def test_parses_the_binarys_own_output():
    assert mergerfs_env.parse_version("mergerfs v2.41.0") == (2, 41, 0)


def test_vunknown_parses_to_nothing():
    # What a package built from a release tarball prints. It must not be
    # mistaken for a version, or every check downstream reasons from noise.
    assert mergerfs_env.parse_version("mergerfs vunknown") is None


def test_two_part_versions_parse():
    assert mergerfs_env.parse_version("2.41") == (2, 41)


def test_package_version_wins_over_the_binary(probes):
    # The usual Debian case: dpkg knows 2.40.2, the binary says vunknown.
    probes()
    assert mergerfs_env.installed_version() == (2, 40, 2)


def test_falls_back_to_the_binary_without_dpkg(probes):
    probes(dpkg=None, binary="mergerfs v2.42.0")
    assert mergerfs_env.installed_version() == (2, 42, 0)


def test_unknown_when_neither_source_answers(probes):
    probes(dpkg=None, binary="mergerfs vunknown")
    assert mergerfs_env.installed_version() is None


def test_new_kernel_old_mergerfs_blames_mergerfs(probes):
    # The case that prompted this check: kernel 7.0 could do passthrough,
    # Debian's 2.40.2 cannot.
    probes(dpkg="2.40.2-5", kernel="7.0.2-6-pve")

    caps = mergerfs_env.capabilities()

    assert caps["passthrough"] is False
    assert caps["passthrough_missing"] == "mergerfs"
    assert caps["mergerfs_version"] == "2.40.2"


def test_old_kernel_blames_the_kernel_not_mergerfs(probes):
    # Upgrading mergerfs would not help here, so the advice must not say to.
    probes(dpkg="2.42.0", kernel="6.5.0-1-amd64")

    caps = mergerfs_env.capabilities()

    assert caps["passthrough"] is False
    assert caps["passthrough_missing"] == "kernel"


def test_both_new_enough_means_passthrough(probes):
    probes(dpkg="2.42.0", kernel="7.0.2-6-pve")

    caps = mergerfs_env.capabilities()

    assert caps["passthrough"] is True
    assert caps["passthrough_missing"] is None


def test_unknown_version_blames_nobody(probes):
    # "Cannot tell" must stay silent rather than recommend an upgrade that
    # could be a downgrade.
    probes(dpkg=None, binary="mergerfs vunknown", kernel="7.0.2-6-pve")

    caps = mergerfs_env.capabilities()

    assert caps["passthrough"] is False
    assert caps["passthrough_missing"] is None
    assert caps["mergerfs_version"] == "?"
