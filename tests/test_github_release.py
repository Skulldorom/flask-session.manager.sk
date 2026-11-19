"""Execute the real state machine against a REST contract, without publication."""

import copy
import hashlib
import subprocess
from types import SimpleNamespace

import pytest

from scripts.github_release import APIError, GitHub, Release
from tests.test_release_workflow_integration import (
    REPOSITORY,
    SOURCE_SHA,
    VERSION,
    release_fixture,
)


class ContractAPI:
    def __init__(self, dist):
        self.source = SOURCE_SHA
        self.dist = dist
        self.tag = None
        self.annotated = False
        self.releases = []
        self.uploaded = {}
        self.calls = []
        self.interrupt = None
        self.tag_race = None
        self.draft_race = False

    def draft(self):
        r = {
            "id": 17,
            "tag_name": f"v{VERSION}",
            "target_commitish": SOURCE_SHA,
            "draft": True,
            "prerelease": False,
            "body": f"Release-Source-SHA: {SOURCE_SHA}\nPackage-Version: {VERSION}\n",
        }
        self.releases.append(r)
        return r

    def collection(self, endpoint):
        self.calls.append(("LIST", endpoint))
        if endpoint.endswith("/releases"):
            return copy.deepcopy(self.releases)
        assert endpoint.endswith("/assets")
        return [
            {
                "id": i,
                "name": name,
                "size": len(content),
                "state": "uploaded",
                "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            }
            for i, (name, content) in enumerate(self.uploaded.items(), 1)
        ]

    def request(self, endpoint, *, method="GET", payload=None, file=None, raw=False):
        self.calls.append((method, endpoint))
        if "/git/ref/heads/" in endpoint:
            return {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": self.source},
            }
        if "/git/ref/tags/" in endpoint:
            if self.tag is None:
                raise APIError(404)
            return {
                "ref": f"refs/tags/v{VERSION}",
                "object": {
                    "type": "tag" if self.annotated else "commit",
                    "sha": "d" * 40 if self.annotated else self.tag,
                },
            }
        if "/git/tags/" in endpoint:
            return {"sha": "d" * 40, "object": {"type": "commit", "sha": self.tag}}
        if endpoint.endswith("/git/refs"):
            assert method == "POST" and self.tag is None
            self.tag = self.tag_race or payload["sha"]
            if self.tag_race:
                raise APIError(422)
            return {"ref": payload["ref"]}
        if endpoint.endswith("/releases/generate-notes"):
            return {"body": "Notes"}
        if method == "POST" and endpoint.endswith("/releases"):
            if self.draft_race:
                self.draft()
                raise APIError(422)
            self.releases.append({**payload, "id": 17})
            # Crucial contract: draft create never assigns self.tag.
            if self.interrupt == "draft":
                raise OSError("cancelled after draft creation")
            return self.releases[-1]
        if "/releases/assets/" in endpoint:
            assert raw
            return list(self.uploaded.values())[int(endpoint.rsplit("/", 1)[1]) - 1]
        if method == "POST" and "/assets?name=" in endpoint:
            name = file.name
            assert name not in self.uploaded  # Never overwrite an asset.
            self.uploaded[name] = file.read_bytes()
            if self.interrupt == "upload":
                raise OSError("cancelled after upload")
            return {"id": len(self.uploaded)}
        r = next(r for r in self.releases if str(r["id"]) == endpoint.rsplit("/", 1)[1])
        if method == "PATCH":
            r["draft"] = payload["draft"]
            if self.interrupt == "publish":
                raise OSError("publication succeeded; response lost")
        return copy.deepcopy(r)


@pytest.fixture
def machine(tmp_path):
    dist, _ = release_fixture(tmp_path)
    api = ContractAPI(dist)
    return Release(api, REPOSITORY, SOURCE_SHA, VERSION, dist), api


@pytest.mark.parametrize("tag", [None, SOURCE_SHA])
@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize("asset_count", [0, 1, 3])
def test_every_reservation_state_recovers_and_finalizes(
    machine, tag, draft, asset_count
):
    release, api = machine
    api.tag = tag
    if draft:
        api.draft()
        api.uploaded = {
            p.name: p.read_bytes() for p in sorted(api.dist.iterdir())[:asset_count]
        }
    release.run()
    assert api.tag == SOURCE_SHA
    assert len(api.releases) == 1 and api.releases[0]["draft"]
    assert set(api.uploaded) == set(release.expected)
    before = copy.deepcopy(api.uploaded)
    release.run()  # duplicate event, same version and immutable bytes
    assert before == api.uploaded and len(api.releases) == 1
    release.run(finalize=True)
    assert not api.releases[0]["draft"]
    release.run(finalize=True)


@pytest.mark.parametrize("point", ["draft", "upload", "publish"])
def test_uncertain_success_is_reconciled_by_next_invocation(machine, point):
    release, api = machine
    if point == "publish":
        release.run()
    api.interrupt = point
    with pytest.raises(OSError):
        release.run(finalize=point == "publish")
    api.interrupt = None
    release.run(finalize=point == "publish")
    assert len(api.releases) == 1
    assert set(api.uploaded) == set(release.expected)


def test_annotated_tag(machine):
    release, api = machine
    api.tag, api.annotated = SOURCE_SHA, True
    release.run()


@pytest.mark.parametrize(
    "conflict", ["tag", "target", "metadata", "duplicate", "asset", "manifest", "main"]
)
def test_contradictions_block_before_any_mutation(machine, conflict):
    release, api = machine
    draft = api.draft()
    if conflict == "tag":
        api.tag = "b" * 40
    elif conflict == "target":
        draft["target_commitish"] = "b" * 40
    elif conflict == "metadata":
        draft["body"] += "Release-Source-SHA: " + "b" * 40
    elif conflict == "duplicate":
        api.releases.append({**draft, "id": 18})
    elif conflict == "asset":
        api.uploaded[next(iter(release.manifest["artifacts"]))] = b"wrong"
    elif conflict == "manifest":
        api.uploaded["release-manifest.json"] = b"{}"
    else:
        api.source = "b" * 40
    with pytest.raises(ValueError):
        release.run()
    assert not any(method in {"POST", "PATCH"} for method, _ in api.calls)


@pytest.mark.parametrize("winner", [SOURCE_SHA, "b" * 40])
def test_concurrent_tag_creation_is_verified_not_overwritten(machine, winner):
    release, api = machine
    api.tag_race = winner
    if winner == SOURCE_SHA:
        with pytest.raises(ValueError, match="concurrent"):
            release.run()
        release.run()
    else:
        with pytest.raises(ValueError, match="conflicts"):
            release.run()
        assert not api.releases


def test_concurrent_draft_creation_does_not_retry_the_post(machine):
    release, api = machine
    api.tag = SOURCE_SHA
    api.draft_race = True
    with pytest.raises(APIError, match="422"):
        release.run()
    assert len(api.releases) == 1 and not api.uploaded
    release.run()
    assert set(api.uploaded) == set(release.expected)
    assert api.calls.count(("POST", f"repos/{REPOSITORY}/releases")) == 1


@pytest.mark.parametrize(
    "change", [{"id": 18}, {"target_commitish": "b" * 40}, {"draft": False}]
)
def test_direct_read_must_confirm_the_created_draft(machine, monkeypatch, change):
    release, api = machine
    request = api.request

    def conflicting_read(endpoint, **kwargs):
        result = request(endpoint, **kwargs)
        if endpoint.endswith("/releases/17"):
            return {**result, **change}
        return result

    monkeypatch.setattr(api, "request", conflicting_read)
    with pytest.raises(ValueError):
        release.run()
    assert not api.uploaded


@pytest.mark.parametrize("value", [None, {}, {"id": True}, {"id": "17"}, {"id": 0}])
def test_invalid_create_identity_never_reaches_upload(machine, monkeypatch, value):
    release, api = machine
    request = api.request

    def malformed_create(endpoint, **kwargs):
        result = request(endpoint, **kwargs)
        if endpoint.endswith("/releases") and kwargs.get("method") == "POST":
            return value
        return result

    monkeypatch.setattr(api, "request", malformed_create)
    with pytest.raises(ValueError, match="response|ID"):
        release.run()
    assert not api.uploaded


def test_main_advances_after_reservation_blocks_finalize(machine):
    release, api = machine
    release_id = release.run()
    api.source = "b" * 40
    with pytest.raises(ValueError, match="main moved"):
        release.run(finalize=True, release_id=release_id)
    assert api.releases[0]["draft"]
    assert not any(method == "PATCH" for method, _ in api.calls)


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500, 502, 503])
def test_transport_classifies_errors_without_converting_to_absence(monkeypatch, status):
    def run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=f'HTTP/2 {status} Error\n\n{{"message":"error"}}'.encode(),
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(APIError) as error:
        GitHub().request("repos/a/b/releases")
    assert error.value.status == status


