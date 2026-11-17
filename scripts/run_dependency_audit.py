"""Run reproducible pip-audit scans with narrow, expiring exceptions."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: run_dependency_audit.py EXCEPTIONS.json REQUIREMENTS..."
        )
    exception_file = Path(sys.argv[1])
    requirements_files = [Path(value) for value in sys.argv[2:]]
    exceptions = json.loads(exception_file.read_text()).get("exceptions", [])
    today = date.today()
    for exception in exceptions:
        required = {"package", "id", "reason", "expires"}
        if not required.issubset(exception) or not exception["reason"].strip():
            raise SystemExit("each exception needs package, id, reason, and expires")
        expiry = date.fromisoformat(exception["expires"])
        if expiry < today:
            raise SystemExit(
                "dependency audit exception expired: "
                f"{exception['package']} {exception['id']}"
            )

    failures: list[str] = []
    for requirements in requirements_files:
        command = [
            sys.executable,
            "-m",
            "pip_audit",
            "--progress-spinner",
            "off",
            "--format",
            "json",
            "--requirement",
            str(requirements),
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            raise SystemExit(
                f"pip-audit did not return JSON for {requirements}"
            ) from None
        dependencies = (
            report.get("dependencies", report) if isinstance(report, dict) else report
        )
        if isinstance(report, dict) and report.get("errors"):
            print(json.dumps(report["errors"], indent=2), file=sys.stderr)
            raise SystemExit(f"pip-audit reported scan errors for {requirements}")
        found_vulnerabilities = False
        for dependency in dependencies:
            package = dependency.get("name", "").lower().replace("_", "-")
            for vulnerability in dependency.get("vulns", []):
                found_vulnerabilities = True
                vuln_id = vulnerability.get("id")
                matching = [
                    item
                    for item in exceptions
                    if item["package"].lower().replace("_", "-") == package
                    and item["id"] == vuln_id
                ]
                if matching:
                    print(
                        f"EXCEPTION {package} {vuln_id} "
                        f"until {matching[0]['expires']}: "
                        f"{matching[0]['reason']}"
                    )
                else:
                    failures.append(f"{requirements}: {package} {vuln_id}")
        if result.returncode not in (0, 1) or (
            result.returncode == 1 and not found_vulnerabilities
        ):
            print(result.stderr, end="", file=sys.stderr)
            raise SystemExit(f"pip-audit failed to scan {requirements}")

    if failures:
        print("Actionable dependency advisories require review:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Resolved runtime and development dependencies passed the advisory policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
