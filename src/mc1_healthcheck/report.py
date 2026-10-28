"""status.json, history.json and the static HTML pages.

Every file is written to a temporary name and renamed, so the web server never
serves a half-written page. Templates must stay compatible with Jinja2 2.11
(the AlmaLinux 9 python3-jinja2).
"""

import json
import os
import tempfile
import time

import jinja2

from . import __version__
from .checks import RANK

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
PAGES = ["index.html", "history.html", "about.html", "api.html", "sitemap.html"]
SCHEMA = 1


def atomic_write(path, text, mode=0o644):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=os.path.basename(path))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def build_status(overall, results, duration, host, user, now):
    nodes = {}
    for r in results:
        for node, s in r.nodes.items():
            nodes.setdefault(node, {})[r.name] = s
    return {
        "schema": SCHEMA,
        "version": __version__,
        "generated": iso(now),
        "generated_epoch": int(now),
        "host": host,
        "user": user,
        "overall": overall,
        "duration_s": duration,
        "counts": {s: sum(1 for r in results if r.status == s) for s in ("ok", "warn", "fail", "skip")},
        "checks": [r.as_dict() for r in results],
        "nodes": dict(sorted(nodes.items())),
    }


def history_entry(status):
    return {
        "generated": status["generated"],
        "generated_epoch": status["generated_epoch"],
        "overall": status["overall"],
        "duration_s": status["duration_s"],
        "checks": {c["name"]: c["status"] for c in status["checks"]},
    }


def update_history(path, status, keep):
    hist = read_json(path, [])
    if not isinstance(hist, list):
        hist = []
    hist.append(history_entry(status))
    hist = hist[-keep:] if keep > 0 else hist
    atomic_write(path, json.dumps(hist, indent=1) + "\n")
    return hist


def env():
    e = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
                           autoescape=jinja2.select_autoescape(["html", "j2"]),
                           trim_blocks=True, lstrip_blocks=True)
    e.filters["rank"] = lambda s: RANK.get(s, 0)
    return e


def render_pages(web_dir, status, history, cfg):
    """Render every page into web_dir; return the list of files written."""
    e = env()
    columns = sorted({n for h in history for n in h["checks"]},
                     key=lambda n: [c["name"] for c in status["checks"]].index(n)
                     if n in [c["name"] for c in status["checks"]] else 999)
    node_checks = [c["name"] for c in status["checks"] if c["nodes"]]
    ctx = {
        "status": status,
        "history": list(reversed(history)),
        "columns": columns,
        "node_checks": node_checks,
        "cfg": cfg,
        "web": cfg["web"],
        "version": __version__,
        "pages": PAGES,
        "uptime_pct": (100.0 * sum(1 for h in history if h["overall"] == "ok") / len(history)) if history else 0.0,
    }
    written = []
    for page in PAGES:
        html = e.get_template(page + ".j2").render(page=page, **ctx)
        path = os.path.join(web_dir, page)
        atomic_write(path, html)
        written.append(path)
    return written


def write_all(cfg, status):
    """status.json + history.json (state dir and web dir) + pages."""
    web = cfg["web"]
    os.makedirs(cfg["state_dir"], exist_ok=True)
    atomic_write(os.path.join(cfg["state_dir"], "status.json"), json.dumps(status, indent=1) + "\n")
    hist = update_history(os.path.join(cfg["state_dir"], "history.json"), status, web["history_runs"])
    written = []
    if web.get("enabled"):
        os.makedirs(web["dir"], exist_ok=True)
        for name, data in (("status.json", status), ("history.json", hist)):
            p = os.path.join(web["dir"], name)
            atomic_write(p, json.dumps(data, indent=1) + "\n")
            written.append(p)
        written += render_pages(web["dir"], status, hist, cfg)
    return written


def rerender(cfg):
    """Rebuild the pages from the saved status and history (no checks run)."""
    status = read_json(os.path.join(cfg["state_dir"], "status.json"), None)
    if status is None:
        raise FileNotFoundError("no status.json in %s; run the checks first" % cfg["state_dir"])
    hist = read_json(os.path.join(cfg["state_dir"], "history.json"), [])
    os.makedirs(cfg["web"]["dir"], exist_ok=True)
    return render_pages(cfg["web"]["dir"], status, hist, cfg)
