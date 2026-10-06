import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def ansible_bin(name):
    """Prefer the interpreter's own bin dir, so a test run under any venv uses that venv's Ansible."""
    for candidate in (Path(sys.executable).parent / name, ROOT / "venv" / "bin" / name):
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


@pytest.fixture(scope="session")
def ansible_env():
    return dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"))


@pytest.fixture(scope="session")
def rendered(tmp_path_factory, ansible_env):
    """Run tests/render.yml once and return the output directory."""
    exe = ansible_bin("ansible-playbook")
    if exe is None:
        pytest.skip("ansible-playbook not installed (run ./bootstrap.sh)")
    out = tmp_path_factory.mktemp("rendered")
    proc = subprocess.run(
        [exe, str(ROOT / "tests" / "render.yml"), "-e", "out_dir=%s" % out],
        cwd=str(ROOT), env=ansible_env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out
