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
