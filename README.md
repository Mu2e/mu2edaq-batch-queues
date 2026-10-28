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

CVMFS (`/cvmfs/mu2e.opensciencegrid.org`, `/cvmfs/fermilab.opensciencegrid.org`):
provided by the site installation; `validate` checks both from jobs. The
`cvmfs` role (off by default) only serves nodes that lack CVMFS.

mu2ebatch is uid 51329, group mu2e (9914); its .k5login is the union of
mu2eshift's and mu2edaq's on each node. See [docs/INSTALL.md](docs/INSTALL.md).

## Layout

```
config/   inventory.yml              controller and compute groups
          group_vars/all.yml         every setting (accounts, ports, partitions, slurmdbd)
playbooks/ preflight.yml storage.yml site.yml nfs.yml cvmfs.yml validate.yml
roles/    preflight storage accounts munge slurm_common firewall slurmdbd slurmctld slurmd nfs cvmfs
scripts/  mc1-slurm-deploy           wrapper: CLI > env > .env > config overrides
          mc1-slurm-presurvey        read-only host survey over ssh
          mc1-cvmfs-fetch-rpms       CVMFS client RPMs, sha256-verified, for the cvmfs role
man/      man1/ man8/                man pages for every script; man8: mc1-fw-zone-guard (node side)
docs/     GETTING-STARTED.md/.pdf     user guide (also web/index.html)
          DESIGN.md INSTALL.md USAGE.md   design; install/operations; job reference
web/      index.html                 deployable getting-started page
tests/    pytest: templates rendered by real Ansible, config consistency, wrapper
results/  survey and validation output (git-ignored)
```

## User documentation

| File | What |
|------|------|
| `docs/GETTING-STARTED.md` / `.pdf` | Getting Started with Mu2e Batch in MC-1: access, accounts, disk areas, submission, getting data back |
| `docs/USAGE.md` | Longer job-submission reference |
| `web/index.html` | The getting-started guide as a deployable web page |

Rebuild the PDF after editing the Markdown (needs pandoc and WeasyPrint):

```sh
cd docs && pandoc GETTING-STARTED.md -s -o pdf/GETTING-STARTED.html --css print.css \
    --metadata pagetitle="Getting Started with Mu2e Batch in MC-1" \
  && (cd pdf && weasyprint GETTING-STARTED.html ../GETTING-STARTED.pdf)
```

Deploy the web page by copying the one file to any static web server, for
example `scp web/index.html <host>:/var/www/html/mc1-batch/index.html`. It
loads no external fonts, scripts or stylesheets, so it also works inside the
enclave. It follows the reader's light or dark theme and prints cleanly.

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
