"""Health checks. Every check is non-disruptive: it reads state or runs small
jobs (named mc1hc-*) as the invoking user. Nothing restarts, drains or reboots.

A check is a function of a Context that returns one Result, or a list of
Results for node_probe, which derives several checks from one srun.
"""

import getpass
import os
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
RANK = {OK: 0, SKIP: 0, WARN: 1, FAIL: 2}

BUSY_RE = re.compile(r"nodes are busy|unable to allocate|immediate|timed out", re.I)
OOM_RE = re.compile(r"oom|out of memory|killed|memoryerror", re.I)

# Node states (sinfo %T, suffix flags stripped) that can run jobs.
USABLE_STATES = {"idle", "mixed", "allocated", "completing", "reserved", "planned"}
DRAIN_STATES = {"drained", "draining", "drain"}


def worst(statuses):
    out = OK
    for s in statuses:
        if RANK.get(s, 0) > RANK[out]:
            out = s
    return out


@dataclass
class Result:
    name: str
    title: str
    status: str = OK
    summary: str = ""
    details: list = field(default_factory=list)
    nodes: dict = field(default_factory=dict)
    duration: float = 0.0

    def as_dict(self):
        return asdict(self)


class Runner:
    """Run a command; return (rc, stdout, stderr). rc 124 = timeout, 127 = not found."""

    def __init__(self, timeout=300):
        self.timeout = timeout

    def __call__(self, cmd, timeout=None, input=None):
        try:
            p = subprocess.run(cmd, input=input, capture_output=True, text=True,
                               timeout=timeout or self.timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            def text(b):
                return b.decode(errors="replace") if isinstance(b, bytes) else (b or "")
            return 124, text(e.stdout), text(e.stderr) + "\ntimed out after %ss" % e.timeout
        except FileNotFoundError:
            return 127, "", "%s: command not found" % cmd[0]


class Context:
    def __init__(self, cfg, run=None, statvfs=os.statvfs, now=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.run = run or Runner(cfg.get("command_timeout", 300))
        self.statvfs = statvfs
        self.now = now
        self.sleep = sleep
        self.user = getpass.getuser()
        self.ctld_up = None
        self.usable = []

    # Common job options.
    def job_opts(self, name):
        c = self.cfg
        return ["-p", c["partition"], "--time=%d" % c["job_time_limit"],
                "--job-name=mc1hc-%s" % name]

    def srun_opts(self, name):
        return self.job_opts(name) + ["--immediate=%d" % self.cfg["srun_immediate"], "--chdir=/tmp"]

    def job_timeout(self):
        return self.cfg["srun_immediate"] + 60 * self.cfg["job_time_limit"] + 60

    def cancel(self, name):
        self.run(["scancel", "-u", self.user, "-n", "mc1hc-%s" % name], timeout=30)

    def sacct_states(self, jobid, expect):
        """sacct rows (JobID, State) for jobid; retried while slurmdbd catches up."""
        rows = []
        for _ in range(6):
            rc, out, _ = self.run(["sacct", "-n", "-P", "-X", "-j", jobid, "-o", "JobID,State"], timeout=30)
            rows = [l.split("|") for l in out.splitlines() if l.strip()]
            if rc == 0 and len(rows) >= expect and all(r[1] not in ("PENDING", "RUNNING") for r in rows):
                break
            self.sleep(2)
        return rows


def short(host):
    return host.split(".")[0]


# ------------------------------------------------------------- controller ---

def controller_services(ctx):
    r = Result("controller_services", "Controller services")
    units = list(ctx.cfg["services"])
    rc, out, err = ctx.run(["systemctl", "is-active"] + units, timeout=30)
    states = out.split()
    if len(states) != len(units):
        r.status, r.summary = FAIL, "systemctl failed: %s" % (err.strip() or "rc %d" % rc)
        return r
    bad = []
    for unit, state in zip(units, states):
        r.details.append("%s: %s" % (unit, state))
        if state != "active":
            bad.append(unit)
    if ctx.cfg.get("zone_guard") and any(u.startswith("mc1-fw-zone-guard") for u in units):
        _, res, _ = ctx.run(["systemctl", "show", "-p", "Result", "--value", "mc1-fw-zone-guard.service"], timeout=30)
        res = res.strip()
        r.details.append("mc1-fw-zone-guard.service last result: %s" % (res or "never ran"))
        if res and res != "success":
            bad.append("mc1-fw-zone-guard.service (%s)" % res)
    r.status = FAIL if bad else OK
    r.summary = ("not active: " + ", ".join(bad)) if bad else "%d units active" % len(units)
    return r


def controller_ping(ctx):
    r = Result("controller_ping", "slurmctld responds")
    rc, out, err = ctx.run(["scontrol", "ping"], timeout=30)
    ctx.ctld_up = rc == 0 and "is UP" in out
    r.status = OK if ctx.ctld_up else FAIL
    r.summary = (out.strip() or err.strip() or "rc %d" % rc).splitlines()[0]
    return r


def controller_disk(ctx):
    r = Result("controller_disk", "Controller disk space")
    statuses = []
    for path, min_gb in ctx.cfg["controller_paths"].items():
        try:
            st = ctx.statvfs(path)
        except OSError as e:
            r.details.append("%s: %s" % (path, e))
            statuses.append(FAIL)
            continue
        free = st.f_bavail * st.f_frsize / 2 ** 30
        s = OK if free >= min_gb else WARN
        statuses.append(s)
        r.details.append("%s: %.1f GiB free (min %s)%s" % (path, free, min_gb, "" if s == OK else " LOW"))
    r.status = worst(statuses)
    r.summary = "; ".join(r.details)
    return r


def accounting(ctx):
    r = Result("accounting", "Accounting (slurmdbd) and association")
    rc, out, err = ctx.run(["sacctmgr", "-n", "-P", "show", "assoc", "user=%s" % ctx.user,
                            "format=cluster,account"], timeout=60)
    rows = [l for l in out.splitlines() if l.strip()]
    if rc != 0:
        r.status, r.summary = FAIL, "sacctmgr failed: %s" % (err.strip() or "rc %d" % rc)
    elif not rows:
        r.status, r.summary = FAIL, "no association for %s: its jobs would be rejected" % ctx.user
    else:
        r.summary = "%s: %s" % (ctx.user, ", ".join(rows))
    return r


# ------------------------------------------------------------------ nodes ---

def node_states(ctx):
    r = Result("node_states", "Node states (sinfo)")
    if ctx.ctld_up is False:
        r.status, r.summary = SKIP, "slurmctld down"
        return r
    rc, out, err = ctx.run(["sinfo", "-h", "-N", "-p", ctx.cfg["partition"], "-o", "%N|%T|%E"], timeout=60)
    if rc != 0:
        r.status, r.summary = FAIL, "sinfo failed: %s" % (err.strip() or "rc %d" % rc)
        return r
    seen = {}
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 2:
            seen[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")
    expected = [short(n) for n in ctx.cfg["expected_nodes"]] or sorted(seen)
    if not expected:
        r.status, r.summary = FAIL, "no nodes in partition %s" % ctx.cfg["partition"]
        return r
    for node in expected:
        if node not in seen:
            r.nodes[node] = FAIL
            r.details.append("%s: absent from sinfo" % node)
            continue
        state, reason = seen[node]
        base = state.rstrip("*~#!%$@^-+").lower()
        if "*" in state or base in ("down", "fail", "failing", "unknown", "not_responding"):
            s = FAIL
        elif base in DRAIN_STATES:
            s = WARN
        elif base in USABLE_STATES:
            s = OK
        else:
            s = WARN
        r.nodes[node] = s
        if s != OK:
            r.details.append("%s: %s%s" % (node, state, " (%s)" % reason if reason and reason != "none" else ""))
    ctx.usable = [n for n in expected if r.nodes[n] == OK]
    r.status = worst(r.nodes.values())
    r.summary = "%d/%d usable" % (len(ctx.usable), len(expected))
    return r


PROBE = r"""
h=$(hostname -s)
echo "$h alive"
for p in @PATHS@; do
  f="$p/.mc1hc.$USER.$$"
  if ( : > "$f" ) 2>/dev/null; then rm -f "$f"; w=1; else w=0; fi
  free=$(df -Pk "$p" 2>/dev/null | awk 'NR==2{print int($4/1048576)}')
  echo "$h path $p $w ${free:--1}"
done
for r in @REPOS@; do
  if ls "/cvmfs/$r" 2>/dev/null | head -1 | grep -q .; then echo "$h cvmfs $r 1"; else echo "$h cvmfs $r 0"; fi
done
if timeout 5 bash -c "</dev/tcp/@CTLD@/@PORT@" 2>/dev/null; then echo "$h ctld 1"; else echo "$h ctld 0"; fi
echo "$h guard $(systemctl is-active mc1-fw-zone-guard.timer 2>/dev/null) $(systemctl show -p Result --value mc1-fw-zone-guard.service 2>/dev/null)"
"""


def probe_script(cfg):
    return (PROBE.replace("@PATHS@", " ".join(cfg["node_paths"]) or ":")
            .replace("@REPOS@", " ".join(cfg["cvmfs_repositories"]))
            .replace("@CTLD@", cfg["controller"])
            .replace("@PORT@", str(cfg["controller_port"])))


def parse_probe(out):
    data = {}
    for line in out.splitlines():
        t = line.split()
        if len(t) < 2:
            continue
        d = data.setdefault(t[0], {"paths": {}, "cvmfs": {}, "ctld": None, "guard": None})
        if t[1] == "path" and len(t) >= 5:
            d["paths"][t[2]] = (t[3] == "1", int(t[4]) if t[4].lstrip("-").isdigit() else -1)
        elif t[1] == "cvmfs" and len(t) >= 4:
            d["cvmfs"][t[2]] = t[3] == "1"
        elif t[1] == "ctld" and len(t) >= 3:
            d["ctld"] = t[2] == "1"
        elif t[1] == "guard":
            d["guard"] = (t[2] if len(t) > 2 else "", t[3] if len(t) > 3 else "")
    return data


def node_probe(ctx):
    """One srun on every usable node; yields node_srun, node_storage, node_cvmfs,
    node_ctld_port and (optionally) node_zone_guard."""
    cfg = ctx.cfg
    names = [("node_srun", "srun on every usable node"),
             ("node_storage", "Node storage writable from a job"),
             ("node_cvmfs", "CVMFS readable from a job"),
             ("node_ctld_port", "Node reaches slurmctld port")]
    if cfg.get("zone_guard"):
        names.append(("node_zone_guard", "Firewall zone guard (D24) on nodes"))
    results = [Result(n, t) for n, t in names]
    if not ctx.usable:
        for r in results:
            r.status, r.summary = SKIP, "no usable nodes"
        return results
    nodes = ctx.usable
    cmd = ["srun"] + ctx.srun_opts("probe") + ["-N", str(len(nodes)), "--ntasks-per-node=1",
                                               "-w", ",".join(nodes), "bash", "-c", probe_script(cfg)]
    rc, out, err = ctx.run(cmd, timeout=ctx.job_timeout())
    data = parse_probe(out)
    srun_r, stor_r, cvmfs_r, port_r = results[:4]
    if not data and rc != 0:
        busy = BUSY_RE.search(err) is not None
        if rc == 124:
            ctx.cancel("probe")
        for r in results:
            r.status = WARN if busy else FAIL
            r.summary = ("cluster busy: " if busy else "srun failed: ") + (err.strip().splitlines() or ["rc %d" % rc])[-1]
        return results
    for node in nodes:
        d = data.get(node)
        if d is None:
            srun_r.nodes[node] = FAIL
            for r in results[1:]:
                r.nodes[node] = SKIP
            continue
        srun_r.nodes[node] = OK
        # storage
        s = OK
        for path, min_gb in cfg["node_paths"].items():
            writable, free = d["paths"].get(path, (False, -1))
            if not writable:
                s = FAIL
                stor_r.details.append("%s: %s not writable" % (node, path))
            elif free < min_gb:
                s = worst([s, WARN])
                stor_r.details.append("%s: %s %d GiB free (min %d)" % (node, path, free, min_gb))
        stor_r.nodes[node] = s
        # cvmfs
        missing = [repo for repo in cfg["cvmfs_repositories"] if not d["cvmfs"].get(repo)]
        cvmfs_r.nodes[node] = FAIL if missing else OK
        if missing:
            cvmfs_r.details.append("%s: %s" % (node, ", ".join(missing)))
        # controller port
        port_r.nodes[node] = OK if d["ctld"] else FAIL
        if not d["ctld"]:
            port_r.details.append("%s: cannot connect to %s:%s" % (node, cfg["controller"], cfg["controller_port"]))
        # zone guard
        if cfg.get("zone_guard"):
            g = results[4]
            active, res = d["guard"] or ("", "")
            if active != "active":
                g.nodes[node] = FAIL
                g.details.append("%s: timer %s" % (node, active or "missing"))
            elif res not in ("", "success"):
                g.nodes[node] = WARN
                g.details.append("%s: last run %s" % (node, res))
            else:
                g.nodes[node] = OK
    missing = [n for n in nodes if srun_r.nodes[n] == FAIL]
    if missing:
        srun_r.details.append("no output from: " + ", ".join(missing))
        if rc != 0:
            srun_r.details.append((err.strip().splitlines() or [""])[-1])
    for r in results:
        r.status = worst(r.nodes.values())
        good = sum(1 for v in r.nodes.values() if v == OK)
        r.summary = "%d/%d ok" % (good, len(r.nodes))
    return results


# ------------------------------------------------------------------- jobs ---

def pick_node(ctx):
    """A usable node, rotating hourly so every node is exercised over a day."""
    return ctx.usable[int(ctx.now() // 3600) % len(ctx.usable)]


def jobid_of(out):
    m = re.search(r"^(\d+)", out.strip())
    return m.group(1) if m else None


def sbatch_roundtrip(ctx):
    r = Result("sbatch_roundtrip", "sbatch, run, accounting record")
    if not ctx.usable:
        r.status, r.summary = SKIP, "no usable nodes"
        return r
    cmd = (["sbatch", "--parsable", "--wait"] + ctx.job_opts("sbatch") +
           ["-N1", "-o", "/dev/null", "--chdir=/tmp", "--wrap", "hostname -s"])
    rc, out, err = ctx.run(cmd, timeout=ctx.cfg["command_timeout"])
    jobid = jobid_of(out)
    if rc == 124:
        ctx.cancel("sbatch")
        r.status, r.summary = WARN, "job %s did not finish within %ss (cluster busy?)" % (jobid, ctx.cfg["command_timeout"])
        return r
    if rc != 0 or not jobid:
        r.status, r.summary = FAIL, "sbatch failed: %s" % (err.strip() or "rc %d" % rc)
        return r
    rows = ctx.sacct_states(jobid, 1)
    states = [row[1] for row in rows if len(row) > 1]
    r.details.append("job %s: %s" % (jobid, ", ".join(states) or "no sacct record"))
    if states == ["COMPLETED"]:
        r.summary = "job %s COMPLETED" % jobid
    else:
        r.status, r.summary = FAIL, "job %s: %s" % (jobid, ", ".join(states) or "no accounting record")
    return r


def memory_limit(ctx):
    r = Result("memory_limit", "Memory limit enforced (cgroup)")
    if not ctx.usable:
        r.status, r.summary = SKIP, "no usable nodes"
        return r
    node = pick_node(ctx)
    cmd = (["srun"] + ctx.srun_opts("oom") + ["-N1", "-w", node, "--mem=64M", "python3", "-c",
           "b = bytearray(512 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096])"])
    rc, out, err = ctx.run(cmd, timeout=ctx.job_timeout())
    text = out + err
    if rc != 0 and OOM_RE.search(text):
        r.status, r.summary = OK, "%s: 64M job allocating 512M was killed" % node
    elif rc != 0 and BUSY_RE.search(err):
        r.status, r.summary = WARN, "%s: cluster busy" % node
    elif rc == 0:
        r.status, r.summary = FAIL, "%s: 64M job allocated 512M; memory limit NOT enforced" % node
    else:
        r.status, r.summary = FAIL, "%s: unexpected failure rc %d: %s" % (node, rc, err.strip()[-200:])
    r.nodes[node] = r.status
    return r


def cpu_binding(ctx):
    r = Result("cpu_binding", "CPU confinement (-c2 sees 2 CPUs)")
    if not ctx.usable:
        r.status, r.summary = SKIP, "no usable nodes"
        return r
    node = pick_node(ctx)
    rc, out, err = ctx.run(["srun"] + ctx.srun_opts("cpu") + ["-N1", "-w", node, "-c2", "nproc"],
                           timeout=ctx.job_timeout())
    got = out.strip()
    if rc == 0 and got == "2":
        r.summary = "%s: nproc = 2" % node
    elif rc != 0 and BUSY_RE.search(err):
        r.status, r.summary = WARN, "%s: cluster busy" % node
    else:
        r.status, r.summary = FAIL, "%s: nproc = %r, rc %d %s" % (node, got, rc, err.strip()[-200:])
    r.nodes[node] = r.status
    return r


def job_array(ctx):
    r = Result("job_array", "Job array (4 tasks)")
    if not ctx.usable:
        r.status, r.summary = SKIP, "no usable nodes"
        return r
    cmd = (["sbatch", "--parsable", "--wait", "--array=0-3"] + ctx.job_opts("array") +
           ["-c1", "-o", "/dev/null", "--chdir=/tmp", "--wrap", "sleep 1"])
    rc, out, err = ctx.run(cmd, timeout=ctx.cfg["command_timeout"])
    jobid = jobid_of(out)
    if rc == 124:
        ctx.cancel("array")
        r.status, r.summary = WARN, "array %s did not finish within %ss (cluster busy?)" % (jobid, ctx.cfg["command_timeout"])
        return r
    if rc != 0 or not jobid:
        r.status, r.summary = FAIL, "sbatch failed: %s" % (err.strip() or "rc %d" % rc)
        return r
    rows = ctx.sacct_states(jobid, 4)
    states = [row[1] for row in rows if len(row) > 1]
    done = states.count("COMPLETED")
    r.details = ["%s: %s" % (row[0], row[1]) for row in rows if len(row) > 1]
    if done == 4:
        r.summary = "array %s: 4/4 COMPLETED" % jobid
    else:
        r.status, r.summary = FAIL, "array %s: %d/4 COMPLETED" % (jobid, done)
    return r


CHECKS = [
    ("controller_services", controller_services),
    ("controller_ping", controller_ping),
    ("controller_disk", controller_disk),
    ("accounting", accounting),
    ("node_states", node_states),
    ("node_probe", node_probe),
    ("sbatch_roundtrip", sbatch_roundtrip),
    ("memory_limit", memory_limit),
    ("cpu_binding", cpu_binding),
    ("job_array", job_array),
]
CHECK_NAMES = [n for n, _ in CHECKS]
CHECK_HELP = {
    "controller_services": "systemctl is-active of slurmctld, slurmdbd, mariadb, munge, zone guard timer",
    "controller_ping": "scontrol ping reports slurmctld UP",
    "controller_disk": "free space on controller paths (controller_paths)",
    "accounting": "sacctmgr: slurmdbd answers and the running user has an association",
    "node_states": "sinfo: every expected node idle/mixed/allocated; drain = warn, down = fail",
    "node_probe": "one srun on every usable node: storage, CVMFS, controller port, zone guard",
    "sbatch_roundtrip": "sbatch --wait of a 1-node job, then its sacct record is COMPLETED",
    "memory_limit": "a 64M job allocating 512M is killed (node rotates hourly)",
    "cpu_binding": "srun -c2 nproc prints 2 (node rotates hourly)",
    "job_array": "4-task job array, all COMPLETED in sacct",
}
# Checks that need slurmctld and the usable node list.
NEEDS_NODES = {"node_probe", "sbatch_roundtrip", "memory_limit", "cpu_binding", "job_array"}


def run_all(ctx, only=None):
    """Run the enabled checks in order; return (overall, [Result], seconds)."""
    wanted = list(only or ctx.cfg["checks"])
    unknown = [n for n in wanted if n not in CHECK_NAMES]
    if unknown:
        raise ValueError("unknown check(s): %s" % ", ".join(unknown))
    if NEEDS_NODES & set(wanted):
        wanted += [n for n in ("controller_ping", "node_states") if n not in wanted]
    t0 = ctx.now()
    results = []
    for name, fn in CHECKS:
        if name not in wanted:
            continue
        start = time.monotonic()
        try:
            out = fn(ctx)
        except Exception as e:  # a broken check must not hide the others
            out = Result(name, name, FAIL, "internal error: %s: %s" % (type(e).__name__, e))
        for r in out if isinstance(out, list) else [out]:
            r.duration = round(time.monotonic() - start, 1)
            results.append(r)
    overall = worst(r.status for r in results)
    return overall, results, round(ctx.now() - t0, 1)


def host_fqdn():
    return socket.getfqdn()
