"""Recoverable GitHub release reservation, using refs and release/asset IDs.

No mutation is blindly retried: after an uncertain result the job fails, and
the next authorized invocation rediscovers and verifies durable API state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from scripts.parse_gh_response import parse_included_response
from scripts.resolve_tag_ref import SHA, _object


class APIError(ValueError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"GitHub API failed with HTTP {status}")


class GitHub:
    def request(self, endpoint, *, method="GET", payload=None, file=None, raw=False):
        command = ["gh", "api", "--method", method, endpoint]
        if raw:
            command += ["-H", "Accept: application/octet-stream"]
        else:
            command += ["--include"]
        data = None
        if payload is not None:
            command += ["--input", "-"]
            data = json.dumps(payload).encode()
        if file is not None:
            command += [
                "--input",
                str(file),
                "-H",
                "Content-Type: application/octet-stream",
            ]
        result = subprocess.run(
            command, input=data, capture_output=True, timeout=90, check=False
        )
        if raw:
            if result.returncode:
                raise ValueError("GitHub asset download failed")
            return result.stdout
        status, body = parse_included_response(result.stdout.decode("utf-8"))
        if not 200 <= status < 300:
            raise APIError(status)
        if result.returncode:
            raise ValueError("GitHub CLI failed despite an HTTP success response")
        return json.loads(body)

    def collection(self, endpoint):
        records = []
        seen = set()
        for page in range(1, 1001):
            values = self.request(f"{endpoint}?per_page=100&page={page}")
            if not isinstance(values, list):
                raise ValueError("GitHub returned a malformed collection")
            for value in values:
                if not isinstance(value, dict) or type(value.get("id")) is not int:
                    raise ValueError("GitHub collection record has no numeric ID")
                if value["id"] in seen:
                    raise ValueError("GitHub pagination returned duplicate records")
                seen.add(value["id"])
            records.extend(values)
            if len(values) < 100:
                return records
        raise ValueError("GitHub collection exceeded pagination bound")


class Release:
    def __init__(self, api, repository, source, version, dist):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        if not SHA.fullmatch(source) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("invalid release identity")
        self.api, self.repository = api, repository
        self.base = f"repos/{repository}"
        self.source, self.version, self.dist = source, version, dist
        self.tag = f"v{version}"
        self.manifest = json.loads((dist / "release-manifest.json").read_text())
        if (
            self.manifest.get("source_sha") != source
            or self.manifest.get("version") != version
        ):
            raise ValueError("candidate manifest identity conflicts with authorization")
        artifacts = self.manifest.get("artifacts")
        if not isinstance(artifacts, dict) or len(artifacts) != 2:
            raise ValueError("expected exactly wheel and sdist")
        if (
            sum(name.endswith(".whl") for name in artifacts) != 1
            or sum(name.endswith(".tar.gz") for name in artifacts) != 1
        ):
            raise ValueError("expected wheel and sdist filenames")
        self.expected = dict(artifacts)
        self.expected["release-manifest.json"] = hashlib.sha256(
            (dist / "release-manifest.json").read_bytes()
        ).hexdigest()
        for name, digest in self.expected.items():
            if Path(name).name != name or ".." in name or "\\" in name:
                raise ValueError("unsafe asset filename")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid artifact digest")
            if hashlib.sha256((dist / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"candidate asset hash conflicts: {name}")

    def guard(self):
        ref = self.api.request(f"{self.base}/git/ref/heads/main")
        if ref.get("ref") != "refs/heads/main" or _object(ref.get("object")) != (
            "commit",
            self.source,
        ):
            raise ValueError("main moved; release source is stale")

    def tag_commit(self):
        try:
            ref = self.api.request(f"{self.base}/git/ref/tags/{self.tag}")
        except APIError as error:
            if error.status == 404:
                return None
            raise
        if ref.get("ref") != f"refs/tags/{self.tag}":
            raise ValueError("GitHub returned a different tag ref")
        kind, sha = _object(ref.get("object"))
        seen = set()
        for _ in range(10):
            if kind == "commit":
                if sha != self.source:
                    raise ValueError("existing tag conflicts with authorized source")
                return sha
            if sha in seen:
                raise ValueError("tag object cycle")
            seen.add(sha)
            obj = self.api.request(f"{self.base}/git/tags/{sha}")
            if obj.get("sha") != sha:
                raise ValueError("tag object identity conflicts")
            kind, sha = _object(obj.get("object"))
        raise ValueError("tag chain too deep")

    def validate(self, release):
        if (
            release.get("tag_name") != self.tag
            or release.get("target_commitish") != self.source
            or type(release.get("draft")) is not bool
            or release.get("prerelease") is not False
        ):
            raise ValueError("release identity, target or status conflicts")
        lines = (release.get("body") or "").splitlines()
        for key, value in (
            ("Release-Source-SHA", self.source),
            ("Package-Version", self.version),
        ):
            if [line for line in lines if line.startswith(key + ":")] != [
                f"{key}: {value}"
            ]:
                raise ValueError("release source metadata conflicts")
        return release

    def by_id(self, release_id):
        if type(release_id) is not int or release_id <= 0:
            raise ValueError("invalid GitHub release ID")
        release = self.api.request(f"{self.base}/releases/{release_id}")
        if not isinstance(release, dict) or type(release.get("id")) is not int:
            raise ValueError("GitHub returned a malformed release")
        if release["id"] != release_id:
            raise ValueError("GitHub returned a different release ID")
        return self.validate(release)

    def discover(self):
        matches = [
            r
            for r in self.api.collection(f"{self.base}/releases")
            if r.get("tag_name") == self.tag
        ]
        if len(matches) > 1:
            raise ValueError("multiple releases reserve this version")
        if not matches:
            return None
        return self.by_id(matches[0]["id"])

    def assets(self, release):
        assets = self.api.collection(f"{self.base}/releases/{release['id']}/assets")
        present = set()
        for asset in assets:
            name = asset.get("name")
            if name not in self.expected or name in present:
                raise ValueError("unexpected or duplicate release asset")
            if (
                asset.get("state") != "uploaded"
                or type(asset.get("size")) is not int
                or asset["size"] <= 0
            ):
                raise ValueError(
                    "incomplete release asset; manual reconciliation required"
                )
            content = self.api.request(
                f"{self.base}/releases/assets/{asset['id']}", raw=True
            )
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != asset["size"] or digest != self.expected[name]:
                raise ValueError(f"existing release asset conflicts: {name}")
            if asset.get("digest") not in (None, f"sha256:{digest}"):
                raise ValueError("GitHub asset digest conflicts with downloaded bytes")
            present.add(name)
        return sorted(self.expected.keys() - present)

    def run(self, *, finalize=False, release_id=None, reserve_only=False):
        self.guard()
        tag = self.tag_commit()
        release = self.discover() if release_id is None else self.by_id(release_id)
        if tag is not None and release is None:
            raise ValueError(
                "tag exists but no draft is visible; refusing another draft creation"
            )
        if release_id is not None and tag is None:
            raise ValueError("verified reservation tag is missing")
        # Validate every existing byte BEFORE reconciling a missing tag.
        missing = self.assets(release) if release else sorted(self.expected)
        if finalize and (release is None or tag is None or missing):
            raise ValueError("finalization requires a complete verified reservation")
        if release and not release["draft"] and (missing or tag is None):
            raise ValueError("published release has incomplete reservation")
        if release is None:
            notes = self.api.request(
                f"{self.base}/releases/generate-notes",
                method="POST",
                payload={"tag_name": self.tag, "target_commitish": self.source},
            )
        if tag is None:
            self.guard()
            try:
                self.api.request(
                    f"{self.base}/git/refs",
                    method="POST",
                    payload={"ref": f"refs/tags/{self.tag}", "sha": self.source},
                )
            except APIError as error:
                if error.status != 422:
                    raise
                # Resolve the winner, but do not race it to create a draft.
                self.tag_commit()
                if release is None:
                    raise ValueError(
                        "concurrent tag reservation; retry after state settles"
                    ) from error
            if self.tag_commit() != self.source:
                raise ValueError("tag creation did not reserve authorized commit")
        if release is None:
            self.guard()
            created = self.api.request(
                f"{self.base}/releases",
                method="POST",
                payload={
                    "tag_name": self.tag,
                    "target_commitish": self.source,
                    "name": self.tag,
                    "draft": True,
                    "prerelease": False,
                    "body": (
                        f"Release-Source-SHA: {self.source}\n"
                        f"Package-Version: {self.version}\n\n{notes['body']}"
                    ),
                },
            )
            # POST returned the identity. The collection may omit this draft.
            if not isinstance(created, dict):
                raise ValueError("GitHub returned a malformed create response")
            release = self.by_id(created.get("id"))
            if not release["draft"]:
                raise ValueError("new reservation is not a draft")
            if self.tag_commit() != self.source:
                raise ValueError("release tag disappeared after draft creation")
            missing = self.assets(release)
        if reserve_only:
            self.guard()
            return release["id"]
        for name in missing:
            self.guard()
            # Re-read before each upload: never clobber another invocation's asset.
            current = self.by_id(release["id"])
            if not current["draft"]:
                raise ValueError("draft changed during asset upload")
            if self.tag_commit() != self.source:
                raise ValueError("release tag disappeared before asset upload")
            if name not in self.assets(current):
                continue
            self.api.request(
                (
                    f"https://uploads.github.com/{self.base}/releases/{release['id']}"
                    f"/assets?name={quote(name, safe='')}"
                ),
                method="POST",
                file=self.dist / name,
            )
        current = self.by_id(release["id"])
        if current["draft"] != release["draft"] or self.assets(current):
            raise ValueError("release reservation is incomplete or changed")
        self.guard()
        if self.tag_commit() != self.source:
            raise ValueError("release tag disappeared")
        if finalize and current["draft"]:
            # Caller must have just verified PyPI hashes; workflow DAG enforces it.
            self.api.request(
                f"{self.base}/releases/{current['id']}",
                method="PATCH",
                payload={"draft": False},
            )
            published = self.by_id(current["id"])
            if published["draft"]:
                raise ValueError("release publication not confirmed")
        print(
            f"Verified {'published release' if finalize else 'reservation'} "
            f"{self.tag} at {self.source}"
        )
        return current["id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["reserve", "upload", "finalize", "list"])
    args = parser.parse_args()
    api = GitHub()
    repository = os.environ["GITHUB_REPOSITORY"]
    if args.action == "list":
        print(json.dumps(api.collection(f"repos/{repository}/releases")))
        return
    finalize = args.action == "finalize"
    release_id = None
    if args.action in {"upload", "finalize"}:
        value = os.environ.get("RELEASE_ID", "")
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError("a verified numeric reservation ID is required")
        release_id = int(value)
    verified_id = Release(
        api,
        repository,
        os.environ["SOURCE_SHA"],
        os.environ["VERSION"],
        Path("dist"),
    ).run(
        finalize=finalize, release_id=release_id, reserve_only=args.action == "reserve"
    )
    if args.action == "reserve" and (output := os.environ.get("GITHUB_OUTPUT")):
        with Path(output).open("a") as stream:
            stream.write(f"release_id={verified_id}\n")


if __name__ == "__main__":
    main()
