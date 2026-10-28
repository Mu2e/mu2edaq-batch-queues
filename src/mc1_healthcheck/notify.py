"""Mail on state change.

A run is "bad" when its overall status is fail (or warn, with
mail.notify_on: warn). Mail goes out when
  - the state turns bad, or the set of failing checks changes;
  - it is still bad and mail.reminder_hours have passed since the last mail;
  - it turns good again (mail.send_recovery).
The decision state lives in <state_dir>/notify.json.
"""

import json
import os
import smtplib
import subprocess
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .checks import RANK
from .report import atomic_write, read_json


def bad_set(status, notify_on):
    floor = RANK[notify_on]
    return sorted(c["name"] for c in status["checks"] if RANK.get(c["status"], 0) >= floor)


def decide(status, prev, mail_cfg, now):
    """Return (kind, new_state); kind is 'problem', 'reminder', 'recovery' or None."""
    bad = bad_set(status, mail_cfg["notify_on"])
    was_bad = bool(prev.get("bad"))
    last = prev.get("last_mail_epoch", 0)
    kind = None
    if bad:
        if not was_bad or bad != prev.get("bad"):
            kind = "problem"
        elif mail_cfg["reminder_hours"] and now - last >= 3600 * mail_cfg["reminder_hours"]:
            kind = "reminder"
    elif was_bad and mail_cfg["send_recovery"]:
        kind = "recovery"
    state = {"bad": bad, "overall": status["overall"], "updated_epoch": int(now),
             "last_mail_epoch": int(now) if kind else last,
             "last_mail_kind": kind or prev.get("last_mail_kind")}
    if bad and not was_bad:
        state["bad_since"] = status["generated"]
    elif bad:
        state["bad_since"] = prev.get("bad_since", status["generated"])
    return kind, state


def compose(kind, status, state, cfg, sender):
    m = cfg["mail"]
    bad = state["bad"]
    if kind == "recovery":
        subject = "%s RECOVERED: %s all checks ok" % (m["subject_prefix"], cfg["cluster"])
    else:
        subject = "%s %s%s: %s" % (m["subject_prefix"], "REMINDER " if kind == "reminder" else "",
                                   status["overall"].upper(), ", ".join(bad))
    lines = ["MC1 Slurm health check on %s at %s (user %s)." % (status["host"], status["generated"], status["user"]),
             "Overall: %s   ok %d, warn %d, fail %d, skip %d   (%.0f s)" % (
                 status["overall"].upper(), status["counts"]["ok"], status["counts"]["warn"],
                 status["counts"]["fail"], status["counts"]["skip"], status["duration_s"])]
    if state.get("bad_since") and kind != "recovery":
        lines.append("Bad since: %s" % state["bad_since"])
    lines.append("")
    for c in status["checks"]:
        if c["status"] in ("warn", "fail"):
            lines.append("[%s] %s: %s" % (c["status"].upper(), c["name"], c["summary"]))
            for d in c["details"][:20]:
                lines.append("        " + d)
            if len(c["details"]) > 20:
                lines.append("        ... %d more" % (len(c["details"]) - 20))
    if kind == "recovery":
        lines.append("All checks are ok again.")
    lines += ["", "All checks:"]
    lines += ["  %-5s %-22s %s" % (c["status"], c["name"], c["summary"]) for c in status["checks"]]
    lines += ["", "Status page: %s" % cfg["web"]["access"],
              "On the controller: mc1-healthcheck run --no-mail   (man mc1-healthcheck)"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(m["to"])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg["X-MC1-Healthcheck"] = kind
    msg.set_content("\n".join(lines) + "\n")
    return msg


def send(msg, mail_cfg):
    if mail_cfg.get("smtp_host"):
        with smtplib.SMTP(mail_cfg["smtp_host"], int(mail_cfg.get("smtp_port", 25)), timeout=60) as s:
            s.send_message(msg)
        return
    p = subprocess.run([mail_cfg["sendmail"], "-t", "-oi"], input=msg.as_bytes(),
                       capture_output=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError("sendmail rc %d: %s" % (p.returncode, p.stderr.decode(errors="replace").strip()))


def sender_of(cfg, user, host):
    return cfg["mail"]["from"] or "%s@%s" % (user, host)


def notify(cfg, status, now, dry_run=False):
    """Decide, send, persist. Return (kind or None, message or None)."""
    m = cfg["mail"]
    path = os.path.join(cfg["state_dir"], "notify.json")
    prev = read_json(path, {})
    kind, state = decide(status, prev, m, now)
    msg = None
    if kind and m["enabled"] and m["to"]:
        msg = compose(kind, status, state, cfg, sender_of(cfg, status["user"], status["host"]))
        if not dry_run:
            send(msg, m)
    elif kind:
        state["last_mail_epoch"] = prev.get("last_mail_epoch", 0)   # nothing was sent
    if not dry_run:
        os.makedirs(cfg["state_dir"], exist_ok=True)
        atomic_write(path, json.dumps(state, indent=1) + "\n")
    return kind, msg
