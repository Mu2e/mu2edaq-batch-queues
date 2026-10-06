# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`mu2edaq-batch-queues` installs and configures a **Slurm** batch system on the
Mu2e **MC1 trigger worker cluster**. These are the `mu2e-mc1-worker-NN` nodes
inside the DAQ enclave. `mu2e-mc1-worker-01.fnal.gov` is the controller
(`slurmctld`) and the submit node. The other workers are compute nodes
(`slurmd`). Every login reaches the workers by ProxyJump as
**`mu2eshift@g2gateway01.fnal.gov`**, which is the user's stated rule. Slurm's
service accounts (`slurm`, `munge`), plus any job accounts the design calls for,
are created here as local users with identical UID/GID on every node.

**Status (2026-10-06): pre-install.** The repository and the read-only survey
helper exist. The deployer, the configuration and the installation do not. Open
design decisions and phase status are in the tracker, whose source is
`docs/PROJECT-STATUS.html`. Update and republish it at each phase change.

The repository lives at `Mu2e/mu2edaq-batch-queues` (public) and is a submodule
of `mu2edaq-main`.

## Setup and Running

```sh
klist                                       # Kerberos ticket required (GSSAPI ssh)
scripts/mc1-slurm-presurvey                 # read-only survey, workers 01-03 as root
scripts/mc1-slurm-presurvey --gateway       # survey the jump host
scripts/mc1-slurm-presurvey -n              # dry run: print ssh commands
```

Reports land in `results/presurvey-<UTC>/` (git-ignored). Options and their
environment overrides are in `man/man1/mc1-slurm-presurvey.1`.

**Harness caveat:** Claude Code's auto-mode classifier denies ssh logins to
these production hosts, even read-only ones, as "production reads". It also
blocks scripts that fan ssh out to many hosts. Do not route around a denial.
Give the user the command to run with the `!` prefix, or ask them to switch
auto mode off.

## Architecture

- **Remote execution pattern.** `scripts/mc1-slurm-presurvey` base64-encodes
  `scripts/lib/presurvey-remote.sh` and runs it as
  `bash <(echo … | base64 -d)` under `ssh -n`. Do not pipe a remote script into
  `bash -s`. Child processes inherit that stdin, and `dnf` will read the rest
  of the script as answers to its GPG key-import prompts. That happened
  during the first survey.
- **Two ssh hops.** The pattern is
  `ssh -J mu2eshift@g2gateway01.fnal.gov root@mu2e-mc1-worker-0N.fnal.gov`.
  Root is needed on the workers for package installs and system config. On the
  gateway, `sudo` requires a tty (`requiretty`).
- **Cluster facts that shape the Slurm design.** The access survey of
  2026-10-02 lives in `../mu2edaq-trigger-cluster/docs/FINDINGS.md`, a
  local-only repo, and the pre-install survey lives in the tracker.
  - Only workers 01–03 are live. Workers 04–16 do not answer, so the node
    inventory must be config-driven.
  - All hosts run AlmaLinux 9.8. The cgroup version on the workers is not
    yet verified; EL9 defaults to v2.
  - Home directories are local disk, not NFS, so jobs need explicit file
    staging unless a shared filesystem is added.
  - `mu2edaq` and `mu2eshift` have consistent UIDs across hosts.
  - Some worker home directories have wrong ownership.
- **Package sources.** The gateway's dnf configuration has Fermilab SLAM
  mirrors of BaseOS, AppStream, CRB, EPEL 9, OSG 23/25 and the Fermilab repo.
  It also has OpenVox (Puppet) repos and an `/etc/puppetlabs` directory.
  Before making local changes, check whether the workers are under Puppet
  management, because Puppet can revert local users, firewall rules or
  service state.

## Configuration Schema

Not yet defined. Planned location is `config/cluster.yaml`, covering the node
inventory, roles, jump host, service-account UID/GID and Slurm parameters. Keep
host-local secrets in `config/*.local.yaml` or `secrets/`, which are both
git-ignored.

## Public-repository rule

This repository is public. Never commit the munge key, a Slurm auth key,
database passwords, survey reports (`results/`), or the enclave's
access-control posture (which accounts may log in where, SELinux modes,
provisioning defects). Those belong in the private tracker artifact or in
`../mu2edaq-trigger-cluster`.

## Testing

No automated test suite yet. `bash -n` and `--dry-run` validate the survey
helper without connecting.
