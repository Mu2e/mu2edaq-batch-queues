#!/usr/bin/env bash
# bootstrap.sh - set up the operator-side toolchain for mu2edaq-batch-queues:
# ./venv with ansible-core (+ pytest unless --no-dev) and the Galaxy
# collections in ./collections. Safe to re-run; it upgrades in place.
# Linux and macOS. On Windows 11 run it inside WSL2 (see bootstrap.ps1).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
python=${PYTHON:-python3}
dev=1

usage() {
    cat <<EOF
Usage: ${0##*/} [--python PATH] [--no-dev] [-h]

  --python PATH   interpreter for the venv (env PYTHON, default python3; >= 3.9)
  --no-dev        skip test dependencies (pytest)
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --python) python=$2; shift 2 ;;
        --no-dev) dev=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

"$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || { echo "Python >= 3.9 required ($python)" >&2; exit 1; }

[[ -d venv ]] || "$python" -m venv venv
venv/bin/python -m pip install --quiet --upgrade pip
if [[ $dev -eq 1 ]]; then
    venv/bin/python -m pip install --quiet --upgrade -r requirements-dev.txt
else
    venv/bin/python -m pip install --quiet --upgrade -r requirements.txt
fi
venv/bin/ansible-galaxy collection install -r requirements.yml -p collections --upgrade >/dev/null

echo "venv:        $(venv/bin/python --version)"
echo "ansible:     $(venv/bin/ansible --version | head -1)"
echo "collections: $(ls collections/ansible_collections 2>/dev/null | tr '\n' ' ')"
