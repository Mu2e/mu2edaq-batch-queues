# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`mu2edaq-batch-queues` installs and configures **Slurm** on the Mu2e **MC1
trigger worker cluster** using Ansible, run from an operator workstation.

- `mu2e-mc1-worker-01.fnal.gov` is the controller and submit node. It runs
  slurmctld, slurmdbd and MariaDB, and no slurmd.
- Workers 02–18 are compute nodes (partition `mc1`); 17–18 run AlmaLinux 10,
  the rest AlmaLinux 9. Slurm 26.05.4 is built locally (`mc1-slurm-build-rpms`)
  and installed from `artifacts/rpms/` (git-ignored, present only in the
  worktree `wt/batch-queues-ansible`).
- Every node has `/scratch` (100G) and `/data-local` (500G) LVs; worker-17
  has `/nfshome`, worker-18 `/nfsdata`. NFS export is blocked by SLAM Puppet
  (`nfs::service`), so the `nfs` role is gated off.
- CVMFS is installed by the site. The `cvmfs` role is off
  (`mc1_cvmfs_enabled: false`) and must not overwrite the site config.
- Every login goes by ProxyJump as **`mu2eshift@g2gateway01.fnal.gov`**, the
  user's rule, then as root on the workers.

Design decisions D1–D27 and their reasons are in `docs/DESIGN.md`. The user guide
is `docs/GETTING-STARTED.md` (+ `.pdf`, rebuilt per README) and `web/index.html`;
keep all three in step.

Status, open decisions and the test and compatibility matrices live in the
private tracker artifact https://claude.ai/artifact/VRZRbwR9QMDSQybSaJRxZG. Its
source is `docs/private/PROJECT-STATUS.html`, which is git-ignored because it
holds the access posture. Edit it and republish to the same URL at each phase
change.

GitHub: `git@github.com:Mu2e/mu2edaq-batch-queues.git` (public, created
2026-10-28), registered as the submodule `mu2edaq-batch-queues` of
`Mu2e/mu2edaq-main`. Releases are tagged `vMAJOR.MINOR.PATCH`; the first is
v1.0.0.

## Setup and Running

```sh
./bootstrap.sh                                   # ./venv + ansible-core + ansible.posix → ./collections
venv/bin/python -m pytest -q tests               # all tests (offline)
venv/bin/python -m pytest -q tests/test_render.py::test_slurm_conf_nodes   # one test
scripts/mc1-slurm-deploy deploy --print          # show the ansible-playbook command and overrides
scripts/mc1-slurm-deploy preflight               # read-only, touches the cluster
scripts/mc1-slurm-deploy deploy [--apply]        # --check --diff unless --apply
scripts/mc1-slurm-deploy {storage,accounts,firewall,nfs,cvmfs} [--apply]   # one role
scripts/mc1-slurm-deploy validate                # results/validate-<UTC>.json
scripts/mc1-slurm-presurvey [-n]                 # raw read-only host survey (text reports)
scripts/mc1-slurm-deploy healthcheck [--apply]   # mc1-healthcheck, cron, status site on worker-01
scripts/mc1-status-tunnel -o                     # view the status site (ssh tunnel, port 8081)
```

Pipe `ansible`/`ansible-playbook` output to a file, not to `head`, and run with
`< /dev/null`. Otherwise Ansible aborts with "requires blocking IO on
stdin/stdout/stderr".

**Harness caveat:** Claude Code's auto-mode classifier denies ssh to these
production hosts, even read-only ("production reads"), and publishing a public
GitHub repo ("out-of-place publication"). Never route around a denial. Hand
the user the command to run with the `!` prefix.

## Architecture

- **Play order (`playbooks/site.yml`).** First comes preflight, which gathers
  facts on all nodes; later plays need them for NodeName hardware lines and
  firewall sources. Then the `accounts` and `munge` roles run on every node.
  The controller play follows (`slurm_common`, `firewall`, `slurmdbd`,
  `slurmctld`), then the compute play (`slurm_common`, `firewall`, `slurmd`).
  The accounts are created before any RPM so the scriptlets reuse the fixed
  UID/GID.
- **Configless.** `slurm.conf` and `cgroup.conf` exist only on worker-01.
  Compute nodes get `SLURMD_OPTIONS=--conf-server …` from
  `/etc/sysconfig/slurmd`. A config change triggers a slurmctld restart, then
  `scontrol reconfigure` (handlers in `roles/slurmctld`).
