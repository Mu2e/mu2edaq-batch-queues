"""mc1_healthcheck against a fake Slurm (no cluster, no network)."""

import json
import os
import sys
from collections import namedtuple

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "src"))

from mc1_healthcheck import checks, cli, config, notify, report  # noqa: E402

NODES = ["mu2e-mc1-worker-%02d" % i for i in range(2, 6)]
StatVfs = namedtuple("StatVfs", "f_bavail f_frsize")


class FakeCluster:
    """Answers the commands the checks run. Attributes break individual parts."""

    def __init__(self):
        self.states = {n: "idle" for n in NODES}
        self.services_down = set()
        self.ping_up = True
        self.assoc = True
        self.no_cvmfs = set()
        self.no_ctld = set()
        self.guard_off = set()
        self.silent = set()          # nodes that produce no probe output
        self.oom_enforced = True
        self.busy = False
        self.sbatch_timeout = False
        self.calls = []

    def __call__(self, cmd, timeout=None, input=None):
        self.calls.append(cmd)
        prog = cmd[0]
        if prog == "systemctl" and cmd[1] == "is-active":
            units = cmd[2:]
            return (3 if self.services_down else 0,
                    "\n".join("inactive" if u in self.services_down else "active" for u in units) + "\n", "")
        if prog == "systemctl" and cmd[1] == "show":
            return 0, "success\n", ""
        if prog == "scontrol":
            if self.ping_up:
                return 0, "Slurmctld(primary) at mu2e-mc1-worker-01 is UP\n", ""
            return 1, "Slurmctld(primary) at mu2e-mc1-worker-01 is DOWN\n", ""
        if prog == "sacctmgr":
            return 0, ("mc1|mu2e\n" if self.assoc else ""), ""
        if prog == "sinfo":
            return 0, "".join("%s|%s|%s\n" % (n, s, "Not responding" if "*" in s else "none")
                              for n, s in self.states.items()), ""
        if prog == "scancel":
            return 0, "", ""
        if prog == "sacct":
            jobid = cmd[cmd.index("-j") + 1]
            if jobid == "200":
                return 0, "".join("200_%d|COMPLETED\n" % i for i in range(4)), ""
            return 0, "%s|COMPLETED\n" % jobid, ""
        if prog == "sbatch":
            if self.sbatch_timeout:
                return 124, "100\n", "timed out after 300s"
            return 0, ("200\n" if any(a.startswith("--array") for a in cmd) else "100\n"), ""
        if prog == "srun":
            if self.busy:
                return 1, "", "srun: error: Unable to allocate resources: Requested nodes are busy\n"
            name = [a for a in cmd if a.startswith("--job-name=")][0].split("=")[1]
            if name == "mc1hc-probe":
                nodes = cmd[cmd.index("-w") + 1].split(",")
                return 0, "".join(self.probe(n) for n in nodes if n not in self.silent), ""
            if name == "mc1hc-oom":
                if self.oom_enforced:
                    return 137, "", "slurmstepd: error: Detected 1 oom_kill event in StepId=5.0\n"
                return 0, "", ""
            if name == "mc1hc-cpu":
                return 0, "2\n", ""
        return 127, "", "unexpected command %r" % (cmd,)

    def probe(self, n):
        out = ["%s alive" % n,
               "%s path /scratch 1 90" % n,
               "%s path /data-local 1 480" % n]
        for repo in ("mu2e.opensciencegrid.org", "fermilab.opensciencegrid.org"):
            out.append("%s cvmfs %s %d" % (n, repo, 0 if n in self.no_cvmfs else 1))
        out.append("%s ctld %d" % (n, 0 if n in self.no_ctld else 1))
        out.append("%s guard %s success" % (n, "inactive" if n in self.guard_off else "active"))
        return "\n".join(out) + "\n"


@pytest.fixture
def cfg(tmp_path):
    c = config.load(config_path=None, environ={"MC1_HC_CONFIG": str(tmp_path / "none.yaml")})
    c["state_dir"] = str(tmp_path / "state")
    c["web"]["dir"] = str(tmp_path / "www")
    c["expected_nodes"] = list(NODES)
    c["mail"]["to"] = ["ops@example.org"]
    return c


def ctx_for(cfg, fake, now=1_800_000_000):
    return checks.Context(cfg, run=fake, statvfs=lambda p: StatVfs(100 * 2 ** 18, 4096),
                          now=lambda: now, sleep=lambda s: None)


