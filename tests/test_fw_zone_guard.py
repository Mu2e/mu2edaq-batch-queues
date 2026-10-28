"""roles/firewall/files/mc1-fw-zone-guard against a fake firewall-cmd.

The fake keeps the running zone list in a file. --reload loads the names of
the *.xml files in the zone directory, as firewalld would.
"""

import os
import subprocess

import pytest

from conftest import ROOT

GUARD = ROOT / "roles" / "firewall" / "files" / "mc1-fw-zone-guard"
ZONE = "10_mc1_cluster"
XML = '<?xml version="1.0" encoding="utf-8"?>\n<zone>\n  <port port="6817" protocol="tcp"/>\n</zone>\n'

FAKE_FIREWALL_CMD = r"""#!/bin/bash
state=$FAKE_FW/running
case $1 in
  --state)    [ -f "$FAKE_FW/up" ] ;;
  --get-zones) cat "$state" 2>/dev/null ;;
  --reload)   echo reload >> "$FAKE_FW/calls"
              (cd "$MC1_FW_ZONE_DIR" && ls *.xml 2>/dev/null | sed 's/\.xml$//' | tr '\n' ' ') > "$state" ;;
  *) exit 2 ;;
esac
"""


@pytest.fixture
def fw(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "firewall-cmd"
    fake.write_text(FAKE_FIREWALL_CMD)
    fake.chmod(0o755)
    zones = tmp_path / "zones"
    zones.mkdir()
    store = tmp_path / "store"
    (tmp_path / "up").touch()
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), FAKE_FW=str(tmp_path),
               MC1_FW_ZONE_DIR=str(zones), MC1_FW_GUARD_STORE=str(store), MC1_FW_ZONE=ZONE)

    class FW:
        live = zones / (ZONE + ".xml")
        saved = store / (ZONE + ".xml")
        root = tmp_path

        def run(self, *args):
            return subprocess.run(["bash", str(GUARD)] + list(args), env=env, capture_output=True, text=True)

        def set_running(self, *names):
            (tmp_path / "running").write_text(" ".join(names))

        def reloads(self):
            calls = tmp_path / "calls"
            return calls.read_text().count("reload") if calls.exists() else 0

    return FW()


def test_sync_saves_live_file(fw):
    fw.live.write_text(XML)
    fw.set_running(ZONE)
    p = fw.run("sync")
    assert p.returncode == 0, p.stdout + p.stderr
    assert fw.saved.read_text() == XML
    assert fw.reloads() == 0
    assert fw.run("check").returncode == 0


def test_sync_restores_deleted_file_and_reloads(fw):
    fw.live.write_text(XML)
    fw.set_running(ZONE)
    fw.run("sync")
    fw.live.unlink()          # what site maintenance did on 2026-10-16
    fw.set_running()          # after reboot: zone not loaded
    assert fw.run("check").returncode == 1
    p = fw.run("sync")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "restored" in p.stdout
    assert fw.live.read_text() == XML
    assert fw.reloads() == 1
    assert fw.run("check").returncode == 0


def test_sync_reloads_when_file_present_but_zone_not_running(fw):
    fw.live.write_text(XML)
    fw.set_running()
    p = fw.run("sync")
    assert p.returncode == 0, p.stdout + p.stderr
    assert fw.reloads() == 1


def test_sync_refreshes_copy_after_edit(fw):
    fw.live.write_text(XML)
    fw.set_running(ZONE)
    fw.run("sync")
    edited = XML.replace("6817", "2049")
    fw.live.write_text(edited)
    assert "STALE" in fw.run("check").stdout
    fw.run("sync")
    assert fw.saved.read_text() == edited


def test_missing_everywhere_is_an_error(fw):
    p = fw.run("sync")
    assert p.returncode == 2
    assert "no saved copy" in p.stdout


def test_dry_run_changes_nothing(fw):
    fw.live.write_text(XML)
    fw.set_running(ZONE)
    fw.run("sync")
    fw.live.unlink()
    p = fw.run("-n", "sync")
    assert p.returncode == 0
    assert "would restore" in p.stdout
    assert not fw.live.exists()
    assert fw.reloads() == 0


def test_firewalld_down_restores_without_reload(fw):
    fw.live.write_text(XML)
    fw.set_running(ZONE)
    fw.run("sync")
    fw.live.unlink()
    (fw.root / "up").unlink()
    p = fw.run("sync")
    assert p.returncode == 0, p.stdout + p.stderr
    assert fw.live.exists()
    assert fw.reloads() == 0


def test_option_overrides_environment(fw):
    fw.live.write_text(XML)
    other = fw.root / "other-store"
    assert fw.run("-s", str(other), "save").returncode == 0
    assert (other / (ZONE + ".xml")).exists()
    assert not fw.saved.exists()


def test_bad_mode_is_usage_error(fw):
    assert fw.run("bogus").returncode == 64