- **Secrets stay on the cluster.** The MUNGE key is generated on worker-01,
  then `slurp`ed and written on the compute nodes via `base64 -d` from stdin
  (`no_log`, checksum-gated). The slurmdbd DB password is generated into
  `/etc/slurm/.slurmdbd-db.pw` on worker-01. Neither touches the operator
  machine or the repo.
- **Zone guard (D24).** The firewall role also installs
  `/usr/local/sbin/mc1-fw-zone-guard` (source `roles/firewall/files/`) with a
  oneshot unit and a 5 min timer. It restores `10_mc1_cluster.xml` from
  `/var/lib/mc1-slurm/firewalld` if site maintenance deletes it, which
  happened on 2026-10-16.
- **Health check (D25–D27).** `src/mc1_healthcheck` (stdlib + Jinja2 2.11 +
  PyYAML 5.4, the EL9 system packages; keep templates 2.11-compatible) is
  deployed by `roles/healthcheck` to `/opt/mc1-healthcheck` on worker-01. It
  runs from mu2ebatch's crontab, writes `/var/www/mc1-status` (httpd on
  127.0.0.1:80 only), and mails via the site Postfix on state change. View it
  with `scripts/mc1-status-tunnel`. `tests/test_healthcheck.py` drives it
  against a fake Slurm.
- **Check mode on fresh nodes.** Tasks that need files created by packages
  carry `ignore_errors: "{{ ansible_check_mode }}"`, and runtime commands
  such as `sacctmgr` and `wait_for` are skipped in check mode. Keep that
  pattern when adding tasks.
- **ansible-core version spread.** Python 3.9 controllers get ansible-core
  2.15; Python 3.10+ get 2.17/2.18 (`requirements.txt` markers). Write
  playbooks to 2.15. Example: the template lookup returns parsed data on 2.18
  but a string on 2.15, so code accepts both (`x if x is mapping else x |
  from_json`).
- **Tests.** `tests/render.yml` renders every role template and the validation
  results template with real Ansible on localhost, using the project
  inventory, group_vars and fake facts. `tests/test_render.py` asserts on the
  output. `conftest.ansible_bin` prefers the running interpreter's venv.
- **Remote survey pattern** (`scripts/mc1-slurm-presurvey`). The remote script
  goes base64-encoded and runs as `bash <(…)` under `ssh -n`. Never pipe a
  remote script into `bash -s`: `dnf` reads the rest of it as prompt answers.
- **Cluster facts behind the design.** Hosts run AlmaLinux 9.8 (01–16) and
  10.2 (17–18). Home directories are local, not NFS, so job output stays on
  the compute node. The gateway has OpenVox/Puppet repos, which is why
  preflight checks for a running Puppet agent. The detailed posture is in
  `../mu2edaq-trigger-cluster/docs/FINDINGS.md`, a local-only repo, and in the
  private tracker.

## Configuration Schema

All settings live in `config/group_vars/all.yml`, which loads because it sits
beside `config/inventory.yml`. Precedence: `--set`/options > `MC1_*` env >
`.env` > the file. The wrapper passes overrides as `-e @overrides.json`.

```yaml
mc1_jump: mu2eshift@g2gateway01.fnal.gov
mc1_ssh_user: root
mc1_puppet_policy: warn            # fail | warn
mc1_service_accounts: [{name: munge, uid: 450, gid: 450, ...}, {name: slurm, uid: 451, gid: 451, ...}]
mc1_job_accounts:                  # create:true → new local account (uid REQUIRED)
  - {name: mu2ebatch, create: true, uid: 51329, group: mu2e, gid: 9914, k5login_from: [mu2eshift, mu2edaq]}
  - {name: mu2eshift, create: false, fix_home_ownership: true}
slurm_ports: {slurmctld: 6817, slurmd: 6818, slurmdbd: 6819}
slurm_future_nodelist: ""            # all 18 workers are live
slurm_partitions: [{name: mc1, default: true, max_time: INFINITE}]
```

Per-host hardware overrides go in `config/host_vars/<fqdn>.yml` as
`slurm_node_spec`.

## Public-repository rule

This repository is public. Never commit the munge key, database passwords,
survey or validation output (`results/`), or the enclave's access-control
posture (which accounts may log in where, SELinux modes, provisioning
defects). Those belong in `docs/private/` and the private tracker.

## Testing

`venv/bin/python -m pytest -q tests`: 85 tests, offline, about 13 s; also run
them under a Python 3.9 / ansible-core 2.15 venv. Live
validation on the cluster is `scripts/mc1-slurm-deploy validate`. Its JSON keys
match the tracker's test-matrix rows.
