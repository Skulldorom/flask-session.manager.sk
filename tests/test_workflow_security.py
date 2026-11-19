"""Structural trust-boundary checks for the GitHub Actions workflows."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "${{ github.token }}"
AUTHENTICATED_GH = re.compile(r"\bgh\s+(?:api|release|run|workflow|pr|repo|issue)\b")


def load_workflow(path: str) -> dict:
    # BaseLoader preserves the `on` key as a string (YAML 1.1 treats it as bool).
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def assert_authenticated_github_steps(workflow: dict) -> None:
    for job_id, job in workflow["jobs"].items():
        job_env = job.get("env", {})
        for index, step in enumerate(job.get("steps", [])):
            run = step.get("run", "")
            if not (AUTHENTICATED_GH.search(run) or "scripts.github_release" in run):
                continue
            step_env = {**job_env, **step.get("env", {})}
            expected = TOKEN
            assert step_env.get("GH_TOKEN") == expected, (
                f"{job_id} step {index} invokes authenticated GitHub CLI "
                "without the read-scoped Actions token"
            )
            assert "secrets." not in step_env["GH_TOKEN"]
            assert not re.search(r"(?:echo|printf).*GH_TOKEN|set\s+-x", run)


def test_release_workflow_has_only_the_verified_ci_event_trigger():
    workflow = load_workflow(".github/workflows/release.yml")
    triggers = workflow["on"]

    assert set(triggers) == {"workflow_run"}
    assert workflow["jobs"]["authorize"]["if"] == "github.event_name == 'workflow_run'"
    assert triggers["workflow_run"]["workflows"] == ["CI"]
    assert triggers["workflow_run"]["branches"] == ["main"]
    assert "skip-existing" not in (ROOT / ".github/workflows/release.yml").read_text()
    assert (
        "git reset --hard origin/"
        not in (ROOT / ".github/workflows/release.yml").read_text()
    )


def test_every_authenticated_github_cli_step_has_token_access():
    assert_authenticated_github_steps(load_workflow(".github/workflows/release.yml"))


def test_missing_token_on_required_step_is_detected():
    workflow = load_workflow(".github/workflows/release.yml")
    step = next(
        step
        for step in workflow["jobs"]["publish-pypi"]["steps"]
        if AUTHENTICATED_GH.search(step.get("run", ""))
    )
    step["env"].pop("GH_TOKEN")

    with pytest.raises(AssertionError, match="without the read-scoped"):
        assert_authenticated_github_steps(workflow)


def test_only_pypi_publisher_has_oidc_and_pypi_upload_capability():
    workflow = load_workflow(".github/workflows/release.yml")
    jobs = workflow["jobs"]
    publish = jobs["publish-pypi"]

    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    for job_id, job in jobs.items():
        if job_id != "publish-pypi":
            assert job.get("permissions", {}).get("id-token") != "write"
            assert all(
                "pypa/gh-action-pypi-publish" not in step.get("uses", "")
                for step in job.get("steps", [])
            )

    publishers = [
        job_id
        for job_id, job in jobs.items()
        for step in job.get("steps", [])
        if "pypa/gh-action-pypi-publish" in step.get("uses", "")
    ]
    assert publishers == ["publish-pypi"]
    assert "environment" not in publish
    for job_id, job in jobs.items():
        if job_id == "publish-pypi":
            continue
        serialized = str(job).lower()
        assert "environment" not in job
        assert "pypi_api_token" not in serialized
        assert "twine_password" not in serialized


def test_release_jobs_have_explicit_permissions_and_correct_dag():
    workflow = load_workflow(".github/workflows/release.yml")
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["authorize"]["permissions"] == {
        "actions": "read",
        "contents": "read",
    }
    assert jobs["prepare"]["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert jobs["publish-pypi"]["permissions"]["contents"] == "read"
    assert jobs["reserve-release"]["permissions"] == {"contents": "write"}
    assert jobs["finalize-release"]["permissions"] == {"contents": "write"}
    assert jobs["reserve-release"]["needs"] == ["authorize", "prepare"]
    assert jobs["publish-pypi"]["needs"] == [
        "authorize",
        "prepare",
        "reserve-release",
    ]
    assert jobs["finalize-release"]["needs"] == [
        "authorize",
        "prepare",
        "reserve-release",
        "publish-pypi",
    ]
    assert "needs.prepare.outputs.skip != 'true'" in jobs["publish-pypi"]["if"]
    assert "needs.reserve-release.result == 'success'" in jobs["finalize-release"]["if"]


def test_actions_are_pinned_to_full_commit_shas():
    for workflow_path in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        workflow = load_workflow(workflow_path)
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses:
                    assert re.search(r"@[0-9a-f]{40}(?:\s|$)", uses), uses


def test_expanded_ci_gates_run_on_pull_requests_without_path_filters():
    workflow = load_workflow(".github/workflows/ci.yml")
    assert "pull_request" in workflow["on"]
    assert "paths" not in str(workflow["on"])
    for job_id in ("release-policy", "dependency-audit", "companion-compatibility"):
        assert job_id in workflow["jobs"]
    # VCS version inference and builds use the full tag history.
    for job_id in ("test", "release-policy"):
        assert workflow["jobs"][job_id]["steps"][0]["with"]["fetch-depth"] == "0"


def test_abandoned_release_recovery_cannot_be_manually_dispatched():
    workflow = load_workflow(".github/workflows/release.yml")
    assert set(workflow["on"]) == {"workflow_run"}
    assert "recover-v1-3-3" not in workflow["jobs"]
    raw = (ROOT / ".github/workflows/release.yml").read_text()
    assert "workflow_dispatch" not in raw
    assert "scripts.release_recovery" not in raw
    assert "create-github-app-token" not in raw
    assert "secrets." not in raw
