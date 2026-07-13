#!/usr/bin/env python3
"""Apply the final PR #32 review fixes using exact, fail-closed replacements."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one replacement target in {path}, found {count}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def main() -> None:
    validator = (
        ROOT
        / "tools"
        / "production_discovery"
        / "validate_moomoo_human_validation.py"
    )
    old_authorization = '''def authorization_is_fail_closed(payload: Any) -> bool:\n    return isinstance(payload, dict) and all(\n        payload.get(key) == expected\n        for key, expected in EXPECTED_AUTHORIZATION.items()\n    )\n'''
    new_authorization = '''def authorization_is_fail_closed(payload: Any) -> bool:\n    if not isinstance(payload, dict):\n        return False\n    authorization_keys = {\n        key\n        for key in payload\n        if key == "production_readiness" or key.endswith("_authorized")\n    }\n    return (\n        authorization_keys == set(EXPECTED_AUTHORIZATION)\n        and all(\n            payload.get(key) == expected\n            for key, expected in EXPECTED_AUTHORIZATION.items()\n        )\n    )\n'''
    replace_exact(validator, old_authorization, new_authorization)

    human_tests = ROOT / "tests" / "test_moomoo_human_validation.py"
    human_anchor = '''def test_confirmed_without_evidence_is_correction_required() -> None:\n    human = confirmed_human_payload()\n    human["checks"]["launch_source"]["evidence_refs"] = []\n    _, result = validator.evaluate(\n        human,\n        operator_result(),\n        discovery_result(),\n        release_manifest(),\n    )\n    assert result["eligibility_status"] == "CORRECTION_REQUIRED"\n\n\n'''
    human_replacement = human_anchor + '''def test_extra_authorization_key_is_correction_required() -> None:\n    operator = operator_result()\n    operator["emergency_authorized"] = False\n    _, result = validator.evaluate(\n        confirmed_human_payload(),\n        operator,\n        discovery_result(),\n        release_manifest(),\n    )\n    assert result["eligibility_status"] == "CORRECTION_REQUIRED"\n\n\n'''
    replace_exact(human_tests, human_anchor, human_replacement)

    release_tests = ROOT / "tests" / "test_moomoo_discovery_release.py"
    release_anchor = '''def test_release_candidate_requires_master_push() -> None:\n'''
    release_replacement = '''def test_sha256sums_utf8_bom_is_supported() -> None:\n    digest = "a" * 64\n    parsed = comparer.parse_sums(\n        b"\\xef\\xbb\\xbf" + f"{digest}  payload.txt\\n".encode("utf-8")\n    )\n    assert parsed == {"payload.txt": digest}\n\n\n''' + release_anchor
    replace_exact(release_tests, release_anchor, release_replacement)


if __name__ == "__main__":
    main()
