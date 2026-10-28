"""scripts/mc1-slurm-deploy: override precedence and command construction."""

import importlib.machinery
import importlib.util

import pytest

from conftest import ROOT


@pytest.fixture(scope="module")
def deploy():
    path = str(ROOT / "scripts" / "mc1-slurm-deploy")
    loader = importlib.machinery.SourceFileLoader("mc1_slurm_deploy", path)
    spec = importlib.util.spec_from_loader("mc1_slurm_deploy", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_dotenv_parsing(deploy, tmp_path):
    f = tmp_path / ".env"
    f.write_text('# comment\nexport MC1_JUMP="a@b"\nMC1_SSH_USER=\'root\'\n\nnot a line\n')
    assert deploy.parse_dotenv(f) == {"MC1_JUMP": "a@b", "MC1_SSH_USER": "root"}
    assert deploy.parse_dotenv(tmp_path / "missing") == {}


def test_precedence_cli_over_env_over_dotenv(deploy):
    dotenv = {"MC1_JUMP": "dot@gw", "MC1_SSH_USER": "dotuser", "MC1_SET": "slurm_debug=debug"}
    environ = {"MC1_JUMP": "env@gw", "MC1_SET": "slurm_def_mem_per_cpu=4000"}
    cli = {"mc1_jump": "cli@gw"}
    merged = deploy.layered_overrides(dotenv, environ, cli)
    assert merged["mc1_jump"] == "cli@gw"
    assert merged["mc1_ssh_user"] == "dotuser"
    assert merged["slurm_debug"] == "debug"
    assert merged["slurm_def_mem_per_cpu"] == 4000


def test_assignments_are_yaml_typed(deploy):
    out = deploy.parse_assignments(["a=1", "b=true", "c=null", "d=x@y", "e=[1, 2]"])
    assert out == {"a": 1, "b": True, "c": None, "d": "x@y", "e": [1, 2]}
    with pytest.raises(ValueError):
        deploy.parse_assignments(["novalue"])


def args_for(deploy, argv):
    return deploy.make_parser().parse_args(argv)


def test_deploy_defaults_to_check_mode(deploy):
    cmd = deploy.build_command("deploy", args_for(deploy, ["deploy"]), None, "ap")
    assert cmd[0] == "ap" and cmd[1].endswith("playbooks/site.yml")
    assert "--check" in cmd and "--diff" in cmd


def test_deploy_apply_drops_check(deploy):
    cmd = deploy.build_command("deploy", args_for(deploy, ["deploy", "--apply"]), "/x.json", "ap")
    assert "--check" not in cmd and "--diff" in cmd
    assert cmd[-2:] == ["-e", "@/x.json"]


@pytest.mark.parametrize("action", ["deploy", "storage", "firewall", "accounts", "nfs", "cvmfs", "healthcheck"])
def test_changing_actions_default_to_check_mode(deploy, action):
    assert "--check" in deploy.build_command(action, args_for(deploy, [action]), None, "ap")
    assert "--check" not in deploy.build_command(action, args_for(deploy, [action, "--apply"]), None, "ap")


def test_only_preflight_and_validate_are_read_only(deploy):
    assert deploy.READ_ONLY == {"preflight", "validate"} and deploy.READ_ONLY <= set(deploy.PLAYBOOKS)


def test_preflight_has_no_check_flag(deploy):
    cmd = deploy.build_command("preflight", args_for(deploy, ["preflight", "--limit", "slurm_compute", "-vv"]), None, "ap")
    assert "--check" not in cmd
    assert cmd[-3:] == ["--limit", "slurm_compute", "-vv"]


def test_print_mode(deploy, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("MC1_JUMP", raising=False)
    monkeypatch.delenv("MC1_SET", raising=False)
    rc = deploy.main(["deploy", "--print", "--env-file", str(tmp_path / "none"),
                      "--jump", "mu2eshift@g2gateway01.fnal.gov", "--set", "mc1_puppet_policy=warn"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--check --diff" in out
    assert '"mc1_jump": "mu2eshift@g2gateway01.fnal.gov"' in out
    assert '"mc1_puppet_policy": "warn"' in out
