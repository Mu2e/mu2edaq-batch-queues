"""Consistency of config/inventory.yml and config/group_vars/all.yml."""

import re

import pytest
import yaml

from conftest import ROOT


@pytest.fixture(scope="module")
def inventory():
    return yaml.safe_load((ROOT / "config" / "inventory.yml").read_text())


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load((ROOT / "config" / "group_vars" / "all.yml").read_text())


def hosts(inv, group):
    groups = inv["all"]["children"]["slurm_cluster"]["children"]
    return list((groups[group].get("hosts") or {}).keys())


def test_single_controller_is_worker_01(inventory):
    assert hosts(inventory, "slurm_controller") == ["mu2e-mc1-worker-01.fnal.gov"]


def test_controller_is_not_compute(inventory):
    assert not set(hosts(inventory, "slurm_controller")) & set(hosts(inventory, "slurm_compute"))


def test_jump_is_mu2eshift_on_g2gateway01(cfg):
    assert cfg["mc1_jump"] == "mu2eshift@g2gateway01.fnal.gov"


def test_service_accounts(cfg):
    names = [a["name"] for a in cfg["mc1_service_accounts"]]
    assert sorted(names) == ["munge", "slurm"]
    uids = [a["uid"] for a in cfg["mc1_service_accounts"]]
    gids = [a["gid"] for a in cfg["mc1_service_accounts"]]
    assert len(set(uids)) == len(uids)
    assert len(set(gids)) == len(gids)
    assert all(200 <= u < 1000 for u in uids), "service accounts belong in the EL9 system range"


def test_job_accounts(cfg):
    accts = {a["name"]: a for a in cfg["mc1_job_accounts"]}
    assert set(accts) == {"mu2ebatch", "mu2eshift", "mu2edaq"}
    assert accts["mu2ebatch"]["create"] is True
    assert accts["mu2eshift"]["create"] is False and accts["mu2edaq"]["create"] is False
    new_uids = [a["uid"] for a in accts.values() if a["create"] and a["uid"] is not None]
    svc_uids = [a["uid"] for a in cfg["mc1_service_accounts"]]
    assert not set(new_uids) & set(svc_uids)


def expand(hostlist):
    """Expand a single-bracket Slurm hostlist like name-[04-16]."""
    hostlist = hostlist.replace(":", "-")
    m = re.fullmatch(r"(.*)\[(\d+)-(\d+)\]", hostlist)
    if not m:
        return [hostlist]
    prefix, lo, hi = m.group(1), m.group(2), m.group(3)
    return ["%s%0*d" % (prefix, len(lo), n) for n in range(int(lo), int(hi) + 1)]


def test_future_nodes_disjoint_from_inventory(inventory, cfg):
    future = set(expand(cfg["slurm_future_nodelist"])) if cfg["slurm_future_nodelist"] else set()
    live = {h.split(".")[0] for g in ("slurm_controller", "slurm_compute") for h in hosts(inventory, g)}
    assert not future & live


def test_all_workers_are_compute(inventory):
    assert hosts(inventory, "slurm_compute") == ["mu2e-mc1-worker-[02:18].fnal.gov"]


def test_ports_distinct(cfg):
    ports = cfg["slurm_ports"]
    assert len(set(ports.values())) == 3
    lo, hi = (int(x) for x in cfg["slurm_srun_port_range"].split("-"))
    assert lo < hi and not any(lo <= p <= hi for p in ports.values())


def test_mu2ebatch_identity(cfg):
    acct = next(a for a in cfg["mc1_job_accounts"] if a["name"] == "mu2ebatch")
    assert (acct["uid"], acct["group"], acct["gid"]) == (51329, "mu2e", 9914)
    assert acct["k5login_from"] == ["mu2eshift", "mu2edaq"]


def test_local_volumes(cfg):
    vols = {v["mount"]: v for v in cfg["mc1_local_volumes"]}
    assert vols["/scratch"]["size"] == "100G" and vols["/data-local"]["size"] == "500G"
    # LV names without hyphens: device-mapper would double them.
    assert all("-" not in v["lv"] for v in vols.values())
    total_gib = sum(int(v["size"].rstrip("G")) for v in vols.values())
    assert total_gib <= 710, "must fit in os_vg free space surveyed 2026-10-07"


def test_nfs_host_volumes():
    for host, lv, mount in (("17", "nfshome", "/nfshome"), ("18", "nfsdata", "/nfsdata")):
        hv = yaml.safe_load((ROOT / "config" / "host_vars" / ("mu2e-mc1-worker-%s.fnal.gov.yml" % host)).read_text())
        vols = hv["mc1_host_volumes"]
        assert [(v["lv"], v["mount"], v["size"]) for v in vols] == [(lv, mount, "100%FREE")]


def test_nfs_config(cfg):
    assert cfg["mc1_nfs_enabled"] is False, "NFS stays off until SCD lifts the Puppet pin"
    ex = {e["mount"]: e["server"] for e in cfg["mc1_nfs_exports"]}
    assert ex == {"/nfshome": "mu2e-mc1-worker-17.fnal.gov", "/nfsdata": "mu2e-mc1-worker-18.fnal.gov"}
    assert "root_squash" in cfg["mc1_nfs_export_options"].split(",")
    assert "x-systemd.automount" in cfg["mc1_nfs_mount_options"]
    assert set(cfg["mc1_nfs_layout"]) == set(ex)


def test_cvmfs_config(cfg):
    assert "mu2e.opensciencegrid.org" in cfg["mc1_cvmfs_repositories"]
    assert set(cfg["mc1_cvmfs_packages"]) >= {"cvmfs", "cvmfs-libs", "cvmfs-config-default"}
    opts = cfg["mc1_cvmfs_mount_options"].split(",")
    assert "_netdev" in opts and "nofail" in opts, "a dead squid must not hang boot"
    assert cfg["mc1_cvmfs_config_repository"] == "", "static mounts: no config repository"
    assert cfg["mc1_cvmfs_enabled"] is False, "the site already installs CVMFS; never overwrite its config"