def by_name(results):
    return {r.name: r for r in results}


# ------------------------------------------------------------------ config ---

def test_config_precedence(tmp_path, monkeypatch):
    f = tmp_path / "config.yaml"
    f.write_text("partition: fromfile\nweb: {dir: /file}\nmail: {to: [a@x]}\n")
    (tmp_path / ".env").write_text("MC1_HC_WEB_DIR=/dotenv\nMC1_HC_MAIL_TO=b@x,c@x\n")
    monkeypatch.chdir(tmp_path)
    env = {"MC1_HC_WEB_DIR": "/env"}
    c = config.load(str(f), sets=["partition=fromcli"], environ=env)
    assert c["partition"] == "fromcli"          # cli > file
    assert c["web"]["dir"] == "/env"            # env > .env > file
    assert c["mail"]["to"] == ["b@x", "c@x"]    # .env > file
    assert c["web"]["refresh_seconds"] == 300   # default kept by the deep merge


def test_unreadable_dotenv_is_ignored(tmp_path, monkeypatch):
    d = tmp_path / "noread"
    d.mkdir()
    (d / ".env").write_text("MC1_HC_PARTITION=x\n")
    (d / ".env").chmod(0)
    monkeypatch.chdir(d)
    try:
        c = config.load(environ={"MC1_HC_CONFIG": str(tmp_path / "x.yaml")})
        assert c["partition"] in ("mc1", "x")    # root can still read it; no exception either way
    finally:
        (d / ".env").chmod(0o644)


def test_config_set_types_and_errors(tmp_path):
    c = config.load(environ={"MC1_HC_CONFIG": str(tmp_path / "x.yaml")},
                    sets=["mail.enabled=false", "srun_immediate=30"])
    assert c["mail"]["enabled"] is False and c["srun_immediate"] == 30
    with pytest.raises(config.ConfigError):
        config.load(environ={"MC1_HC_CONFIG": str(tmp_path / "x.yaml")}, sets=["mail.notify_on=sometimes"])
    with pytest.raises(config.ConfigError):
        config.load(str(tmp_path / "missing.yaml"))


def test_deployed_config_template_keys_are_known():
    """Every key the Ansible template writes must exist in the defaults."""
    import yaml
    text = (ROOT / "roles" / "healthcheck" / "templates" / "config.yaml.j2").read_text()
    keys = {line.split(":")[0] for line in text.splitlines()
            if line and not line.startswith((" ", "#", "{")) and ":" in line}
    assert keys and keys <= set(config.DEFAULTS), keys - set(config.DEFAULTS)
    assert yaml  # template is YAML once rendered (rendered in test_render)


# ------------------------------------------------------------------ checks ---

def test_all_ok(cfg):
    overall, results, _ = checks.run_all(ctx_for(cfg, FakeCluster()))
    r = by_name(results)
    assert overall == "ok", [(x.name, x.status, x.summary) for x in results]
    assert r["node_states"].summary == "4/4 usable"
    assert r["node_srun"].nodes == {n: "ok" for n in NODES}
    assert r["sbatch_roundtrip"].summary == "job 100 COMPLETED"
    assert r["job_array"].summary == "array 200: 4/4 COMPLETED"
    assert "was killed" in r["memory_limit"].summary
    assert set(r) >= {"node_storage", "node_cvmfs", "node_ctld_port", "node_zone_guard"}


def test_node_state_classification(cfg):
    fake = FakeCluster()
    fake.states.update({NODES[0]: "down*", NODES[1]: "drained", NODES[2]: "mixed"})
    ctx = ctx_for(cfg, fake)
    overall, results, _ = checks.run_all(ctx)
    r = by_name(results)
    assert r["node_states"].nodes == {NODES[0]: "fail", NODES[1]: "warn", NODES[2]: "ok", NODES[3]: "ok"}
    assert ctx.usable == [NODES[2], NODES[3]]
    probe_cmd = [c for c in fake.calls if "--job-name=mc1hc-probe" in c][0]
    assert probe_cmd[probe_cmd.index("-w") + 1] == ",".join(ctx.usable)
    assert overall == "fail"


def test_absent_node_fails(cfg):
    fake = FakeCluster()
    del fake.states[NODES[3]]
    r = by_name(checks.run_all(ctx_for(cfg, fake))[1])
    assert r["node_states"].nodes[NODES[3]] == "fail"
    assert "absent" in r["node_states"].details[0]


