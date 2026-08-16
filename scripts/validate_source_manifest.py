from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.source_manifest import (
    ManifestError,
    build_manifest,
    manifest_bytes,
    validate_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a deterministic, validation-only source manifest."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        manifest = build_manifest(args.root, source_commit=args.source_commit)
        validate_manifest(args.root, manifest)
        payload = manifest_bytes(manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        print(
            json.dumps(
                {"manifest_sha256": digest, "member_count": manifest["member_count"]}
            )
        )
    except (ManifestError, OSError, TypeError, ValueError) as error:
        print(f"source manifest validation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
