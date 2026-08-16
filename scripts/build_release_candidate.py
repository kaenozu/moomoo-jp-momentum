from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.release_candidate import (
    ReleaseCandidateError,
    SourceMetadata,
    git_head_commit,
    normalize_git_commit,
)
from src.release_candidate_security import (
    secure_build_release_archive,
    secure_git_worktree_dirty,
    secure_source_files_from_git,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic, fail-closed source release candidate."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--repository",
        default=os.environ.get(
            "GITHUB_REPOSITORY",
            "local/moomoo-jp-momentum",
        ),
    )
    parser.add_argument(
        "--source-commit",
        default=os.environ.get("GITHUB_SHA", "HEAD"),
    )
    parser.add_argument(
        "--source-ref",
        default=os.environ.get("GITHUB_REF", "refs/heads/local"),
    )
    parser.add_argument(
        "--source-event",
        default=os.environ.get("GITHUB_EVENT_NAME", "local"),
    )
    parser.add_argument("--default-branch", default="master")
    parser.add_argument(
        "--allow-dirty-validation",
        action="store_true",
        help="Allow a dirty worktree only for VALIDATION_ONLY output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        source_commit = normalize_git_commit(args.source_commit)
        head_commit = git_head_commit()
        if source_commit != head_commit:
            raise ReleaseCandidateError(
                "source commit must equal checked-out HEAD: "
                f"source={source_commit}, head={head_commit}"
            )

        source_dirty = secure_git_worktree_dirty()
        if source_dirty and not args.allow_dirty_validation:
            raise ReleaseCandidateError(
                "worktree is dirty; use --allow-dirty-validation only for "
                "an explicitly non-candidate local artifact"
            )

        metadata = SourceMetadata(
            repository=args.repository,
            source_commit=source_commit,
            source_ref=args.source_ref,
            source_event=args.source_event,
            default_branch=args.default_branch,
            source_dirty=source_dirty,
        )
        source_files = secure_source_files_from_git(source_commit)
        result = secure_build_release_archive(
            args.output,
            source_files,
            metadata,
        )
    except ReleaseCandidateError as error:
        print(f"release candidate build failed: {error}")
        return 1

    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
