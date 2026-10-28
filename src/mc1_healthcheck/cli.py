"""mc1-healthcheck command line. See mc1-healthcheck(1).

Exit status: 0 ok, 1 warn, 2 fail, 3 the checks ran but writing the pages or
sending mail failed, 4 another run holds the lock, 64 usage or config error.
"""

import argparse
import fcntl
import json
import os
import sys
import time

from . import __version__, checks, config, notify, report

EXIT = {"ok": 0, "skip": 0, "warn": 1, "fail": 2}


def parse_args(argv):
    p = argparse.ArgumentParser(prog="mc1-healthcheck",
                                description="Slurm health checks for the Mu2e MC1 cluster, "
                                            "with a status web page and mail on problems.")
    p.add_argument("-c", "--config", help="YAML config (env MC1_HC_CONFIG; default %s)" % config.DEFAULT_CONFIG)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override a config value, dotted keys allowed (e.g. mail.enabled=false); repeatable")
    p.add_argument("-q", "--quiet", action="store_true", help="print nothing unless something is wrong")
    p.add_argument("-V", "--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="command")
    r = sub.add_parser("run", help="run the checks, update the pages, mail if needed (default)")
    r.add_argument("--only", help="comma-separated checks to run (default: config 'checks')")
    r.add_argument("--no-mail", action="store_true", help="never send mail (state is still tracked)")
    r.add_argument("--no-web", action="store_true", help="do not write the web pages")
    r.add_argument("--dry-run", action="store_true",
                   help="run checks and print; write nothing, send nothing, show the mail that would go out")
    r.add_argument("--json", action="store_true", help="print status.json to stdout")
    sub.add_parser("render", help="rebuild the web pages from the last saved status")
    mt = sub.add_parser("mail-test", help="send a test message to mail.to")
    mt.add_argument("--to", help="send to this address instead")
    sub.add_parser("list", help="list the checks")
    sub.add_parser("config", help="print the effective configuration")
    a = p.parse_args(argv)
    if a.command is None:
        a = p.parse_args((argv or []) + ["run"])
    return a


def print_table(status, out=None):
    out = out or sys.stdout
    print("%s  overall %s  (%.0f s, %s@%s)" % (status["generated"], status["overall"].upper(),
                                               status["duration_s"], status["user"], status["host"]), file=out)
    for c in status["checks"]:
        print("  %-5s %-20s %s" % (c["status"], c["name"], c["summary"]), file=out)
        if c["status"] in ("warn", "fail"):
            for d in c["details"][:10]:
                print("        %s" % d, file=out)


def lock(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    f = open(os.path.join(state_dir, "run.lock"), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def cmd_run(a, cfg):
    if a.no_web:
        cfg["web"]["enabled"] = False
    held = None
    if not a.dry_run:
        held = lock(cfg["state_dir"])
        if held is None:
            print("mc1-healthcheck: another run is in progress", file=sys.stderr)
            return 4
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    ctx = checks.Context(cfg)
    try:
        overall, results, duration = checks.run_all(ctx, only)
    except ValueError as e:
        print("mc1-healthcheck: %s" % e, file=sys.stderr)
        return 64
    now = time.time()
    status = report.build_status(overall, results, duration, checks.host_fqdn(), ctx.user, now)
    rc = EXIT[overall]
    if a.json:
        json.dump(status, sys.stdout, indent=1)
        print()
    elif not a.quiet or rc:
        print_table(status)
    if a.dry_run:
        kind, state = notify.decide(status, report.read_json(os.path.join(cfg["state_dir"], "notify.json"), {}),
                                    cfg["mail"], now)
        print("\nmail: %s" % (kind or "none (no state change)"))
        if kind and cfg["mail"]["to"]:
            print(notify.compose(kind, status, state, cfg,
                                 notify.sender_of(cfg, ctx.user, status["host"])).as_string())
        return rc
    try:
        report.write_all(cfg, status)
    except OSError as e:
        print("mc1-healthcheck: writing results failed: %s" % e, file=sys.stderr)
        rc = 3
    if not a.no_mail:
        try:
            kind, _ = notify.notify(cfg, status, now)
            if kind and not a.quiet:
                print("mail: %s sent to %s" % (kind, ", ".join(cfg["mail"]["to"]) or "(nobody)"))
        except Exception as e:
            print("mc1-healthcheck: mail failed (will retry next run): %s" % e, file=sys.stderr)
            rc = 3
    held.close()
    return rc


def cmd_mail_test(a, cfg):
    to = [a.to] if a.to else cfg["mail"]["to"]
    if not to:
        print("mc1-healthcheck: mail.to is empty; use --to", file=sys.stderr)
        return 64
    from email.message import EmailMessage
    host, user = checks.host_fqdn(), checks.Context(cfg).user
    msg = EmailMessage()
    msg["Subject"] = "%s test message from %s" % (cfg["mail"]["subject_prefix"], host)
    msg["From"] = notify.sender_of(cfg, user, host)
    msg["To"] = ", ".join(to)
    msg.set_content("Test message from mc1-healthcheck %s on %s as %s.\nStatus page: %s\n"
                    % (__version__, host, user, cfg["web"]["access"]))
    try:
        notify.send(msg, cfg["mail"])
    except Exception as e:
        print("mc1-healthcheck: mail failed: %s" % e, file=sys.stderr)
        return 3
    print("sent to %s via %s" % (", ".join(to), cfg["mail"]["smtp_host"] or cfg["mail"]["sendmail"]))
    return 0


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        cfg = config.load(a.config, a.set)
    except (config.ConfigError, OSError, ValueError) as e:
        print("mc1-healthcheck: %s" % e, file=sys.stderr)
        return 64
    if a.command == "list":
        for name, _ in checks.CHECKS:
            mark = "*" if name in cfg["checks"] else " "
            print("%s %-20s %s" % (mark, name, checks.CHECK_HELP[name]))
        return 0
    if a.command == "config":
        print(json.dumps(cfg, indent=1))
        return 0
    if a.command == "render":
        try:
            for p in report.rerender(cfg):
                if not a.quiet:
                    print(p)
        except OSError as e:
            print("mc1-healthcheck: %s" % e, file=sys.stderr)
            return 3
        return 0
    if a.command == "mail-test":
        return cmd_mail_test(a, cfg)
    return cmd_run(a, cfg)