def test_probe_failures_per_node(cfg):
    fake = FakeCluster()
    fake.no_cvmfs.add(NODES[0])
    fake.no_ctld.add(NODES[1])
    fake.guard_off.add(NODES[2])
    fake.silent.add(NODES[3])
    r = by_name(checks.run_all(ctx_for(cfg, fake), only=["node_probe"])[1])
    assert r["node_cvmfs"].nodes[NODES[0]] == "fail"
    assert r["node_ctld_port"].nodes[NODES[1]] == "fail"
    assert r["node_zone_guard"].nodes[NODES[2]] == "fail"
    assert r["node_srun"].nodes[NODES[3]] == "fail"
    assert r["node_cvmfs"].nodes[NODES[3]] == "skip"


def test_low_space_is_warn(cfg):
    fake = FakeCluster()
    fake.probe = lambda n, orig=fake.probe: orig(n).replace("/data-local 1 480", "/data-local 1 20")
    r = by_name(checks.run_all(ctx_for(cfg, fake), only=["node_probe"])[1])
    assert r["node_storage"].status == "warn"


def test_memory_not_enforced_fails(cfg):
    fake = FakeCluster()
    fake.oom_enforced = False
    r = by_name(checks.run_all(ctx_for(cfg, fake), only=["memory_limit"])[1])
    assert r["memory_limit"].status == "fail" and "NOT enforced" in r["memory_limit"].summary


def test_busy_cluster_is_warn_not_fail(cfg):
    fake = FakeCluster()
    fake.busy = True
    overall, results, _ = checks.run_all(ctx_for(cfg, fake), only=["node_probe", "memory_limit", "cpu_binding"])
    r = by_name(results)
    assert r["node_srun"].status == "warn" and r["node_srun"].summary.startswith("cluster busy")
    assert r["memory_limit"].status == "warn" and r["cpu_binding"].status == "warn"
    assert overall == "warn"


def test_sbatch_timeout_cancels_job(cfg):
    fake = FakeCluster()
    fake.sbatch_timeout = True
    r = by_name(checks.run_all(ctx_for(cfg, fake), only=["sbatch_roundtrip"])[1])
    assert r["sbatch_roundtrip"].status == "warn"
    assert ["scancel", "-u", checks.getpass.getuser(), "-n", "mc1hc-sbatch"] in fake.calls


def test_controller_down_skips_node_checks(cfg):
    fake = FakeCluster()
    fake.ping_up = False
    fake.services_down.add("slurmctld")
    overall, results, _ = checks.run_all(ctx_for(cfg, fake))
    r = by_name(results)
    assert r["controller_services"].status == "fail" and "slurmctld" in r["controller_services"].summary
    assert r["node_states"].status == "skip" and r["sbatch_roundtrip"].status == "skip"
    assert overall == "fail"
    assert not [c for c in fake.calls if c[0] in ("srun", "sbatch")]


def test_no_association_fails(cfg):
    fake = FakeCluster()
    fake.assoc = False
    r = by_name(checks.run_all(ctx_for(cfg, fake), only=["accounting"])[1])
    assert r["accounting"].status == "fail" and "rejected" in r["accounting"].summary


def test_node_rotation_is_hourly(cfg):
    ctx = ctx_for(cfg, FakeCluster())
    ctx.usable = list(NODES)
    seen = set()
    for hour in range(8):
        ctx.now = lambda h=hour: 3600 * h + 10
        seen.add(checks.pick_node(ctx))
    assert seen == set(NODES)


def test_unknown_check_rejected(cfg):
    with pytest.raises(ValueError):
        checks.run_all(ctx_for(cfg, FakeCluster()), only=["bogus"])


def test_probe_script_substitution(cfg):
    s = checks.probe_script(cfg)
    assert "@" not in s.replace("$USER", "")
    assert "/dev/tcp/mu2e-mc1-worker-01.fnal.gov/6817" in s
    assert "for p in /scratch /data-local; do" in s


# ------------------------------------------------------------------ report ---

def status_for(cfg, fake, now=1_800_000_000):
    overall, results, dur = checks.run_all(ctx_for(cfg, fake, now))
    return report.build_status(overall, results, dur, "mu2e-mc1-worker-01.fnal.gov", "mu2ebatch", now)


