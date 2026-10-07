# mu2edaq-batch-queues

Slurm batch queues for the Mu2e **MC1 trigger worker cluster**: the
`mu2e-mc1-worker-NN` nodes inside the DAQ enclave, reached by ProxyJump through
`g2gateway01`. Deployed with Ansible from an operator workstation.

| Role | Host |
|------|------|
| Controller (`slurmctld`, `slurmdbd`, MariaDB) and submit node | `mu2e-mc1-worker-01.fnal.gov` |
| Compute nodes (`slurmd`) | `mu2e-mc1-worker-02`, `-03`; `-04 … -16` declared `State=FUTURE` |
| Ingress / ProxyJump | `mu2eshift@g2gateway01.fnal.gov` |

**Status:** the deployer is written and tested offline. Nothing is installed on
the cluster yet. Project status, decisions and test matrices are tracked in a
private Claude artifact. Its source, `docs/private/`, is git-ignored because it
records enclave access detail.

## Quick start

```sh
./bootstrap.sh                               # venv, ansible-core, ansible.posix
klist                                        # Kerberos ticket for GSSAPI ssh
scripts/mc1-slurm-deploy preflight           # read-only checks on every node
scripts/mc1-slurm-deploy deploy              # dry run: --check --diff
scripts/mc1-slurm-deploy deploy --apply      # install
scripts/mc1-slurm-deploy validate            # test jobs -> results/validate-<UTC>.json
```

mu2ebatch is uid 51329, group mu2e (9914); its .k5login is the union of
mu2eshift's and mu2edaq's on each node. See [docs/INSTALL.md](docs/INSTALL.md).

## Layout

```
config/   inventory.yml              controller and compute groups
          group_vars/all.yml         every setting (accounts, ports, partitions, slurmdbd)
playbooks/ preflight.yml site.yml validate.yml
roles/    preflight accounts munge slurm_common firewall slurmdbd slurmctld slurmd
scripts/  mc1-slurm-deploy           wrapper: CLI > env > .env > config overrides
          mc1-slurm-presurvey        read-only host survey over ssh
man/      man1/                      man pages for every script
docs/     DESIGN.md INSTALL.md       design decisions; install and operations
tests/    pytest: templates rendered by real Ansible, config consistency, wrapper
results/  survey and validation output (git-ignored)
```

## Tests

```sh
venv/bin/python -m pytest -q tests
```

The tests run offline. They render every template through Ansible on
localhost, syntax-check the playbooks, check the config for consistency, and
test the wrapper's override precedence. They have been verified with Python
3.9 + ansible-core 2.15 and Python 3.12 + ansible-core 2.18.

## Repository policy

This repository is public. Never commit the munge key or a Slurm auth key,
database credentials, survey output, or the enclave's access-control posture.
`.gitignore` covers `*.key`, `.env`, `config/*.local.yaml`, `secrets/`,
`results/` and `docs/private/`.
