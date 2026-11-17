"""Structural trust-boundary checks for the publishing workflow."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def job_section(workflow: str, name: str) -> str:
    jobs = workflow.split("jobs:\n", 1)[1]
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        jobs,
    )
    assert match, f"missing workflow job: {name}"
    return match.group(1)


def test_release_workflow_has_only_the_verified_ci_event_trigger():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    triggers = workflow.split("permissions:\n", 1)[0]

    assert "workflow_run:" in triggers
    assert "workflows: [CI]" in triggers
    assert "branches: [main]" in triggers
    assert "workflow_dispatch:" not in triggers
    assert "push:" not in triggers
    assert "skip-existing" not in workflow
    assert "git reset --hard origin/" not in workflow


def test_oidc_write_permission_is_scoped_to_the_pypi_job():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert workflow.count("id-token: write") == 1
    publish = job_section(workflow, "publish-pypi")
    assert "id-token: write" in publish
    assert "actions/checkout" not in publish
    assert "persist-credentials: false" not in publish
    for name in ("authorize", "prepare", "reserve-release", "finalize-release"):
        assert "id-token: write" not in job_section(workflow, name)


def test_actions_are_pinned_to_full_commit_shas():
    for workflow_path in (
        ROOT / ".github/workflows/ci.yml",
        ROOT / ".github/workflows/release.yml",
    ):
        for line in workflow_path.read_text().splitlines():
            if "uses:" in line:
                assert re.search(r"@[0-9a-f]{40}(?:\s|$)", line), line


def test_new_ci_gates_run_without_path_filters():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "pull_request:" in workflow
    assert "paths:" not in workflow
    for name in ("release-policy", "dependency-audit", "companion-compatibility"):
        assert job_section(workflow, name)
