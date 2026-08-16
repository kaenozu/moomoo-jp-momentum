from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.release_candidate import ReleaseCandidateError
from src.release_candidate_security import secure_verify_release_archive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify a deterministic moomoo source release candidate."
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected-repository")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-ref")
    parser.add_argument("--expected-event")
    parser.add_argument("--expected-status")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = secure_verify_release_archive(
            args.archive,
            expected_repository=args.expected_repository,
            expected_commit=args.expected_commit,
            expected_ref=args.expected_ref,
            expected_event=args.expected_event,
            expected_status=args.expected_status,
        )
    except ReleaseCandidateError as error:
        print(f"release candidate verification failed: {error}")
        return 1

    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
