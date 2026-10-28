# Install and operate

## 1. Operator toolchain

Requirements: Python 3.9 or newer, an OpenSSH client with GSSAPI, and a
Kerberos ticket.

```sh
./bootstrap.sh                        # venv + ansible-core + ansible.posix
venv/bin/python -m pytest -q tests    # offline: templates, config, wrapper
```

On Windows 11, run `bootstrap.ps1`, which forwards to WSL2.

## 2. Settle the open configuration

Edit `config/group_vars/all.yml`:

- `mc1_job_accounts[mu2ebatch].uid` is required. Preflight refuses `null`.
- `k5login` lists the principals that may ssh in as `mu2ebatch`. It is
  optional.
- The proposed `munge`/`slurm` IDs are 450/451. Preflight verifies they are
  free on every node.

## 3. Preflight (read-only)

```sh
klist
scripts/mc1-slurm-deploy preflight
```

Preflight stops on: the wrong OS, a hostname mismatch, cgroup v1, clock skew,
a running Puppet agent, UID/GID collisions, missing accounts, Slurm older
than 22.05 in the repositories, or a compute node that cannot reach the
controller.

## 4. Dry run, then apply

```sh
scripts/mc1-slurm-deploy deploy            # --check --diff, changes nothing
scripts/mc1-slurm-deploy deploy --apply
```

## 5. Validate

```sh
scripts/mc1-slurm-deploy validate          # writes results/validate-<UTC>.json
```

## Day-to-day

| Task | Command (on worker-01) |
|------|------------------------|
| Cluster state | `sinfo -N -l` |
| Queue | `squeue` |
| Drain a node | `scontrol update nodename=mu2e-mc1-worker-02 state=drain reason="maintenance"` |
| Resume a node | `scontrol update nodename=mu2e-mc1-worker-02 state=resume` |
| Job history | `sacct -S today -X -o JobID,User,State,Elapsed,NodeList` |

### Bring a FUTURE worker online

1. In `config/inventory.yml`, add `mu2e-mc1-worker-NN.fnal.gov` under
   `slurm_compute`.
2. In `config/group_vars/all.yml`, shrink `slurm_future_nodelist` so it no
   longer covers NN.
3. Run `scripts/mc1-slurm-deploy deploy`, review the diff, then rerun with
   `--apply`.

### Compute nodes all `down*` / "Not responding"

Check the cluster zone first. Site maintenance has deleted
`/etc/firewalld/zones/10_mc1_cluster.xml` before (2026-10-16). Without the
file, traffic from the compute nodes to 6817 is rejected (`No route to host`).
The firewall role installs `mc1-fw-zone-guard` (D24), which puts the file back
at boot, when firewalld starts, and every 5 min. To check or repair by hand:

```sh
ansible slurm_cluster -i config/inventory.yml -a 'mc1-fw-zone-guard check' < /dev/null > fw-check.txt
scripts/mc1-slurm-deploy firewall --apply        # recreates the zone if no saved copy exists
# then, on worker-01:
scontrol update nodename=mu2e-mc1-worker-[02-18] state=resume
```

A slurmd that started while the zone was missing may be `failed (timeout)`;
`systemctl restart slurmd` on that node. See `man man/man8/mc1-fw-zone-guard.8`.

## Health check, status page and mail

`mc1-healthcheck` runs every 30 min from the crontab of `mu2ebatch` on worker-01
(D25–D27). It writes the status site and mails `mc1_hc_mail_to` when the state
turns bad, changes, stays bad for 12 h, or recovers. Deploy or update it with:

```sh
scripts/mc1-slurm-deploy healthcheck          # dry run
scripts/mc1-slurm-deploy healthcheck --apply  # ends with one run (no mail) and a check of the site
```

httpd on worker-01 serves the site to loopback clients and to the cluster nodes
(`Require ip` plus `http` in `10_mc1_cluster` on worker-01; `mc1_hc_web_cluster_access`).
On any MC1 node: `curl -s http://mu2e-mc1-worker-01.fnal.gov/status.json`. httpd also
admits 131.225.0.0/16 (`mc1_hc_web_allow_nets`), but other FNAL hosts are still
rejected by the site firewall (50_FNAL) until SCD opens port 80.
From anywhere else the site firewall rejects port 80, so use the tunnel:

```sh
scripts/mc1-status-tunnel -o      # ssh -L 8081:127.0.0.1:80 -J mu2eshift@g2gateway01 ...; opens http://localhost:8081/
scripts/mc1-status-tunnel -c      # print the overall status and anything not ok, then close
```

On worker-01, as mu2ebatch: `mc1-healthcheck run --no-mail`, `mc1-healthcheck
run --dry-run` (shows the mail that would go out), `mc1-healthcheck mail-test`,
and `tail /var/lib/mc1-healthcheck/cron.log`. Settings are the `mc1_hc_*`
variables in `config/group_vars/all.yml`; see `man man/man1/mc1-healthcheck.1`.

## Local volumes (/scratch, /data-local)

`mc1_local_volumes` in `config/group_vars/all.yml` defines LVs carved from
`os_vg` on every host in `mc1_workers` (workers 01–18): `scratch` 100G on
`/scratch` and `data_local` 500G on `/data-local`, both xfs, `root:mu2e 1777`,
fstab `defaults,nofail`. The role never shrinks, reformats or mounts over a
non-empty directory or a different device.

```sh
scripts/mc1-slurm-deploy storage            # dry run
scripts/mc1-slurm-deploy storage --apply
```

Grow later with `lvextend -r -L +50G os_vg/data_local`, then update the size in
the config.

## CVMFS (/cvmfs/mu2e.opensciencegrid.org)

CVMFS is already installed on the workers by the site installation, so the
`cvmfs` role is off (`mc1_cvmfs_enabled: false`) and `deploy` skips it.
`validate` still lists every repository in `mc1_cvmfs_repositories` from a
job on each compute node (`cvmfs_in_job`).

The role is kept for a node that lacks CVMFS. It installs the client and
mounts each repository at `/cvmfs/<repo>` from `/etc/fstab`; enable it per
host, never on a node that already has a site CVMFS configuration:

```sh
scripts/mc1-cvmfs-fetch-rpms                  # once per release: RPMs -> artifacts/rpms/cvmfs/<ver>/
scripts/mc1-slurm-deploy cvmfs --set mc1_cvmfs_enabled=true --limit <host>            # dry run
scripts/mc1-slurm-deploy cvmfs --set mc1_cvmfs_enabled=true --limit <host> --apply
scripts/mc1-slurm-deploy validate             # cvmfs_in_job: every repo listed from a job on each node
```

The dry run already stops if Puppet manages CVMFS, autofs owns `/cvmfs`, the
cache filesystem is too small, or neither WPAD nor a stratum 1 answers. If
the enclave needs an explicit squid:

```sh
scripts/mc1-slurm-deploy cvmfs --set 'mc1_cvmfs_http_proxy=http://<squid>:3128' --apply
```

On a node: `cvmfs_config probe`, `cvmfs_config stat -v mu2e.opensciencegrid.org`,
`cvmfs_config showconfig mu2e.opensciencegrid.org`. To add a repository,
append it to `mc1_cvmfs_repositories` and re-run.
