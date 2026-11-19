"""Normal releases stay on the current CI-tested main SHA."""

import pytest

from scripts.select_recovery_source import select_source

REPOSITORY = "Skulldorom/flask-session.manager.sk"
HEAD = "e" * 40
OLD = "a" * 40


class Evidence:
    def __init__(self, drafts=None):
        self.drafts = [] if drafts is None else drafts

    def collection(self, endpoint):
        assert endpoint == f"repos/{REPOSITORY}/releases"
        return self.drafts


def draft(source):
    return {"id": 123, "draft": True, "target_commitish": source, "tag_name": "v1.3.3"}


def test_no_pending_draft_allows_current_main():
    assert select_source(Evidence(), REPOSITORY, HEAD) == HEAD


def test_matching_draft_allows_current_main_retry():
    assert select_source(Evidence([draft(HEAD)]), REPOSITORY, HEAD) == HEAD


def test_historical_draft_blocks_automatic_release():
    with pytest.raises(ValueError, match="historical draft"):
        select_source(Evidence([draft(OLD)]), REPOSITORY, HEAD)


def test_duplicate_pending_drafts_fail_closed():
    with pytest.raises(ValueError, match="multiple pending"):
        select_source(Evidence([draft(HEAD), draft(OLD)]), REPOSITORY, HEAD)


def test_bad_source_sha_fails_closed():
    with pytest.raises(ValueError, match="immutable"):
        select_source(Evidence([draft("not-a-sha")]), REPOSITORY, HEAD)


def test_invalid_main_sha_fails_closed():
    with pytest.raises(ValueError, match="authorized main"):
        select_source(Evidence(), REPOSITORY, "not-a-sha")
