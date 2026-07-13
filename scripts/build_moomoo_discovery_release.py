#!/usr/bin/env python3
"""Build a deterministic master-bound production-discovery release package."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
OPERATOR_BUILDER_PATH = (
    ROOT / "scripts" / "build_moomoo_discovery_operator_bundle.py"
)
RELEASE_VERIFIER_PATH = (
    ROOT / "scripts" / "compare_moomoo_discovery_releases.py"
)
HUMAN_SOURCE = ROOT / "tools" / "production_discovery"
OPERATOR_ZIP = "moomoo_production_discovery_operator_v4_v1.2.1.zip"
RELEASE_ZIP = "moomoo_production_discovery_release_v1.2.1.zip"
RELEASE_VERIFIER = "compare_moomoo_discovery_releases.py"
HUMAN_FILES = (
    "human-validation.schema.json",
    "human-validation.template.json",
    "validate_moomoo_human_validation.py",
    "README_moomoo_human_validation_ja.md",
)
EXPECTED_AUTHORIZATION = {
    "production_readiness": "BLOCKED",
    "preflight_authorized": False,
    "production_drill_authorized": False,
    "cutover_authorized": False,
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


operator_builder = load_module(
    "moomoo_operator_bundle_builder", OPERATOR_BUILDER_PATH
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_identity() -> tuple[str, str | None, str | None, str]:
    head = operator_builder.git_text("rev-parse", "HEAD")
    source_commit = os.environ.get("GITHUB_SHA") or head
    source_ref = os.environ.get("GITHUB_REF")
    source_event = os.environ.get("GITHUB_EVENT_NAME")
    if source_commit != head:
        raise RuntimeError(
            f"GITHUB_SHA does not match checked-out HEAD: {source_commit} != {head}"
        )
    return source_commit, source_ref, source_event, head


def classify_release_candidate(
    source_commit: str,
    source_ref: str | None,
    source_event: str | None,
    checked_out_head: str,
) -> bool:
    return (
        source_commit == checked_out_head
        and source_ref == "refs/heads/master"
        and source_event == "push"
    )


def read_operator_manifest(operator_zip: Path) -> dict[str, Any]:
    with zipfile.ZipFile(operator_zip, "r") as archive:
        try:
            payload = json.loads(
                archive.read("bundle-manifest.json").decode("utf-8")
            )
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Operator bundle manifest is invalid: {exc}"
            ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Operator bundle manifest must be a JSON object")
    return payload


def write_deterministic_zip(stage: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    fixed_time = (2026, 7, 13, 0, 0, 0)
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(
            stage.iterdir(), key=lambda item: item.name.lower()
        ):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.name, date_time=fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def build_release(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    operator_output = output_dir / "_operator"
    if operator_output.exists():
        shutil.rmtree(operator_output)
    operator_result = operator_builder.build_bundle(operator_output)
    operator_zip = Path(str(operator_result["zip"]))
    if operator_zip.name != OPERATOR_ZIP:
        raise RuntimeError(
            f"Unexpected operator ZIP name: {operator_zip.name}"
        )

    source_commit, source_ref, source_event, head = source_identity()
    release_candidate = classify_release_candidate(
        source_commit, source_ref, source_event, head
    )
    distribution_status = (
        "MASTER_RELEASE_CANDIDATE"
        if release_candidate
        else "VALIDATION_ONLY"
    )

    operator_manifest = read_operator_manifest(operator_zip)
    if operator_manifest.get("source_commit") != source_commit:
        raise RuntimeError(
            "Operator bundle source_commit does not match release source_commit"
        )
    if operator_manifest.get("source_ref") != source_ref:
        raise RuntimeError(
            "Operator bundle source_ref does not match release source_ref"
        )
    if operator_manifest.get("authorization") != EXPECTED_AUTHORIZATION:
        raise RuntimeError(
            "Operator bundle authorization is not fail-closed"
        )

    stage = output_dir / "moomoo_production_discovery_release_v1.2.1"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    (stage / OPERATOR_ZIP).write_bytes(operator_zip.read_bytes())
    for name in HUMAN_FILES:
        source = HUMAN_SOURCE / name
        (stage / name).write_bytes(
            operator_builder.verified_tracked_bytes(source)
        )
    (stage / RELEASE_VERIFIER).write_bytes(
        operator_builder.verified_tracked_bytes(RELEASE_VERIFIER_PATH)
    )

    manifest = {
        "report_type": "moomoo_discovery_release_manifest",
        "release_format_version": 1,
        "operator_version": "1.2.1",
        "built_at": operator_builder.deterministic_built_at(),
        "source_commit": source_commit,
        "source_ref": source_ref,
        "source_event": source_event,
        "source_bytes": "git_blob",
        "release_candidate": release_candidate,
        "distribution_status": distribution_status,
        "operator_bundle": {
            "filename": OPERATOR_ZIP,
            "sha256": sha256_file(stage / OPERATOR_ZIP),
            "manifest_source_commit": operator_manifest.get(
                "source_commit"
            ),
            "manifest_source_ref": operator_manifest.get(
                "source_ref"
            ),
        },
        "human_validation": {
            "schema": "human-validation.schema.json",
            "template": "human-validation.template.json",
            "validator": "validate_moomoo_human_validation.py",
            "readme": "README_moomoo_human_validation_ja.md",
            "release_verifier": RELEASE_VERIFIER,
            "outputs": [
                "06-human-validation.json",
                "07-preflight-eligibility.json",
            ],
        },
        "authorization": EXPECTED_AUTHORIZATION,
        "separate_approval_required": True,
    }
    (stage / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(
            stage.iterdir(), key=lambda item: item.name.lower()
        )
        if path.is_file()
    ]
    (stage / "SHA256SUMS.txt").write_text(
        "\n".join(sums) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    release_zip = output_dir / RELEASE_ZIP
    write_deterministic_zip(stage, release_zip)
    return {
        "report_type": "moomoo_discovery_release_build",
        "operator_version": "1.2.1",
        "source_commit": source_commit,
        "source_ref": source_ref,
        "source_event": source_event,
        "release_candidate": release_candidate,
        "distribution_status": distribution_status,
        "release_zip": str(release_zip),
        "release_zip_sha256": sha256_file(release_zip),
        "operator_zip_sha256": sha256_file(stage / OPERATOR_ZIP),
        "member_count": len(
            [path for path in stage.iterdir() if path.is_file()]
        ),
        "authorization": EXPECTED_AUTHORIZATION,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json-output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    result = build_release(Path(args.output_dir).resolve())
    rendered = json.dumps(
        result, ensure_ascii=False, indent=2
    ) + "\n"
    if args.json_output:
        output = Path(args.json_output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            rendered, encoding="utf-8", newline="\n"
        )
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
