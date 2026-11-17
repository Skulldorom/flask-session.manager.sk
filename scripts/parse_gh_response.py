"""Parse `gh api --include` output without treating API errors as absence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

STATUS_LINE = re.compile(r"^HTTP/\S+\s+(\d{3})(?:\s|$)", re.MULTILINE)


def parse_included_response(raw: str) -> tuple[int, str]:
    matches = list(STATUS_LINE.finditer(raw))
    if not matches:
        raise ValueError("GitHub CLI output contains no HTTP status line")
    match = matches[-1]
    header_start = match.end()
    crlf_separator = raw.find("\r\n\r\n", header_start)
    lf_separator = raw.find("\n\n", header_start)
    candidates = [index for index in (crlf_separator, lf_separator) if index >= 0]
    if not candidates:
        raise ValueError("GitHub CLI response has no header/body separator")
    separator = min(candidates)
    separator_size = 4 if raw.startswith("\r\n\r\n", separator) else 2
    return int(match.group(1)), raw[separator + separator_size :]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gh-exit-code", type=int, required=True)
    parser.add_argument("--allow-not-found", action="store_true")
    args = parser.parse_args()

    status, body = parse_included_response(args.response.read_text())
    if status == 404 and args.allow_not_found:
        if args.gh_exit_code == 0:
            raise SystemExit("gh api returned success for an HTTP 404")
        args.output.write_text(json.dumps({"found": False}) + "\n")
        print("found=false")
        return
    if not 200 <= status < 300:
        raise SystemExit(f"GitHub API lookup failed with HTTP {status}")
    if args.gh_exit_code != 0:
        raise SystemExit("gh api failed despite an HTTP success response")
    try:
        json.loads(body)
    except json.JSONDecodeError as error:
        raise SystemExit("GitHub API success response was not valid JSON") from error
    args.output.write_text(body.rstrip() + "\n")
    print("found=true")


if __name__ == "__main__":
    main()