def test_pages_and_json(cfg):
    fake = FakeCluster()
    fake.no_cvmfs.add(NODES[1])
    status = status_for(cfg, fake)
    written = report.write_all(cfg, status)
    www = cfg["web"]["dir"]
    names = sorted(os.path.basename(p) for p in written)
    assert names == sorted(report.PAGES + ["status.json", "history.json"])
    s = json.load(open(os.path.join(www, "status.json")))
    assert s["schema"] == 1 and s["overall"] == "fail"
    assert s["nodes"][NODES[1]]["node_cvmfs"] == "fail"
    index = open(os.path.join(www, "index.html")).read()
    assert '<span class="big fail">fail</span>' in index
    assert NODES[1] in index and 'http-equiv="refresh"' in index
    for page in ("about.html", "api.html", "sitemap.html", "history.html"):
        html = open(os.path.join(www, page)).read()
        assert "<nav" in html and 'aria-current="page"' in html
    assert "python3-jinja2" in open(os.path.join(www, "about.html")).read()
    assert "/status.json" in open(os.path.join(www, "api.html")).read()


def test_about_page_cluster_url(cfg):
    report.write_all(cfg, status_for(cfg, FakeCluster()))
    about = open(os.path.join(cfg["web"]["dir"], "about.html")).read()
    assert "loopback clients on the controller only" in about
    cfg["web"]["cluster_url"] = "http://mu2e-mc1-worker-01.fnal.gov/"
    report.rerender(cfg)
    about = open(os.path.join(cfg["web"]["dir"], "about.html")).read()
    assert "curl http://mu2e-mc1-worker-01.fnal.gov/status.json" in about


def test_html_is_escaped(cfg):
    fake = FakeCluster()
    fake.states[NODES[0]] = "down*"
    status = status_for(cfg, fake)
    status["checks"][0]["summary"] = "<script>x</script>"
    report.write_all(cfg, status)
    index = open(os.path.join(cfg["web"]["dir"], "index.html")).read()
    assert "<script>" not in index and "&lt;script&gt;" in index


def test_history_is_trimmed(cfg):
    cfg["web"]["history_runs"] = 3
    for i in range(5):
        report.write_all(cfg, status_for(cfg, FakeCluster(), now=1_800_000_000 + 60 * i))
    hist = json.load(open(os.path.join(cfg["state_dir"], "history.json")))
    assert len(hist) == 3 and hist[-1]["generated_epoch"] == 1_800_000_240


def test_rerender_without_checks(cfg):
    report.write_all(cfg, status_for(cfg, FakeCluster()))
    os.unlink(os.path.join(cfg["web"]["dir"], "index.html"))
    report.rerender(cfg)
    assert os.path.exists(os.path.join(cfg["web"]["dir"], "index.html"))


# ------------------------------------------------------------------ notify ---

def test_notify_state_machine(cfg):
    m = cfg["mail"]
    ok = status_for(cfg, FakeCluster())
    down = FakeCluster(); down.states[NODES[0]] = "down*"
    bad1 = status_for(cfg, down)
    down2 = FakeCluster(); down2.states[NODES[0]] = "down*"; down2.oom_enforced = False
    bad2 = status_for(cfg, down2)
    t, prev, seq = 1_800_000_000, {}, []
    for status, dt in [(ok, 0), (bad1, 1800), (bad1, 1800), (bad1, 13 * 3600), (bad2, 1800),
                       (ok, 1800), (ok, 1800)]:
        t += dt
        kind, prev = notify.decide(status, prev, m, t)
        seq.append(kind)
    assert seq == [None, "problem", None, "reminder", "problem", "recovery", None]


def test_warn_only_mails_when_configured(cfg):
    busy = FakeCluster(); busy.busy = True
    st = status_for(cfg, busy)
    assert st["overall"] == "warn"
    assert notify.decide(st, {}, cfg["mail"], 0)[0] is None
    cfg["mail"]["notify_on"] = "warn"
    assert notify.decide(st, {}, cfg["mail"], 0)[0] == "problem"


def fake_sendmail(tmp_path):
    out = tmp_path / "sent.eml"
    sm = tmp_path / "sendmail"
    sm.write_text("#!/bin/sh\necho \"$@\" > %s.args\ncat >> %s\n" % (out, out))
    sm.chmod(0o755)
    return str(sm), out


