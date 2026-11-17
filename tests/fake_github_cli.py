#!/usr/bin/env python3
"""Stateful REST contract fixture, never contacting GitHub.

Unlike the former fake, draft creation does NOT create a Git tag, and
release-by-tag does NOT discover drafts. All assets use numeric API IDs.
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

state_path = Path(os.environ["MOCK_GH_STATE"])
state = (
    json.loads(state_path.read_text())
    if state_path.exists()
    else {
        "tag_sha": None,
        "release": None,
        "assets": {},
        "create_count": 0,
        "upload_count": 0,
        "api_endpoints": [],
        "patch_fails": False,
    }
)
args = sys.argv[1:]
endpoint = next(arg for arg in args if arg.startswith(("repos/", "https://")))
method = args[args.index("--method") + 1] if "--method" in args else "GET"
included = "--include" in args
url = urlsplit(endpoint)
path = url.path if url.scheme else endpoint.split("?")[0]
query = parse_qs(url.query if url.scheme else endpoint.partition("?")[2])
payload = (
    json.load(sys.stdin)
    if "--input" in args and args[args.index("--input") + 1] == "-"
    else {}
)
state.setdefault("api_endpoints", []).append(endpoint)


def save():
    state_path.write_text(json.dumps(state))


def respond(status, value):
    save()
    if included:
        print(f"HTTP/2.0 {status} Response\nContent-Type: application/json\n")
    print(json.dumps(value))
    sys.exit(0 if 200 <= status < 300 else 1)


save()
if "/git/ref/heads/" in path:
    respond(
        200,
        {
            "ref": "refs/heads/main",
            "object": {
                "type": "commit",
                "sha": state.get("main_sha", os.environ["SOURCE_SHA"]),
            },
        },
    )
if "/git/ref/tags/" in path:
    if state.get("tag_lookup_status"):
        respond(state["tag_lookup_status"], {"message": "simulated tag lookup error"})
    if not state.get("tag_sha"):
        respond(404, {"message": "Not Found"})
    kind = state.get("tag_object_type", "commit")
    respond(
        200,
        {
            "ref": "refs/tags/" + path.rsplit("/", 1)[1],
            "object": {
                "type": kind,
                "sha": "d" * 40 if kind == "tag" else state["tag_sha"],
            },
        },
    )
if "/git/tags/" in path:
    respond(
        200, {"sha": "d" * 40, "object": {"type": "commit", "sha": state["tag_sha"]}}
    )
if path.endswith("/git/refs") and method == "POST":
    if state.get("tag_sha"):
        respond(422, {"message": "Reference already exists"})
    state["tag_sha"] = payload["sha"]
    respond(
        201,
        {"ref": payload["ref"], "object": {"type": "commit", "sha": payload["sha"]}},
    )
if "/releases/tags/" in path:
    if state.get("release") and not state["release"]["draft"]:
        respond(200, state["release"])
    respond(404, {"message": "Not Found"})
if path.endswith("/releases/generate-notes"):
    respond(200, {"body": "Generated notes"})
if path.endswith("/releases"):
    if method == "GET":
        respond(200, [state["release"]] if state.get("release") else [])
    if state.get("release"):
        respond(422, {"message": "already_exists"})
    state["release"] = {**payload, "id": 17}
    state["create_count"] += 1
    # Deliberately do not create a tag here.
    respond(201, state["release"])
if "/releases/assets/" in path:
    index = int(path.rsplit("/", 1)[1]) - 1
    name = sorted(state["assets"])[index]
    sys.stdout.buffer.write(Path(state["assets"][name]).read_bytes())
    sys.exit(0)
if path.endswith("/assets"):
    if method == "GET":
        values = []
        for index, name in enumerate(sorted(state["assets"]), 1):
            content = Path(state["assets"][name]).read_bytes()
            values.append(
                {
                    "id": index,
                    "name": name,
                    "size": len(content),
                    "state": "uploaded",
                    "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            )
        respond(200, values)
    name = query["name"][0]
    if name in state["assets"]:
        respond(422, {"message": "already_exists"})
    store = Path(os.environ["MOCK_GH_ASSET_STORE"])
    store.mkdir(parents=True, exist_ok=True)
    source = Path(args[args.index("--input") + 1])
    shutil.copy2(source, store / name)
    state["assets"][name] = str(store / name)
    state["upload_count"] += 1
    save()
    if os.environ.get("MOCK_GH_PARTIAL_CREATE") == "1" and state["upload_count"] == 1:
        sys.exit(1)  # Upload succeeded, client interrupted before response.
    respond(201, {"id": len(state["assets"]), "name": name})
if "/releases/" in path and path.rsplit("/", 1)[1].isdigit():
    if method == "PATCH":
        if state.get("patch_fails"):
            respond(500, {"message": "simulated update failure"})
        state["release"]["draft"] = payload["draft"]
    respond(200, state["release"])
print("unexpected gh request: " + repr(args), file=sys.stderr)
sys.exit(2)
