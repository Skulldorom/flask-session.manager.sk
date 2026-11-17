"""CLI guard shared by workflow finalization and local regression tests."""

from __future__ import annotations

import argparse

from scripts.release_policy import can_finalize_release


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-state", required=True)
    parser.add_argument("--publish-result", required=True)
    parser.add_argument("--reservation-verified", action="store_true")
    args = parser.parse_args()
    if not can_finalize_release(
        reservation_verified=args.reservation_verified,
        artifact_state=args.artifact_state,
        publish_result=args.publish_result,
    ):
        raise SystemExit(
            "publication evidence is incomplete; release must remain a draft"
        )
    print("Release may be finalized.")


if __name__ == "__main__":
    main()
