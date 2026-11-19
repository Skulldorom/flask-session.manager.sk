"""Executable release-script and GitHub CLI flow tests with local fixtures."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_release_artifacts import SOURCE_SHA, VERSION, artifact_pair

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Skulldorom/flask-session.manager.sk"


def command(module, *args, env=None):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)
    if env:
        environment.update({key: str(value) for key, value in env.items()})
    return subprocess.run(
        [sys.executable, "-m", module, *map(str, args)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def write_json(path: Path, value) -> Path:
    path.write_text(json.dumps(value))
    return path


def workflow_step(job: str, name: str) -> str:
    workflow = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    return next(
        step["run"]
        for step in workflow["jobs"][job]["steps"]
        if step.get("name") == name
    )


def release_fixture(tmp_path: Path) -> tuple[Path, dict]:
    dist = tmp_path / "dist"
    dist.mkdir()
    artifact_pair(dist)
    manifest_path = dist / "release-manifest.json"
    validated = command(
        "scripts.validate_release_artifacts",
        "--dist",
        dist,
        "--source-sha",
        SOURCE_SHA,
        "--version",
        VERSION,
        "--manifest",
        manifest_path,
    )
    assert validated.returncode == 0, validated.stderr
    manifest = json.loads(manifest_path.read_text())
    return dist, manifest


def install_fake_gh(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    executable = bindir / "gh"
    shutil.copy2(ROOT / "tests/fake_github_cli.py", executable)
    executable.chmod(0o755)
    return bindir


def run_workflow_step(
    script: str,
    tmp_path: Path,
    *,
    partial_create: bool = False,
    hide_created_draft: bool = False,
    release_id: str = "",
    state: dict | None = None,
) -> tuple[subprocess.CompletedProcess, dict]:
    state_path = tmp_path / "gh-state.json"
    if state is not None:
        write_json(state_path, state)
    bindir = install_fake_gh(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "PYTHONPATH": str(ROOT),
            "GITHUB_REPOSITORY": REPOSITORY,
            "GH_TOKEN": "test-only-read-write-token",
            "GITHUB_OUTPUT": str(tmp_path / "gh-output"),
            "RELEASE_ID": release_id,
            "MOCK_GH_HIDE_CREATED_DRAFT": str(int(hide_created_draft)),
            "SOURCE_SHA": SOURCE_SHA,
            "VERSION": VERSION,
            "MOCK_GH_STATE": str(state_path),
            "MOCK_GH_ASSET_STORE": str(tmp_path / "remote-assets"),
            "MOCK_GH_PARTIAL_CREATE": "1" if partial_create else "0",
        }
    )
    result = subprocess.run(
        ["bash", "-e", "-u", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    saved = json.loads(state_path.read_text()) if state_path.exists() else {}
    return result, saved


def test_authorization_cli_accepts_exact_ci_sha_and_rejects_advanced_main(tmp_path):
    event = {
        "repository": {"full_name": REPOSITORY, "default_branch": "main"},
        "workflow_run": {"id": 99, "head_sha": SOURCE_SHA},
    }
    run = {
        "id": 99,
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
        "head_branch": "main",
        "head_sha": SOURCE_SHA,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "workflow_id": 42,
    }
    workflow = {"id": 42, "name": "CI", "path": ".github/workflows/ci.yml"}
    event_file = write_json(tmp_path / "event.json", event)
    run_file = write_json(tmp_path / "run.json", run)
    workflow_file = write_json(tmp_path / "workflow.json", workflow)
    repository_file = write_json(
        tmp_path / "repository.json", {"default_branch": "main"}
    )
    ref_file = write_json(tmp_path / "ref.json", {"object": {"sha": SOURCE_SHA}})
    inputs = [
        "--event",
        event_file,
        "--run",
        run_file,
        "--workflow",
        workflow_file,
        "--default-ref",
        ref_file,
        "--repository",
        repository_file,
    ]
    env = {
        "GITHUB_EVENT_NAME": "workflow_run",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
    }

    accepted = command("scripts.authorize_release_event", *inputs, env=env)
    assert accepted.returncode == 0, accepted.stderr
    assert f"source_sha={SOURCE_SHA}" in (tmp_path / "output.txt").read_text()

    write_json(ref_file, {"object": {"sha": "b" * 40}})
    rejected = command("scripts.authorize_release_event", *inputs, env=env)
    assert rejected.returncode != 0
    assert "stale" in rejected.stderr


def test_release_selection_cli_covers_labels_conflicts_ambiguity_and_retry(tmp_path):
    sha = SOURCE_SHA
    merged_pr = {
        "merged_at": "2026-11-17T10:00:00Z",
        "merge_commit_sha": sha,
        "base": {"ref": "main"},
        "labels": [{"name": "version:minor"}],
    }
    pypi = write_json(tmp_path / "pypi.json", {"info": {"version": "1.3.2"}})
    prs = write_json(tmp_path / "prs.json", [merged_pr])
    releases = write_json(tmp_path / "releases.json", [])
    tags = tmp_path / "tag-refs.txt"
    tags.touch()
    commit = tmp_path / "message.txt"
    commit.write_text("feat: release capability\n")
    output = tmp_path / "selection.txt"
    common = [
        "--source-sha",
        sha,
        "--pypi-json",
        pypi,
        "--branch",
        "main",
        "--pull-requests",
        prs,
        "--releases",
        releases,
        "--tag-refs",
        tags,
        "--commit-message-file",
        commit,
        "--output",
        output,
    ]

    selected = command("scripts.select_release", *common)
    assert selected.returncode == 0, selected.stderr
    assert "version=1.4.0" in output.read_text()
    assert "part=minor" in output.read_text()

    write_json(prs, [merged_pr, merged_pr])
    ambiguous = command("scripts.select_release", *common)
    assert ambiguous.returncode != 0
    assert "multiple" in ambiguous.stderr

    write_json(prs, [merged_pr])
    source_url = f"https://github.com/{REPOSITORY}/commit/{sha}"
    write_json(
        pypi,
        {"info": {"version": "1.3.3", "project_urls": {"Release Source": source_url}}},
    )
    write_json(releases, [{"tag_name": "v1.3.4", "body": f"Release-Source-SHA: {sha}"}])
    conflict = command("scripts.select_release", *common)
    assert conflict.returncode != 0
    assert "conflicts" in conflict.stderr

    write_json(pypi, {"info": {"version": "1.3.2"}})
    write_json(releases, [{"tag_name": "v1.3.3", "body": f"Release-Source-SHA: {sha}"}])
    retry = command("scripts.select_release", *common)
    assert retry.returncode == 0, retry.stderr
    assert "version=1.3.3" in output.read_text()
    assert "part=reserved-release" in output.read_text()


@pytest.mark.parametrize(
    "draft, tag_target, latest, expected",
    [
        (False, None, "1.3.2", "1.3.3"),
        (True, "old", "1.3.2", "1.3.4"),
        (True, None, "1.3.2", "1.3.4"),
        (False, "old", "1.3.2", "1.3.4"),
        (False, "annotated-old", "1.3.2", "1.3.4"),
        (False, "current", "1.3.2", "1.3.3"),
        (False, "annotated-current", "1.3.2", "1.3.3"),
        (True, "old", "1.3.3", "1.3.4"),
    ],
)
def test_workflow_selects_current_release_around_abandoned_versions(
    tmp_path, draft, tag_target, latest, expected
):
    old = "b" * 40
    write_json(tmp_path / "pypi.json", {"info": {"version": latest}})
    write_json(tmp_path / "associated-pulls.json", [])
    write_json(
        tmp_path / "releases.json",
        [
            {
                "tag_name": "v1.3.3",
                "target_commitish": old,
                "draft": True,
                "body": f"Release-Source-SHA: {old}\nPackage-Version: 1.3.3",
            }
        ]
        if draft
        else [],
    )
    tags = ""
    if tag_target:
        target = SOURCE_SHA if tag_target.endswith("current") else old
        if tag_target.startswith("annotated"):
            tags = f"{'d' * 40}\trefs/tags/v1.3.3\n{target}\trefs/tags/v1.3.3^{{}}\n"
        else:
            tags = f"{target}\trefs/tags/v1.3.3\n"
    (tmp_path / "tag-refs.txt").write_text(tags)
    (tmp_path / "commit-message.txt").write_text("ordinary change")
    (tmp_path / "gh-output").touch()
    choose = workflow_step(
        "prepare", "Select release version from exact PR association"
    )
    result, _ = run_workflow_step(f"export DEFAULT_BRANCH=main\n{choose}", tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gh-output").read_text().splitlines() == [
        f"version={expected}",
        "part=patch",
        "skip=false",
    ]


def test_pypi_artifact_cli_simulates_publish_retry_failure_and_conflict(tmp_path):
    dist, manifest = release_fixture(tmp_path)
    manifest_file = dist / "release-manifest.json"
    empty_pypi = write_json(tmp_path / "empty-pypi.json", {"urls": []})
    output = tmp_path / "pypi-state.txt"
    args = ["--manifest", manifest_file, "--pypi-json", empty_pypi, "--output", output]

    publish = command("scripts.check_pypi_artifacts", *args)
    assert publish.returncode == 0, publish.stderr
    assert "state=publish" in output.read_text()

    matching_urls = [
        {"filename": name, "digests": {"sha256": digest}}
        for name, digest in manifest["artifacts"].items()
    ]
    matching = write_json(tmp_path / "matching-pypi.json", {"urls": matching_urls})
    retry_args = [
        "--manifest",
        manifest_file,
        "--pypi-json",
        matching,
        "--output",
        output,
    ]
    already = command("scripts.check_pypi_artifacts", *retry_args)
    assert already.returncode == 0, already.stderr
    assert "state=already-published" in output.read_text()

    partial = write_json(tmp_path / "partial-pypi.json", {"urls": matching_urls[:1]})
    failed = command(
        "scripts.check_pypi_artifacts",
        "--manifest",
        manifest_file,
        "--pypi-json",
        partial,
        "--output",
        output,
    )
    assert failed.returncode != 0
    assert "partial or conflicting" in failed.stderr

    conflicting_urls = [dict(item) for item in matching_urls]
    conflicting_urls[0]["digests"] = {"sha256": "0" * 64}
    conflicting = write_json(
        tmp_path / "conflicting-pypi.json", {"urls": conflicting_urls}
    )
    rejected = command(
        "scripts.check_pypi_artifacts",
        "--manifest",
        manifest_file,
        "--pypi-json",
        conflicting,
        "--output",
        output,
    )
    assert rejected.returncode != 0

    simulated_success = command(
        "scripts.check_finalize_policy",
        "--artifact-state",
        "publish",
        "--publish-result",
        "success",
        "--reservation-verified",
    )
    assert simulated_success.returncode == 0
    simulated_failure = command(
        "scripts.check_finalize_policy",
        "--artifact-state",
        "publish",
        "--publish-result",
        "failure",
        "--reservation-verified",
    )
    assert simulated_failure.returncode != 0
    already_published = command(
        "scripts.check_finalize_policy",
        "--artifact-state",
        "already-published",
        "--publish-result",
        "skipped",
        "--reservation-verified",
    )
    assert already_published.returncode == 0


def test_release_assets_are_verified_by_executable_cli(tmp_path):
    dist, manifest = release_fixture(tmp_path)
    expected = write_json(tmp_path / "expected.json", manifest)
    release_dir = tmp_path / "release-assets"
    shutil.copytree(
        dist, release_dir, ignore=shutil.ignore_patterns("release-manifest.json")
    )
    shutil.copy2(dist / "release-manifest.json", release_dir / "release-manifest.json")
    args = [
        "--expected-manifest",
        expected,
        "--release-dir",
        release_dir,
        "--source-sha",
        SOURCE_SHA,
        "--version",
        VERSION,
    ]

    verified = command("scripts.verify_release_assets", *args)
    assert verified.returncode == 0, verified.stderr

    wheel = next(release_dir.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    rejected = command("scripts.verify_release_assets", *args)
    assert rejected.returncode != 0


def test_draft_release_creation_and_duplicate_retry_are_idempotent(tmp_path):
    release_fixture(tmp_path)
    script = workflow_step(
        "reserve-release", "Create or verify the draft release reservation"
    )

    created, state = run_workflow_step(script, tmp_path)
    assert created.returncode == 0, created.stderr
    assert state["create_count"] == 1
    assert state["release"]["draft"] is True
    assert len(state["assets"]) == 3
    assert (
        "repos/Skulldorom/flask-session.manager.sk/git/ref/tags/v1.3.3"
        in state["api_endpoints"]
    )
    assert not any("/commits/v1.3.3" in endpoint for endpoint in state["api_endpoints"])

    duplicate, retried_state = run_workflow_step(script, tmp_path)
    assert duplicate.returncode == 0, duplicate.stderr
    assert retried_state["create_count"] == 1
    assert retried_state["upload_count"] == 3
    assert len(retried_state["assets"]) == 3


def test_created_draft_is_verified_by_id_when_missing_from_collection(tmp_path):
    release_fixture(tmp_path)
    reserve = workflow_step(
        "reserve-release", "Create or verify the draft release reservation"
    )
    result, state = run_workflow_step(reserve, tmp_path, hide_created_draft=True)
    assert result.returncode == 0, result.stderr
    assert state["tag_sha"] == SOURCE_SHA
    assert state["create_count"] == 1
    assert state["release"]["draft"] is True
    assert len(state["assets"]) == 3
    assert state["hide_release_from_list"]
    assert (
        sum("/releases?per_page=" in endpoint for endpoint in state["api_endpoints"])
        == 1
    )

    key, release_id = (tmp_path / "gh-output").read_text().strip().split("=")
    assert key == "release_id" and release_id == str(state["release"]["id"])
    finalize = workflow_step(
        "finalize-release", "Verify draft assets and publish the release"
    )
    result, published = run_workflow_step(finalize, tmp_path, release_id=release_id)
    assert result.returncode == 0, result.stderr
    assert published["release"]["draft"] is False
    assert (
        sum(
            "/releases?per_page=" in endpoint for endpoint in published["api_endpoints"]
        )
        == 1
    )


def test_partial_draft_creation_is_repaired_without_clobber_on_retry(tmp_path):
    release_fixture(tmp_path)
    script = workflow_step(
        "reserve-release", "Create or verify the draft release reservation"
    )

    interrupted, partial_state = run_workflow_step(
        script, tmp_path, partial_create=True
    )
    assert interrupted.returncode != 0
    assert partial_state["create_count"] == 1
    assert len(partial_state["assets"]) == 1

    resumed, complete_state = run_workflow_step(script, tmp_path)
    assert resumed.returncode == 0, resumed.stderr
    assert complete_state["create_count"] == 1
    assert complete_state["upload_count"] == 3
    assert len(complete_state["assets"]) == 3


def test_conflicting_draft_asset_is_never_overwritten(tmp_path):
    dist, manifest = release_fixture(tmp_path)
    asset_name = next(iter(manifest["artifacts"]))
    source = dist / asset_name
    tampered = tmp_path / "remote-assets" / asset_name
    tampered.parent.mkdir()
    tampered.write_bytes(source.read_bytes() + b"wrong")
    state = {
        "tag_sha": SOURCE_SHA,
        "tag_object_type": "tag",
        "release": {
            "id": 17,
            "tag_name": f"v{VERSION}",
            "target_commitish": SOURCE_SHA,
            "prerelease": False,
            "body": f"Release-Source-SHA: {SOURCE_SHA}\nPackage-Version: {VERSION}",
            "draft": True,
        },
        "assets": {asset_name: str(tampered)},
        "create_count": 1,
        "upload_count": 0,
        "patch_fails": False,
    }
    script = workflow_step(
        "reserve-release", "Create or verify the draft release reservation"
    )

    rejected, final_state = run_workflow_step(script, tmp_path, state=state)
    assert rejected.returncode != 0
    assert "conflicts" in rejected.stderr
    assert final_state["upload_count"] == 0


@pytest.mark.parametrize("status", [401, 403, 422, 429, 500, 502, 503])
def test_github_lookup_server_error_is_not_treated_as_missing_tag(tmp_path, status):
    release_fixture(tmp_path)
    state = {
        "tag_sha": None,
        "tag_lookup_status": status,
        "release": None,
        "assets": {},
        "create_count": 0,
        "upload_count": 0,
        "patch_fails": False,
    }
    script = workflow_step(
        "reserve-release", "Create or verify the draft release reservation"
    )

    failed, final_state = run_workflow_step(script, tmp_path, state=state)
    assert failed.returncode != 0
    assert f"HTTP {status}" in failed.stderr
    assert final_state["create_count"] == 0


def test_github_release_finalization_failure_leaves_draft_unpublished(tmp_path):
    dist, manifest = release_fixture(tmp_path)
    asset_store = tmp_path / "remote-assets"
    asset_store.mkdir()
    assets = {}
    for name in [*manifest["artifacts"], "release-manifest.json"]:
        shutil.copy2(dist / name, asset_store / name)
        assets[name] = str(asset_store / name)
    state = {
        "tag_sha": SOURCE_SHA,
        "tag_object_type": "commit",
        "release": {
            "id": 17,
            "tag_name": f"v{VERSION}",
            "target_commitish": SOURCE_SHA,
            "prerelease": False,
            "body": f"Release-Source-SHA: {SOURCE_SHA}\nPackage-Version: {VERSION}",
            "draft": True,
        },
        "assets": assets,
        "create_count": 1,
        "upload_count": 0,
        "patch_fails": True,
    }
    script = workflow_step(
        "finalize-release", "Verify draft assets and publish the release"
    )

    failed, final_state = run_workflow_step(
        script, tmp_path, state=state, release_id="17"
    )
    assert failed.returncode != 0
    assert "HTTP 500" in failed.stderr
    assert final_state["release"]["draft"] is True

    final_state["patch_fails"] = False
    resumed, published = run_workflow_step(
        script, tmp_path, state=final_state, release_id="17"
    )
    assert resumed.returncode == 0, resumed.stderr
    assert published["release"]["draft"] is False
    assert published["upload_count"] == 0
