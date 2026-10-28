"""Templates rendered by real Ansible (tests/render.yml) with fake facts."""

import json
import subprocess

import pytest

from conftest import ROOT, ansible_bin


def conf_lines(path):
    return [l for l in path.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]


def conf_dict(path):
    out = {}
    for line in conf_lines(path):
        if line.startswith(("NodeName=", "PartitionName=")):
            continue
        key, _, value = line.partition("=")
        out[key] = value
    return out


def test_slurm_conf_core_settings(rendered):
    conf = conf_dict(rendered / "slurm.conf")
    assert conf["ClusterName"] == "mc1"
    assert conf["SlurmctldHost"] == "mu2e-mc1-worker-01(mu2e-mc1-worker-01.fnal.gov)"
    assert conf["SlurmctldParameters"] == "enable_configless"
    assert conf["AuthType"] == "auth/munge"
    assert conf["AccountingStorageType"] == "accounting_storage/slurmdbd"
    assert conf["AccountingStorageHost"] == "mu2e-mc1-worker-01.fnal.gov"
    assert conf["AccountingStorageEnforce"] == "associations,limits"
    assert conf["ProctrackType"] == "proctrack/cgroup"
    assert conf["SelectTypeParameters"] == "CR_Core_Memory"
    assert conf["SrunPortRange"] == "60001-63000"


def test_slurm_conf_nodes(rendered):
    nodes = [l for l in conf_lines(rendered / "slurm.conf") if l.startswith("NodeName=")]
    names = [l.split()[0].split("=", 1)[1] for l in nodes]
    # Decision D4: the controller runs no slurmd and is not a node.
    assert "mu2e-mc1-worker-01" not in names
    assert names == ["mu2e-mc1-worker-02", "mu2e-mc1-worker-03", "mu2e-mc1-worker-[04-16]"]
    assert nodes[0].endswith("State=UNKNOWN")
    assert "NodeAddr=mu2e-mc1-worker-02.fnal.gov" in nodes[0]
    assert "Sockets=2 CoresPerSocket=16 ThreadsPerCore=2 RealMemory=254000" in nodes[0]
    # FUTURE nodes copy the first compute node's hardware.
    assert nodes[2].endswith("State=FUTURE")
    assert "Sockets=2 CoresPerSocket=16 ThreadsPerCore=2 RealMemory=254000" in nodes[2]


def test_slurm_conf_partition(rendered):
    parts = [l for l in conf_lines(rendered / "slurm.conf") if l.startswith("PartitionName=")]
    assert parts == [
        "PartitionName=mc1 Nodes=mu2e-mc1-worker-02,mu2e-mc1-worker-03,mu2e-mc1-worker-[04-16] "
        "Default=YES MaxTime=INFINITE State=UP"
    ]


def test_cgroup_conf(rendered):
    conf = conf_dict(rendered / "cgroup.conf")
    assert conf["CgroupPlugin"] == "cgroup/v2"
    assert conf["ConstrainRAMSpace"] == "yes"
    assert conf["ConstrainCores"] == "yes"


def test_slurmdbd_conf(rendered):
    conf = conf_dict(rendered / "slurmdbd.conf")
    assert conf["DbdHost"] == "mu2e-mc1-worker-01"
    assert conf["StorageHost"] == "localhost"
    assert conf["StoragePass"] == "test-password"
    assert conf["StorageLoc"] == "slurm_acct_db"
    assert conf["PurgeJobAfter"] == "24months"


def test_mariadb_loopback_only(rendered):
    assert "bind-address=127.0.0.1" in (rendered / "mariadb-slurmdbd.cnf").read_text()


def test_slurmd_configless(rendered):
    text = (rendered / "sysconfig-slurmd").read_text()
    assert 'SLURMD_OPTIONS="--conf-server mu2e-mc1-worker-01.fnal.gov:6817"' in text


def test_validate_results(rendered):
    res = json.loads((rendered / "validate-results.json").read_text())
    c = res["checks"]
    assert c["slurmctld_ping"] is True
    assert c["node_state"] == {"mu2e-mc1-worker-02": "idle", "mu2e-mc1-worker-03": "down*"}
    assert c["node_idle"] == {"mu2e-mc1-worker-02": True, "mu2e-mc1-worker-03": False}
    assert c["munge_roundtrip"] == {"mu2e-mc1-worker-02": True, "mu2e-mc1-worker-03": False}
    assert c["srun_hostname"] == {"mu2e-mc1-worker-02": True, "mu2e-mc1-worker-03": False}
    assert c["fw_zone_guard"] == {"mu2e-mc1-worker-01": True, "mu2e-mc1-worker-02": False}
    assert c["sbatch"] == {"mu2e-mc1-worker-02": True, "mu2e-mc1-worker-03": False}
    # rc 0 means the 64M job was NOT killed: the limit is not enforced.
    assert c["memory_limit_enforced"] == {"mu2e-mc1-worker-02": True, "mu2e-mc1-worker-03": False}
    assert c["multi_node_srun"] is False
    assert c["cvmfs_in_job"] == {"mu2e-mc1-worker-02": True, "mu2e-mc1-worker-03": False}
    assert c["sacct_records"] == ["17|COMPLETED"]


