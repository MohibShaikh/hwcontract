"""Installed discovery is useful without granting arbitrary reads outside ROOT."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from hwcontract import contracts, server, __version__


@pytest.mark.parametrize("args, expected", [
    (["--help"], "default: stdio"),
    (["--version"], __version__),
    (["--list-contracts"], "28 bundled contract templates"),
])
def test_cli_returns_with_stdin_still_open(tmp_path, args, expected):
    # communicate() would close stdin and mask the original --help hang.
    with subprocess.Popen([sys.executable, "-m", "hwcontract.server", *args],
                          cwd=tmp_path, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
        try:
            assert proc.wait(timeout=10) == 0
            assert expected in proc.stdout.read()
        finally:
            if proc.poll() is None:
                proc.kill()


def test_cli_json_matches_mcp_catalog(tmp_path):
    result = subprocess.run([sys.executable, "-m", "hwcontract.server",
                             "--list-contracts", "--json"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10, check=True)
    cli = json.loads(result.stdout)
    response = server._call({"name": "list_contracts", "arguments": {}})
    assert not response["isError"]
    assert json.loads(response["content"][0]["text"]) == cli
    assert len(cli) == 28
    by_name = {e["name"]: e for e in cli}
    assert by_name["dshot"]["contract"] == "dshot600"
    assert by_name["servo"]["capture_path"] == "custom observation adapter required"
    assert {e["kind"] for e in cli} == {"timing", "serial", "events"}
    assert all(Path(e["path"]).is_file() for e in cli)


@pytest.mark.parametrize("name", ["boot", "boot.contract.yaml"])
def test_bundled_name_outside_root_and_evidence(tmp_path, monkeypatch, name):
    monkeypatch.setattr(server, "ROOT", str(tmp_path))
    result = server.tool_judge_serial(name, "boot v3\nIMU init OK\n")
    assert result["verdict"] == "PASS"
    assert len(result["evidence"]["contract_sha256"]) == 64


def test_exact_installed_path_allowed_but_other_package_files_rejected(tmp_path):
    expected = str(contracts.EXAMPLES / "boot.contract.yaml")
    assert contracts.resolve_contract(expected, tmp_path) == expected
    for path in [str(contracts.EXAMPLES.parent / "server.py"),
                 str(contracts.EXAMPLES / "../examples/boot.contract.yaml")]:
        with pytest.raises(ValueError, match="escapes allowed root"):
            contracts.resolve_contract(path, tmp_path)


def test_project_file_precedes_bundled_name(tmp_path):
    project = tmp_path / "boot.contract.yaml"
    project.write_text("invalid contract: must not fall back to a passing bundled file\n")
    assert contracts.resolve_contract(project.name, tmp_path) == str(project)
    project.rename(tmp_path / "boot")
    assert contracts.resolve_contract("boot", tmp_path) == str(tmp_path / "boot")


@pytest.mark.parametrize("path", ["../boot.contract.yaml", "../elsewhere/boot", "/etc/passwd"])
def test_no_basename_fallback_for_outside_paths(tmp_path, path):
    with pytest.raises(ValueError, match="escapes allowed root"):
        contracts.resolve_contract(path, tmp_path)


def test_project_symlink_cannot_escape_or_fall_back(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "secret.yaml"
    outside.write_text("secret")
    for name in ["boot", "boot.contract.yaml"]:
        (root / name).symlink_to(outside)
        with pytest.raises(ValueError, match="escapes allowed root"):
            contracts.resolve_contract(name, root)
    outside.unlink()  # dangling links are still an escape, not a bundled name
    with pytest.raises(ValueError, match="escapes allowed root"):
        contracts.resolve_contract("boot", root)


def test_bundled_symlink_is_not_allowlisted(tmp_path, monkeypatch):
    package = tmp_path / "examples"
    package.mkdir()
    secret = tmp_path / "secret.yaml"
    secret.write_text("secret")
    link = package / "boot.contract.yaml"
    link.symlink_to(secret)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(contracts, "EXAMPLES", package)
    assert not contracts.bundled_paths()
    with pytest.raises(ValueError, match="escapes allowed root"):
        contracts.resolve_contract(str(link), root)
    with pytest.raises(FileNotFoundError, match="list-contracts"):
        contracts.resolve_contract("boot", root)


def test_missing_contract_suggests_discovery(tmp_path):
    with pytest.raises(FileNotFoundError, match="list-contracts"):
        contracts.resolve_contract("unknown", tmp_path)


@pytest.mark.parametrize("args", [["--http"], ["--http", "0"], ["--typo"], ["--json"]])
def test_invalid_cli_usage_errors_without_entering_stdio(tmp_path, args):
    result = subprocess.run([sys.executable, "-m", "hwcontract.server", *args],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "error:" in result.stderr


def test_default_stdin_still_speaks_mcp(tmp_path):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "judge_serial", "arguments": {
                   "contract_path": "boot", "log": "boot v3\nIMU init OK\n"}}}
    result = subprocess.run([sys.executable, "-m", "hwcontract.server"], cwd=tmp_path,
                            input=json.dumps(request) + "\n", capture_output=True,
                            text=True, timeout=10, check=True)
    response = json.loads(result.stdout)
    assert json.loads(response["result"]["content"][0]["text"])["ok"]
