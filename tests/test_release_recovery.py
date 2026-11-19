"""Pinned one-time recovery and real workflow gates, with no live mutations."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest

from scripts import release_recovery as recovery
from scripts.github_release import APIError
from tests.test_github_release import ContractAPI
from tests.test_release_workflow_integration import ROOT, install_fake_gh, workflow_step

HEAD = "e" * 40
# The main commit that introduced the recovery workflow contained only
# recovery-eligible changes from the original v1.3.3 source.
RECOVERY_ELIGIBLE_MAIN = "63f42ea6475ece56cd1f4406429a804629b1ac65"


def draft():
    return {
        "id": recovery.RELEASE_ID,
        "tag_name": "v1.3.3",
        "draft": True,
        "prerelease": False,
        "target_commitish": recovery.SOURCE,
        "body": f"Release-Source-SHA: {recovery.SOURCE}\nPackage-Version: 1.3.3\n",
    }


def ci_run(source, run_id):
    return {
        "id": run_id,
        "head_sha": source,
        "head_branch": "main",
        "repository": {"full_name": recovery.REPOSITORY},
        "head_repository": {"full_name": recovery.REPOSITORY},
        "workflow_id": 327914495,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }


def request_event(head=HEAD):
    inputs = {
        "repository": recovery.REPOSITORY,
        "version": "1.3.3",
        "source_sha": recovery.SOURCE,
        "release_id": str(recovery.RELEASE_ID),
        "main_sha": head,
        "confirmation": recovery.CONFIRM_PUBLICATION,
    }
    event = {
        "inputs": inputs,
        "ref": "main",
        "sender": {"login": "maintainer"},
        "repository": {"full_name": recovery.REPOSITORY, "default_branch": "main"},
    }
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": recovery.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": head,
        "GITHUB_WORKFLOW_SHA": head,
        "GITHUB_WORKFLOW_REF": (
            f"{recovery.REPOSITORY}/.github/workflows/release.yml@refs/heads/main"
        ),
        "GITHUB_ACTOR": "maintainer",
        "GITHUB_TRIGGERING_ACTOR": "maintainer",
    }
    return event, env


class RecoveryAPI(ContractAPI):
    def __init__(self, dist):
        super().__init__(dist)
        self.source = HEAD
        self.releases = [draft()]
        self.uploaded = {p.name: p.read_bytes() for p in dist.iterdir()}
        self.role = "admin"
        self.old_ci = ci_run(recovery.SOURCE, recovery.SOURCE_CI)
        self.main_ci = ci_run(HEAD, 100)
        self.missing_ci = False
        self.missing_original_ci = False

    def request(self, endpoint, **kwargs):
        if endpoint == "user":
            return {"login": "maintainer", "type": "User"}
        if "/collaborators/" in endpoint:
            return {
                "permission": "admin",
                "role_name": self.role,
                "user": {"login": endpoint.split("/collaborators/")[1].split("/")[0]},
            }
        if "/actions/workflows/" in endpoint:
            if "/runs?" in endpoint:
                return {"workflow_runs": [] if self.missing_ci else [self.main_ci]}
            return {"id": 327914495, "name": "CI", "path": ".github/workflows/ci.yml"}
        if "/actions/runs/" in endpoint:
            if self.missing_original_ci and endpoint.endswith(str(recovery.SOURCE_CI)):
                raise APIError(404)
            return copy.deepcopy(
                self.old_ci
                if endpoint.endswith(str(recovery.SOURCE_CI))
                else self.main_ci
            )
        return super().request(endpoint, **kwargs)


@pytest.fixture
def original_dist(tmp_path):
    archive = ROOT / "tests/fixtures/v1.3.3-artifacts.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == (
        "b466d765610b8e9c151cdae033df85c2937ce4dbc43902f4747a53857b30779f"
    )
    dist = tmp_path / "recovery-dist"
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == set(recovery.HASHES)
        bundle.extractall(dist)
    return dist


@pytest.fixture
def operator(original_dist, monkeypatch):
    api = RecoveryAPI(original_dist)
    state = {"value": "publish"}
    monkeypatch.setattr(
        recovery, "pypi_artifact_state", lambda manifest: state["value"]
    )
    args = SimpleNamespace(
        repository=recovery.REPOSITORY,
        version="1.3.3",
        source_sha=recovery.SOURCE,
        release_id=recovery.RELEASE_ID,
        main_sha=HEAD,
        dist=original_dist,
        confirm=None,
    )
    return api, args, state


def confirm(args, action):
    args.confirm = (
        f"{action} {recovery.REPOSITORY} v1.3.3 {recovery.SOURCE} {recovery.RELEASE_ID}"
    )


def test_one_time_reconcile_oidc_handoff_and_maintainer_finalize(operator):
    api, args, state = operator

    def diff(*args):
        return ["scripts/release_recovery.py"]

    for command in ("inspect", "validate", "plan"):
        result = recovery.execute_operator(command, args, api, diff=diff)
        assert result["tag"] == "absent" and result["pypi_state"] == "publish"
    assert not any(method in {"POST", "PATCH"} for method, _ in api.calls)
    confirm(args, "reconcile")
    recovery.execute_operator("reconcile", args, api, diff=diff)
    assert api.tag == recovery.SOURCE and api.releases[0]["draft"]
    event, env = request_event()
    assert recovery.authorize_workflow(api, event, env, diff=diff) == HEAD
    # Reservation repaired, publication is still pending; local finalize refuses.
    confirm(args, "finalize")
    with pytest.raises(ValueError, match="PyPI publication first"):
        recovery.execute_operator("finalize", args, api, diff=diff)
    state["value"] = "already-published"  # Simulated external OIDC publisher.
    api.interrupt = "publish"
    with pytest.raises(OSError):
        recovery.execute_operator("finalize", args, api, diff=diff)
    api.interrupt = None
    recovery.execute_operator("finalize", args, api, diff=diff)
    assert len(api.releases) == 1 and not api.releases[0]["draft"]
    assert set(api.uploaded) == set(recovery.HASHES)
    writes = [(m, p) for m, p in api.calls if m in {"POST", "PATCH"}]
    assert [m for m, _ in writes] == ["POST", "PATCH"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "other/repo"),
        ("version", "1.3.4"),
        ("source_sha", "b" * 40),
        ("release_id", 17),
        ("confirm", "yes"),
    ],
)
def test_wrong_operator_identity_or_confirmation_never_mutates(operator, field, value):
    api, args, _ = operator
    confirm(args, "reconcile")
    setattr(args, field, value)
    with pytest.raises(ValueError):
        recovery.execute_operator(
            "reconcile", args, api, diff=lambda *a: ["scripts/release_recovery.py"]
        )
    assert not any(m in {"POST", "PATCH"} for m, _ in api.calls)


@pytest.mark.parametrize(
    "problem",
    [
        "permission",
        "main",
        "old-ci",
        "old-ci-missing",
        "missing-ci",
        "draft-id",
        "duplicate",
        "asset",
        "partial",
        "application",
    ],
)
def test_contradictory_remote_state_never_mutates(operator, problem):
    api, args, _ = operator
    confirm(args, "reconcile")

    def diff(*args):
        return ["scripts/release_recovery.py"]

    if problem == "permission":
        api.role = "write"
    elif problem == "main":
        api.source = "b" * 40
    elif problem == "old-ci":
        api.old_ci["conclusion"] = "failure"
    elif problem == "old-ci-missing":
        api.missing_original_ci = True
    elif problem == "missing-ci":
        api.missing_ci = True
    elif problem == "draft-id":
        api.releases[0]["id"] = 17
    elif problem == "duplicate":
        api.releases.append({**draft(), "id": 18})
    elif problem == "asset":
        api.uploaded[next(iter(api.uploaded))] = b"conflict"
    elif problem == "partial":
        api.uploaded.pop(next(iter(api.uploaded)))
    else:

        def diff(*args):
            return ["pyproject.toml"]

    with pytest.raises(ValueError):
        recovery.execute_operator("reconcile", args, api, diff=diff)
    assert not any(m in {"POST", "PATCH"} for m, _ in api.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "1.3.4"),
        ("source_sha", "b" * 40),
        ("release_id", "17"),
        ("repository", "other/repo"),
        ("confirmation", "yes"),
    ],
)
def test_manual_dispatch_is_not_a_generic_publish_bypass(operator, field, value):
    api, _, _ = operator
    event, env = request_event()
    event["inputs"][field] = value
    with pytest.raises(ValueError, match="unauthorized"):
        recovery.authorize_workflow(api, event, env)


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_REF", "refs/tags/v1.3.3"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_WORKFLOW_SHA", "b" * 40),
        ("GITHUB_SHA", "b" * 40),
        ("GITHUB_TRIGGERING_ACTOR", "untrusted"),
    ],
)
def test_wrong_workflow_context_and_rerun_actor_are_rejected(operator, key, value):
    api, _, _ = operator
    event, env = request_event()
    env[key] = value
    if key == "GITHUB_TRIGGERING_ACTOR":
        original = api.request

        def request(endpoint, **kwargs):
            if "/untrusted/permission" in endpoint:
                return {
                    "permission": "read",
                    "role_name": "read",
                    "user": {"login": "untrusted"},
                }
            return original(endpoint, **kwargs)

        api.request = request
    with pytest.raises(ValueError):
        recovery.authorize_workflow(
            api, event, env, diff=lambda *a: ["scripts/release_recovery.py"]
        )


@pytest.mark.parametrize("ref", ["main", "refs/heads/main"])
def test_dispatch_ref_spelling_does_not_change_the_required_main_identity(
    operator, ref
):
    api, _, _ = operator
    event, env = request_event()
    event["ref"] = ref
    assert (
        recovery.authorize_workflow(
            api, event, env, diff=lambda *a: ["scripts/release_recovery.py"]
        )
        == HEAD
    )


def test_pinned_candidate_does_not_accept_a_self_consistent_rebuild(original_dist):
    wheel = next(original_dist.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="original build hash"):
        recovery.candidate(RecoveryAPI(original_dist), original_dist, HEAD)


def test_real_cli_and_yaml_handoff_use_original_bytes_without_github_writes(
    tmp_path, original_dist
):
    """Only GitHub and PyPI I/O are fixtures; run actual CLI and YAML commands."""
    workspace = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(ROOT), str(workspace)], check=True
    )
    remote = tmp_path / "remote"
    shutil.copytree(original_dist, remote)
    shutil.copytree(original_dist, workspace / "recovery-dist")
    (workspace / "recovery-dist/release-manifest.json").unlink()
    state_file = tmp_path / "state.json"
    state = {
        "main_sha": RECOVERY_ELIGIBLE_MAIN,
        "tag_sha": recovery.SOURCE,
        "release": draft(),
        "assets": {p.name: str(p) for p in remote.iterdir()},
        "create_count": 0,
        "upload_count": 0,
        "patch_fails": False,
    }
    state_file.write_text(json.dumps(state))
    bindir = install_fake_gh(tmp_path)
    # Inject only the external PyPI read. This adapter cannot publish packages.
    python = bindir / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, runpy, sys
module=sys.argv[2]; sys.argv=sys.argv[2:]
if module == 'scripts.release_recovery':
    from scripts import release_recovery as r
    from scripts.check_pypi_artifacts import pypi_artifact_state
    r.pypi_artifact_state=lambda manifest: pypi_artifact_state(
        manifest,json.load(open(os.environ['MOCK_PYPI'])),fetch=False)
    r.main()
else:
    runpy.run_module(module,run_name='__main__')
"""
    )
    python.chmod(0o755)
    event, context = request_event(RECOVERY_ELIGIBLE_MAIN)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    pypi = tmp_path / "pypi.json"
    pypi.write_text('{"urls": []}')
    env = {
        **os.environ,
        **context,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "PYTHONPATH": str(ROOT),
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "SOURCE_SHA": recovery.SOURCE,
        "MOCK_GH_STATE": str(state_file),
        "MOCK_PYPI": str(pypi),
    }

    def step(name):
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", workflow_step("recover-v1-3-3", name)],
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
        )

    for name in (
        "Authorize fixed historical publication",
        "Validate original source metadata",
        "Recheck authorization reservation and original hashes",
        "Stage only verified packages",
    ):
        result = step(name)
        assert result.returncode == 0, result.stderr
    assert "state=publish" in (tmp_path / "output").read_text()
    premature = step("Verify publication and report maintainer finalization")
    assert premature.returncode != 0
    manifest = json.loads((remote / "release-manifest.json").read_text())
    pypi.write_text(
        json.dumps(
            {
                "urls": [
                    {"filename": n, "digests": {"sha256": h}}
                    for n, h in manifest["artifacts"].items()
                ]
            }
        )
    )
    result = step("Recheck authorization reservation and original hashes")
    assert result.returncode == 0, result.stderr
    assert "state=already-published" in (tmp_path / "output").read_text()
    result = step("Verify publication and report maintainer finalization")
    assert result.returncode == 0, result.stderr
    assert "finalize release 448693106 locally" in result.stdout
    final = json.loads(state_file.read_text())
    assert final["tag_sha"] == recovery.SOURCE and final["release"]["draft"]
    assert final["upload_count"] == 0 and final["create_count"] == 0