@pytest.mark.parametrize(
    "body,code",
    [
        (b"", 0),
        (b"HTTP/2 200 OK\n\n", 0),
        (b"HTTP/2 200 OK\n\n{", 0),
        (b"HTTP/2 200 OK\n\n{}", 1),
    ],
)
def test_transport_rejects_empty_malformed_and_uncertain_success(
    monkeypatch, body, code
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=code, stdout=body),
    )
    with pytest.raises(ValueError):
        GitHub().request("repos/a/b/releases")


def test_network_interruption_never_becomes_missing(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 90)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        GitHub().request("repos/a/b/releases")


def test_collection_paginates_and_rejects_duplicate_records(monkeypatch):
    api = GitHub()
    pages = [[{"id": i} for i in range(100)], [{"id": 101}]]
    calls = []

    def request(endpoint):
        calls.append(endpoint)
        return pages.pop(0)

    monkeypatch.setattr(api, "request", request)
    assert len(api.collection("repos/a/b/releases")) == 101
    assert calls[-1].endswith("page=2")
    pages[:] = [[{"id": i} for i in range(100)], [{"id": 1}]]
    with pytest.raises(ValueError, match="duplicate"):
        api.collection("repos/a/b/releases")


def test_asset_digest_is_checked_against_bytes(machine):
    release, api = machine
    api.draft()
    api.uploaded = {p.name: p.read_bytes() for p in api.dist.iterdir()}
    original = api.collection

    def collection(endpoint):
        values = original(endpoint)
        if endpoint.endswith("/assets"):
            values[0]["digest"] = "sha256:" + "0" * 64
        return values

    api.collection = collection
    with pytest.raises(ValueError, match="digest conflicts"):
        release.run()
