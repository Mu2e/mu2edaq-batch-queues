# Using the MC1 Slurm cluster

All examples were run as `mu2ebatch` on 2026-10-08.

## The cluster

| | |
|---|---|
| Submit node | `mu2e-mc1-worker-01` (also runs slurmctld and slurmdbd; runs no jobs) |
| Partition | `mc1` (default), no time limit |
| Compute nodes | `mu2e-mc1-worker-[02-18]` |
| 02–16 | 2 sockets × 8 cores, 1 thread/core → 16 CPUs; ~60 GB for jobs |
| 17–18 | 1 socket × 16 cores, 2 threads/core → 32 CPUs; ~60 GB for jobs |
| Default memory | 2000 MB per CPU if the job asks for none |
| Who can submit | `mu2ebatch`, `mu2eshift`, `mu2edaq` (Slurm account `mu2e`) |
| Slurm | 26.05.4 |

Accounting associations are enforced: an account that is not registered in
slurmdbd gets `Invalid account or account/partition combination`.

## Log in to the submit node

```sh
klist                                      # need a valid FNAL.GOV ticket
ssh -J mu2eshift@g2gateway01.fnal.gov mu2ebatch@mu2e-mc1-worker-01.fnal.gov
```

Any principal in the account's `.k5login` can log in this way.
`mu2eshift@…` and `mu2edaq@…` work the same.

## Files: there is no shared filesystem

Home directories and `/scratch` and `/data-local` are **local to each node**.
A job writes its output on the compute node that ran it, not on worker-01.

- Write job output under `/data-local/<you>/…` or `/scratch/<you>/…`, which
  exist on every node, and copy the results back afterwards.
- `--chdir` must name a directory that exists on the compute node. `/tmp`,
  `/scratch` and `/data-local` always exist.
- To stage an input file to every allocated node, use `sbcast` inside the job
  script. See the example below.

## Smoke tests

```sh
sinfo                       # partition state: 17 nodes idle
sinfo -N -l                 # per-node CPUs, memory, state
scontrol ping               # controller up

srun -N1 hostname -s                          # one task on any node
srun -N17 --chdir=/tmp hostname -s | sort     # one task on every node
srun -w mu2e-mc1-worker-17 -c4 --chdir=/tmp nproc   # 4 CPUs on a chosen node
```

## Batch job

`hello.sbatch`:

```bash
#!/bin/bash
#SBATCH --job-name=hello
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:10:00
#SBATCH --chdir=/data-local
#SBATCH --output=/data-local/%u/slurm-%x-%j.out

mkdir -p /data-local/$USER
echo "job $SLURM_JOB_ID on $(hostname -s) with $SLURM_CPUS_PER_TASK CPUs"
srun sleep 5
```

```sh
mkdir -p /data-local/$USER
sbatch hello.sbatch                  # prints: Submitted batch job <id>
squeue -u $USER                      # pending/running
sacct -j <id> -X -o JobID,JobName,State,Elapsed,NodeList%20,ExitCode
```

Slurm creates the output file before the script runs, so the
`/data-local/<user>` directory has to exist on the node first. Create it once
on every node with a one-off job:

```sh
srun -N17 --chdir=/tmp mkdir -p /data-local/$USER
```

Read the output on the node that ran the job. `sacct` truncates columns to
15 characters by default, so ask for the node name with `-P`:

```sh
node=$(sacct -j <id> -X -n -P -o NodeList)
srun -w $node --chdir=/tmp cat /data-local/$USER/slurm-hello-<id>.out
```

## Array jobs (many independent tasks)

```bash
#!/bin/bash
#SBATCH --job-name=sweep
#SBATCH --array=0-99%34          # 100 tasks, at most 34 at once
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=2G
#SBATCH --chdir=/scratch
#SBATCH --output=/scratch/%u/sweep-%A_%a.out

./my_program --index "$SLURM_ARRAY_TASK_ID"
```

## Staging an input file to the allocated nodes

```bash
#SBATCH --nodes=4
sbcast /data-local/$USER/input.root /scratch/$USER/input.root   # copy to every node in the job
srun myprog /scratch/$USER/input.root
```

## Interactive session

```sh
salloc -N1 -c8 --mem=16G --time=1:00:00     # get an allocation
srun --pty bash                             # shell on the allocated node
exit; exit                                  # leave the shell, release the allocation
```

## Watching and controlling jobs

| Task | Command |
|---|---|
| My queue | `squeue -u $USER` |
| Why is it pending | `squeue -j <id> -o "%i %T %r"` |
| Job details | `scontrol show job <id>` |
| Cancel one / all mine | `scancel <id>` / `scancel -u $USER` |
| History today | `sacct -S today -X -o JobID,JobName,State,Elapsed,MaxRSS,NodeList%20` |
| Efficiency of a finished job | `sacct -j <id> -o JobID,TotalCPU,Elapsed,MaxRSS,ReqMem` |

## Limits that will bite

- **Memory is enforced** through cgroups. A job that exceeds its `--mem` is
  killed, and `sacct` shows `OUT_OF_MEMORY`.
- **CPUs are enforced as cores.** On workers 17–18, a core is 2 hyperthreads,
  so `-c1` still allocates a whole core there.
- **No time limit is set**, but always pass `--time`: the backfill scheduler
  starts short jobs sooner.

## Operator commands (root on worker-01)

```sh
scontrol update nodename=mu2e-mc1-worker-05 state=drain reason="maintenance"
scontrol update nodename=mu2e-mc1-worker-05 state=resume
sacctmgr add user <name> account=mu2e           # allow another account to submit
```

Cluster-wide changes go through the repository:
`scripts/mc1-slurm-deploy deploy` (dry run), then `deploy --apply`.
