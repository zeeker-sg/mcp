"""Installed-artifact and unchanged-server checks, using only synthetic canaries."""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import venv
import zipfile
from pathlib import Path

import pytest

from zeeker_identity.db import initialize, open_registry
from zeeker_identity.errors import RegistryError

ROOT = Path(__file__).resolve().parents[2]
CANARY = b"ARTIFACT_PRIVATE_STATE_CANARY_2099"


def execute(command, *, cwd, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    sandbox = tmp_path_factory.mktemp("distribution")
    project = sandbox / "project"
    project.mkdir()
    shutil.copytree(ROOT / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "uv.lock", "README.md", ".dockerignore", "Dockerfile"):
        shutil.copy2(ROOT / name, project / name)
    # Explicitly injected synthetic state in a throwaway context, never the repo.
    for relative in (
        "registry.sqlite3",
        ".env",
        "src/zeeker_identity/snapshot.db",
        "src/zeeker_identity/private/manifest.json",
        "src/zeeker_identity/backups/newer.sqlite3",
    ):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(CANARY)
    destination = sandbox / "artifacts"
    execute(["uv", "build", "--offline", "--out-dir", str(destination)], cwd=project)
    return project, destination


def test_wheel_and_sdist_exclude_synthetic_private_state(artifacts):
    _, destination = artifacts
    wheel = next(destination.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "zeeker_identity/cli.py" in names
        assert "mcp_zeeker/server.py" in names
        assert all(CANARY not in archive.read(name) for name in names)
        assert not any(name.endswith((".db", ".sqlite3")) for name in names)
    with tarfile.open(next(destination.glob("*.tar.gz"))) as archive:
        assert all(
            CANARY not in archive.extractfile(member).read()
            for member in archive
            if member.isfile()
        )


def test_installed_wheel_cli_needs_no_server_dependencies(artifacts, tmp_path, source):
    _, destination = artifacts
    environment = tmp_path / "installed"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    execute(
        [
            "uv",
            "pip",
            "install",
            "--no-cache",
            "--no-deps",
            "--python",
            str(environment / "bin/python"),
            str(next(destination.glob("*.whl"))),
        ],
        cwd=tmp_path,
    )
    cli = str(environment / "bin/zeeker-identity")
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "ZEEKER_REGISTRY_DIR"}}
    assert "Private metadata identity registry" in execute([cli, "--help"], cwd=tmp_path, env=env)
    rejected = subprocess.run([cli, "init"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert rejected.returncode == 2
    assert "explicit registry directory" in rejected.stderr
    root = tmp_path / "explicit-private"
    prefix = [cli, "--registry-dir", str(root)]
    assert json.loads(execute([*prefix, "init"], cwd=tmp_path, env=env))["created"]
    proposed = json.loads(
        execute(
            [
                cli,
                "source-manifest",
                "--source",
                str(source),
                "--source-id",
                "synthetic",
                "--generation",
                "1",
                "--corpus",
                "judgments",
                "--table",
                "judgments",
                "--columns",
                "id,citation",
            ],
            cwd=tmp_path,
            env=env,
        )
    )
    assert proposed["approval_reference"] is None
    proposed["approval_reference"] = "synthetic-only"
    contract = tmp_path / "synthetic-contract.json"
    contract.write_text(json.dumps(proposed))
    imported = json.loads(
        execute(
            [*prefix, "import", "--source", str(source), "--contract", str(contract)],
            cwd=tmp_path,
            env=env,
        )
    )
    execute([*prefix, "candidates", "--snapshot", imported["snapshot_id"]], cwd=tmp_path, env=env)
    report = json.loads(execute([*prefix, "report"], cwd=tmp_path, env=env))
    assert report["counts"]["legacy_row_assertion"] == 6
    assert report["bound_assertions"] == 0
    listed = json.loads(
        execute([*prefix, "claims", "--snapshot", imported["snapshot_id"]], cwd=tmp_path, env=env)
    )
    claim = next(c for c in listed if c["reason"] == "individual_review")["claim_id"]
    evidence = json.loads(execute([*prefix, "inspect", "--claim", claim], cwd=tmp_path, env=env))
    review = [
        *prefix,
        "review",
        "--claim",
        claim,
        "--reviewer",
        "synthetic-reviewer",
        "--basis",
        "synthetic fixture",
        "--claim-revision",
        str(evidence["revision"]),
        "--expected-bindings",
        json.dumps(evidence["expected_bindings"]),
    ]
    assert (
        json.loads(execute([*review, "--verdict", "accept"], cwd=tmp_path, env=env))[
            "changed_bindings"
        ]
        == 1
    )
    stale = subprocess.run(
        [*review, "--verdict", "accept"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert stale.returncode == 2 and "stale" in stale.stderr
    execute([*prefix, "backup", "--name", "after-synthetic-review"], cwd=tmp_path, env=env)
    restored = json.loads(
        execute(
            [
                *prefix,
                "restore-copy",
                "--backup",
                "after-synthetic-review",
                "--name",
                "separate-check",
            ],
            cwd=tmp_path,
            env=env,
        )
    )
    assert restored["promoted"] is False


@pytest.mark.skipif(
    os.environ.get("ZEEKER_DOCKER_CONTEXT") != "1",
    reason="opt-in local Docker context export; no service is started",
)
def test_native_docker_context_export_excludes_canaries(artifacts, tmp_path):
    project, _ = artifacts
    # A scratch-only export tests Docker's own ignore matcher without fetching
    # base images, executing RUN commands, creating containers, or publishing.
    (project / "Dockerfile").write_text("FROM scratch\nCOPY . /context\n")
    archive_path = tmp_path / "synthetic-context.tar"
    execute(
        ["docker", "build", "--network=none", "--output", f"type=tar,dest={archive_path}", "."],
        cwd=project,
    )
    with tarfile.open(archive_path) as archive:
        members = [member for member in archive if member.isfile()]
        assert any(member.name.endswith("src/zeeker_identity/cli.py") for member in members)
        assert all(CANARY not in archive.extractfile(member).read() for member in members)


def test_server_import_and_all_six_protocol_contracts_unchanged(tmp_path):
    forbidden = str(tmp_path / "must-not-be-opened")
    env = dict(os.environ, ZEEKER_REGISTRY_DIR=forbidden)
    code = """import asyncio,json,os,sys
root=os.environ['ZEEKER_REGISTRY_DIR']
def audit(event,args):
 if event=='open' and args and isinstance(args[0],str) and args[0].startswith(root):
  raise AssertionError('server accessed registry path')
 if event=='import' and args and str(args[0]).startswith('zeeker_identity'):
  raise AssertionError('server imported registry package')
sys.addaudithook(audit)
from mcp_zeeker.app import app
from mcp_zeeker.server import mcp
from fastmcp import Client
async def check():
 async with Client(mcp) as client:
  tools=await client.list_tools()
  snapshot=[t.model_dump(mode='json') for t in sorted(tools,key=lambda t:t.name)]
  print('CONTRACT='+json.dumps(snapshot,sort_keys=True))
asyncio.run(check())
assert not any(n.startswith('zeeker_identity') for n in sys.modules)
"""
    output = execute([sys.executable, "-c", code], cwd=tmp_path, env=env)
    actual = json.loads(
        next(
            line.removeprefix("CONTRACT=")
            for line in output.splitlines()
            if line.startswith("CONTRACT=")
        )
    )
    expected = json.loads((Path(__file__).parent / "fixtures/legacy-tools.json").read_text())
    assert actual == expected
    assert not Path(forbidden).exists()


def test_state_directory_is_explicit_owner_only_and_outside_git(tmp_path):
    with pytest.raises(RegistryError):
        initialize(None)
    with pytest.raises(RegistryError, match="repository"):
        initialize(ROOT / "accidental-registry")
    root = tmp_path / "private"
    initialize(root)
    assert root.stat().st_mode & 0o077 == 0
    assert (root / "registry.sqlite3").stat().st_mode & 0o077 == 0
    link = tmp_path / "linked"
    link.symlink_to(root)
    with pytest.raises(RegistryError, match="symbolic"):
        initialize(link)


def test_schema_tampering_rejected(registry):
    import sqlite3

    with sqlite3.connect(registry / "registry.sqlite3") as connection:
        connection.execute("DROP TRIGGER binding_change")
    with pytest.raises(RegistryError, match="schema objects"):
        with open_registry(registry):
            pytest.fail("must reject changed schema")
