# mu2edaq-batch-queues

Slurm batch queues for the Mu2e **MC1 trigger worker cluster**: the
`mu2e-mc1-worker-NN` nodes inside the DAQ enclave, reached by ProxyJump through
`g2gateway01`.

| Role | Host |
|------|------|
| Controller (`slurmctld`) and submit node | `mu2e-mc1-worker-01.fnal.gov` |
| Compute nodes (`slurmd`) | `mu2e-mc1-worker-02 … -NN.fnal.gov` |
| Ingress / ProxyJump | `mu2eshift@g2gateway01.fnal.gov` |

**Status:** pre-install. The read-only node survey and the design decisions come
first. Nothing is installed on the cluster yet. The project tracker is kept as a
Claude artifact; its source is [`docs/PROJECT-STATUS.html`](docs/PROJECT-STATUS.html).

## Layout

```
scripts/  mc1-slurm-presurvey        read-only pre-install survey (wrapper)
          lib/presurvey-remote.sh    survey body executed on each host
man/      man1/                      man pages
config/   (cluster inventory and Slurm settings, YAML; to come)
docs/     PROJECT-STATUS.html        tracker source
results/  survey output (git-ignored: contains enclave host detail)
```

## Pre-install survey

Requires a Kerberos ticket and an ssh client with GSSAPI.

```sh
klist
scripts/mc1-slurm-presurvey              # workers 01-03 as root, via mu2eshift@g2gateway01
scripts/mc1-slurm-presurvey --gateway    # the gateway itself
scripts/mc1-slurm-presurvey -n           # print the ssh commands only
man man/man1/mc1-slurm-presurvey.1
```

## Repository policy

This repository is public. Never commit the munge key or a Slurm auth key,
database credentials, or survey output. `.gitignore` covers `*.key`, `.env`,
`config/*.local.yaml`, `secrets/` and `results/`.
