"""mc1-healthcheck: periodic Slurm health checks for the Mu2e MC1 cluster.

Runs non-disruptive checks on the controller (worker-01), writes status.json,
history.json and static HTML pages for the local web server, and mails the
operators when the cluster state turns bad, changes, or recovers.
"""

__version__ = "1.0.0"
