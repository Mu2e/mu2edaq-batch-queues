"""Configuration: defaults < YAML file < .env < environment (MC1_HC_*) < command line.

The YAML file is /etc/mc1-healthcheck/config.yaml unless --config or
MC1_HC_CONFIG names another. A .env file next to it (or in the current
directory) holds KEY=value lines with the same names as the environment.
"""

import copy
import os

import yaml

DEFAULT_CONFIG = "/etc/mc1-healthcheck/config.yaml"

DEFAULTS = {
    "cluster": "mc1",
    "partition": "mc1",
    "controller": "mu2e-mc1-worker-01.fnal.gov",
    "controller_port": 6817,
    # Compute nodes that must be present; empty = every node sinfo lists.
    "expected_nodes": [],
    "services": ["slurmctld", "slurmdbd", "mariadb", "munge", "mc1-fw-zone-guard.timer"],
    "cvmfs_repositories": ["mu2e.opensciencegrid.org", "fermilab.opensciencegrid.org"],
    # Node paths that must be writable from a job, with minimum free GiB.
    "node_paths": {"/scratch": 10, "/data-local": 50},
    # Controller paths with minimum free GiB.
    "controller_paths": {"/var": 5, "/tmp": 2},
    "zone_guard": True,              # check mc1-fw-zone-guard on every node (D24)
    "checks": ["controller_services", "controller_ping", "controller_disk", "accounting",
               "node_states", "node_probe", "sbatch_roundtrip", "memory_limit",
               "cpu_binding", "job_array"],
    "srun_immediate": 120,           # seconds to wait for free resources before "busy"
    "job_time_limit": 3,             # minutes, for every test job
    "command_timeout": 300,          # seconds, hard limit per command
    "state_dir": "/var/lib/mc1-healthcheck",
    "web": {
        "enabled": True,
        "dir": "/var/www/mc1-status",
        "history_runs": 336,         # one week at 30 min
        "refresh_seconds": 300,
        "title": "MC1 Slurm health",
        "cluster_url": "",           # set when cluster nodes may read the site directly
        "access": "ssh -N -L 8081:localhost:80 -J mu2eshift@g2gateway01.fnal.gov "
                  "<user>@mu2e-mc1-worker-01.fnal.gov, then http://localhost:8081/",
    },
    "mail": {
        "enabled": True,
        "to": [],
        "from": "",                  # empty = <user>@<fqdn>
        "sendmail": "/usr/sbin/sendmail",
        "smtp_host": "",             # set to use SMTP instead of sendmail
        "smtp_port": 25,
        "notify_on": "fail",         # fail | warn
        "reminder_hours": 12,        # repeat while still bad; 0 = never
        "send_recovery": True,
        "subject_prefix": "[mc1-healthcheck]",
    },
}

# Environment variable -> dotted config key.
ENV_KEYS = {
    "MC1_HC_PARTITION": "partition",
    "MC1_HC_CONTROLLER": "controller",
    "MC1_HC_EXPECTED_NODES": "expected_nodes",
    "MC1_HC_CHECKS": "checks",
    "MC1_HC_STATE_DIR": "state_dir",
    "MC1_HC_WEB_ENABLED": "web.enabled",
    "MC1_HC_WEB_DIR": "web.dir",
    "MC1_HC_MAIL_ENABLED": "mail.enabled",
    "MC1_HC_MAIL_TO": "mail.to",
    "MC1_HC_MAIL_FROM": "mail.from",
    "MC1_HC_SMTP_HOST": "mail.smtp_host",
    "MC1_HC_NOTIFY_ON": "mail.notify_on",
}

LIST_KEYS = {"expected_nodes", "checks", "mail.to", "services", "cvmfs_repositories"}


class ConfigError(Exception):
    pass


def merge(base, over):
    """Recursive dict merge; values in `over` win."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def parse_dotenv(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                k, _, v = line.partition("=")
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                out[k.strip()] = v
    except OSError:      # absent, or unreadable (e.g. cwd /root under runuser)
        pass
    return out


def coerce(key, value):
    """Turn a string from env/.env/--set into a typed config value."""
    if key in LIST_KEYS:
        return [x.strip() for x in value.replace(";", ",").split(",") if x.strip()]
    parsed = yaml.safe_load(value) if value != "" else ""
    return parsed


def set_dotted(cfg, key, value):
    parts = key.split(".")
    node = cfg
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            raise ConfigError("unknown config section: %s" % key)
        node = node[p]
    node[parts[-1]] = value


def load(config_path=None, sets=(), environ=None):
    """Return the effective configuration dict."""
    env = os.environ if environ is None else environ
    path = config_path or env.get("MC1_HC_CONFIG") or DEFAULT_CONFIG
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ConfigError("%s: top level must be a mapping" % path)
        cfg = merge(cfg, data)
    elif config_path:
        raise ConfigError("config file not found: %s" % path)

    dotenv = parse_dotenv(os.path.join(os.path.dirname(os.path.abspath(path)), ".env"))
    dotenv.update(parse_dotenv(os.path.join(os.getcwd(), ".env")))
    for layer in (dotenv, env):
        for var, key in ENV_KEYS.items():
            if var in layer:
                set_dotted(cfg, key, coerce(key, layer[var]))

    for item in sets:
        if "=" not in item:
            raise ConfigError("--set wants key=value, got %r" % item)
        key, _, value = item.partition("=")
        set_dotted(cfg, key.strip(), coerce(key.strip(), value))

    if cfg["mail"]["notify_on"] not in ("fail", "warn"):
        raise ConfigError("mail.notify_on must be fail or warn")
    cfg["config_path"] = path
    return cfg
