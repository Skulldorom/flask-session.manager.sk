"""Recovery requires both current-main CI and exact-source historical CI."""

import copy

import pytest

from scripts.select_recovery_source import (
    RECOVERY_PATHS,
    git_diff,
    select_source,
    verify_source_ci,
)
from tests.test_release_workflow_integration import REPOSITORY, SOURCE_SHA

HEAD = "e" * 40


class Evidence:
    def __init__(self):
        self.drafts = [
            {
                "id": 448693106,
                "draft": True,
                "target_commitish": SOURCE_SHA,
                "tag_name": "v1.3.3",
                "body": f"Release-Source-SHA: {SOURCE_SHA}\nPackage-Version: 1.3.3",
            }
        ]
        self.workflow = {"id": 42, "name": "CI", "path": ".github/workflows/ci.yml"}
        self.run = {
            "id": 99,
            "head_sha": SOURCE_SHA,
            "head_branch": "main",
            "repository": {"full_name": REPOSITORY},
            "head_repository": {"full_name": REPOSITORY},
            "workflow_id": 42,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }

    def collection(self, endpoint):
        return copy.deepcopy(self.drafts)

    def request(self, endpoint):
        if "/runs?" in endpoint:
            return {"workflow_runs": [copy.deepcopy(self.run)]}
        if "/actions/runs/" in endpoint:
            return copy.deepcopy(self.run)
        return copy.deepcopy(self.workflow)


def test_only_release_infrastructure_changes_allow_same_version_recovery():
    evidence = Evidence()
    # Historical publication is no longer automatically selected, even if every
    # path is release infrastructure. Its exact-source CI check remains shared.
    with pytest.raises(ValueError, match="one-time recovery"):
        select_source(
            evidence, REPOSITORY, HEAD, git_diff=lambda *a: list(RECOVERY_PATHS)
        )
    assert verify_source_ci(evidence, REPOSITORY, SOURCE_SHA).source_sha == SOURCE_SHA


@pytest.mark.parametrize(
    "paths",
    [
        ["pyproject.toml"],
        ["uv.lock"],
        ["flask_session_manager_sk/__init__.py"],
        ["scripts/evil.py"],
        [],
    ],
)
def test_recovery_blocks_application_build_dependencies_and_unknown_changes(paths):
    with pytest.raises(ValueError, match="one-time recovery"):
        select_source(Evidence(), REPOSITORY, HEAD, git_diff=lambda *a: paths)


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("conclusion", "failure"),
        ("head_sha", HEAD),
        ("head_branch", "feature"),
        ("workflow_id", 43),
    ],
)
def test_recovery_never_substitutes_current_ci_for_original_source_ci(field, value):
    evidence = Evidence()
    evidence.run[field] = value
    with pytest.raises(ValueError):
        verify_source_ci(evidence, REPOSITORY, SOURCE_SHA)


def test_duplicate_pending_releases_are_not_ignored():
    evidence = Evidence()
    evidence.drafts *= 2
    with pytest.raises(ValueError, match="ambiguous"):
        select_source(evidence, REPOSITORY, HEAD, git_diff=lambda *a: [])


def test_fresh_source_with_no_draft_is_current_authorized_main():
    evidence = Evidence()
    evidence.drafts = []
    assert select_source(evidence, REPOSITORY, HEAD, git_diff=lambda *a: []) == HEAD


def test_git_evidence_rejects_nonancestor_and_reports_actual_changed_files(
    tmp_path, monkeypatch
):
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path).decode().strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    (tmp_path / "file").write_text("a")
    git("add", ".")
    git("commit", "-qm", "a")
    source = git("rev-parse", "HEAD")
    (tmp_path / "file").write_text("b")
    git("commit", "-qam", "b")
    head = git("rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    assert git_diff(source, head) == ["file"]
    with pytest.raises(subprocess.CalledProcessError):
        git_diff(head, source)