@pytest.mark.parametrize("playbook", ["preflight.yml", "site.yml", "storage.yml", "firewall.yml", "accounts.yml", "nfs.yml", "cvmfs.yml", "validate.yml", "healthcheck.yml"])
def test_playbook_syntax(playbook, ansible_env):
    exe = ansible_bin("ansible-playbook")
    if exe is None:
        pytest.skip("ansible-playbook not installed")
    proc = subprocess.run([exe, "--syntax-check", str(ROOT / "playbooks" / playbook)],
                          cwd=str(ROOT), env=ansible_env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_preflight_verdict_lists_all_failures(tmp_path, ansible_env):
    exe = ansible_bin("ansible-playbook")
    if exe is None:
        pytest.skip("ansible-playbook not installed")
    proc = subprocess.run([exe, str(ROOT / "tests" / "verdict.yml"), "-e", "out_dir=%s" % tmp_path],
                          cwd=str(ROOT), env=ansible_env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fail_msg = (tmp_path / "verdict-fail-case.txt").read_text().strip()
    assert fail_msg == "preflight failed on localhost: uid_set, slurm_query, slurm_version"
    assert (tmp_path / "verdict-pass-case.txt").read_text().strip() == "PASSED"


def test_k5login_union(rendered):
    # Sources trimmed, comments/blanks dropped, other accounts' sources ignored.
    assert (rendered / "k5login").read_text() == "a@FNAL.GOV\nb@FNAL.GOV\nc@FNAL.GOV\nextra@FNAL.GOV\n"


def test_nfs_exports(rendered):
    lines = conf_lines(rendered / "mc1.exports")
    opts = "rw,sync,root_squash,no_subtree_check,sec=sys"
    assert lines == ["/nfshome 131.225.246.21/32(%s) 131.225.246.22/32(%s)" % (opts, opts)]


def cvmfs_settings(path):
    return dict(l.split("=", 1) for l in conf_lines(path))


def test_cvmfs_default_local(rendered):
    kv = cvmfs_settings(rendered / "cvmfs-default.local")
    assert kv == {
        "CVMFS_REPOSITORIES": "mu2e.opensciencegrid.org,fermilab.opensciencegrid.org",
        "CVMFS_CLIENT_PROFILE": "single",
        "CVMFS_CACHE_BASE": "/var/lib/cvmfs",
        "CVMFS_QUOTA_LIMIT": "20000",
        "CVMFS_CONFIG_REPOSITORY": "",
    }


def test_cvmfs_default_local_explicit_proxy(rendered):
    kv = cvmfs_settings(rendered / "cvmfs-default.local.proxy")
    assert kv["CVMFS_HTTP_PROXY"] == '"http://squid-a:3128|http://squid-b:3128;DIRECT"'
    assert "CVMFS_CLIENT_PROFILE" not in kv
    assert kv["CVMFS_CONFIG_REPOSITORY"] == "config-osg.opensciencegrid.org"


def test_fw_zone_guard_units(rendered):
    svc = (rendered / "mc1-fw-zone-guard.service").read_text()
    assert "After=firewalld.service" in svc
    assert "Before=slurmd.service slurmctld.service" in svc
    assert "WantedBy=firewalld.service multi-user.target" in svc
    assert "ExecStart=/usr/local/sbin/mc1-fw-zone-guard -q sync" in svc
    assert "OnUnitActiveSec=5min" in (rendered / "mc1-fw-zone-guard.timer").read_text()
    env = dict(l.split("=", 1) for l in conf_lines(rendered / "mc1-fw-zone-guard.sysconfig"))
    assert env == {"MC1_FW_ZONE": "10_mc1_cluster", "MC1_FW_GUARD_STORE": "/var/lib/mc1-slurm/firewalld"}


def test_healthcheck_config(rendered):
    import yaml
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from mc1_healthcheck import config
    path = rendered / "mc1-healthcheck.yaml"
    cfg = config.load(str(path), environ={})
    assert cfg["partition"] == "mc1"
    assert cfg["controller"] == "mu2e-mc1-worker-01.fnal.gov"
    assert cfg["expected_nodes"] == ["mu2e-mc1-worker-%02d" % i for i in range(2, 19)]
    assert cfg["services"][-1] == "mc1-fw-zone-guard.timer" and cfg["zone_guard"] is True
    assert cfg["mail"]["to"] == ["anorman@fnal.gov"]
    assert cfg["mail"]["from"] == "mu2ebatch@mu2e-mc1-worker-01.fnal.gov"
    assert "-L 8081:127.0.0.1:80 -J mu2eshift@g2gateway01.fnal.gov" in cfg["web"]["access"]
    assert cfg["web"]["cluster_url"] == "http://mu2e-mc1-worker-01.fnal.gov/"
    assert set(yaml.safe_load(path.read_text())) <= set(config.DEFAULTS)


def test_healthcheck_httpd_and_wrapper(rendered):
    conf = (rendered / "httpd-mc1-status.conf").read_text()
    assert 'DocumentRoot "/var/www/mc1-status"' in conf and "Require local" in conf
    assert "httpd listens on 80 " in conf
    assert "    Require ip 131.225.0.0/16 131.225.246.21 131.225.246.22\n" in conf
    assert "Options -Indexes" in conf
    wrapper = (rendered / "mc1-healthcheck").read_text()
    assert wrapper.startswith("#!/usr/bin/python3") and 'sys.path.insert(0, "/opt/mc1-healthcheck/lib")' in wrapper
    assert "su mu2ebatch mu2e" in (rendered / "logrotate-mc1-healthcheck").read_text()