def test_notify_sends_once_and_persists(cfg, tmp_path):
    cfg["mail"]["sendmail"], out = fake_sendmail(tmp_path)
    down = FakeCluster(); down.states[NODES[0]] = "down*"
    st = status_for(cfg, down)
    assert notify.notify(cfg, st, 1_800_000_000)[0] == "problem"
    assert notify.notify(cfg, st, 1_800_001_800)[0] is None
    mail = out.read_text()
    assert mail.count("Subject:") == 1
    assert "Subject: [mc1-healthcheck] FAIL: node_states" in mail
    assert "To: ops@example.org" in mail and "From: mu2ebatch@mu2e-mc1-worker-01.fnal.gov" in mail
    assert "mu2e-mc1-worker-02: down* (Not responding)" in mail
    assert open(str(out) + ".args").read().split() == ["-t", "-oi"]


def test_failed_send_is_retried(cfg, tmp_path):
    sm = tmp_path / "badmail"
    sm.write_text("#!/bin/sh\necho relay refused >&2\nexit 75\n")
    sm.chmod(0o755)
    cfg["mail"]["sendmail"] = str(sm)
    down = FakeCluster(); down.states[NODES[0]] = "down*"
    st = status_for(cfg, down)
    with pytest.raises(RuntimeError, match="relay refused"):
        notify.notify(cfg, st, 1_800_000_000)
    assert not os.path.exists(os.path.join(cfg["state_dir"], "notify.json"))   # next run tries again


# --------------------------------------------------------------------- cli ---

@pytest.fixture
def cli_env(cfg, tmp_path, monkeypatch):
    """Point the CLI at a config file and a fake cluster."""
    import yaml
    cfg["mail"]["sendmail"], sent = fake_sendmail(tmp_path)
    cfgfile = tmp_path / "config.yaml"
    keep = {k: v for k, v in cfg.items() if k != "config_path"}
    cfgfile.write_text(yaml.safe_dump(keep))
    fake = FakeCluster()
    monkeypatch.setattr(checks, "Runner", lambda timeout=300: fake)
    monkeypatch.setattr(checks, "host_fqdn", lambda: "mu2e-mc1-worker-01.fnal.gov")
    monkeypatch.setattr(checks.getpass, "getuser", lambda: "mu2ebatch")
    return {"config": str(cfgfile), "fake": fake, "sent": sent, "cfg": cfg}


def test_cli_run_ok(cli_env, capsys):
    rc = cli.main(["-c", cli_env["config"], "run"])
    assert rc == 0
    assert "overall OK" in capsys.readouterr().out
    assert os.path.exists(os.path.join(cli_env["cfg"]["web"]["dir"], "index.html"))
    assert not cli_env["sent"].exists()


def test_cli_run_fail_mails_and_exit_2(cli_env, capsys):
    cli_env["fake"].states[NODES[0]] = "down*"
    assert cli.main(["-c", cli_env["config"], "-q", "run"]) == 2
    out = capsys.readouterr().out
    assert "node_states" in out          # -q still prints when not ok
    assert "FAIL: node_states" in cli_env["sent"].read_text()


def test_cli_no_mail_and_dry_run(cli_env, capsys):
    cli_env["fake"].states[NODES[0]] = "down*"
    assert cli.main(["-c", cli_env["config"], "run", "--dry-run"]) == 2
    out = capsys.readouterr().out
    assert "mail: problem" in out and "Subject: [mc1-healthcheck] FAIL" in out
    assert not os.path.exists(cli_env["cfg"]["state_dir"] + "/status.json")
    assert cli.main(["-c", cli_env["config"], "run", "--no-mail"]) == 2
    assert not cli_env["sent"].exists()


def test_cli_lock_held(cli_env):
    held = cli.lock(cli_env["cfg"]["state_dir"])
    try:
        assert cli.main(["-c", cli_env["config"], "-q", "run"]) == 4
    finally:
        held.close()


def test_cli_only_and_json(cli_env, capsys):
    assert cli.main(["-c", cli_env["config"], "run", "--only", "controller_ping", "--json", "--no-web"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert [c["name"] for c in s["checks"]] == ["controller_ping"]
    assert cli.main(["-c", cli_env["config"], "run", "--only", "nope"]) == 64


def test_cli_mail_test_and_list(cli_env, capsys):
    assert cli.main(["-c", cli_env["config"], "mail-test", "--to", "me@example.org"]) == 0
    assert "To: me@example.org" in cli_env["sent"].read_text()
    assert cli.main(["-c", cli_env["config"], "list"]) == 0
    assert "memory_limit" in capsys.readouterr().out


def test_cli_bad_config_is_64(tmp_path):
    assert cli.main(["-c", str(tmp_path / "nope.yaml"), "list"]) == 64
