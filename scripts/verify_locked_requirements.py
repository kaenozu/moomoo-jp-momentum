from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


class LockValidationError(ValueError):
    """Raised when the committed dependency contract is not reproducible."""


def _requirements(path: Path) -> Iterable[tuple[int, Requirement]]:
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if not line:
            continue
        if line.startswith(("-", "--")):
            raise LockValidationError(
                f"{path}:{line_number}: nested requirement options are not supported"
            )
        try:
            yield line_number, Requirement(line)
        except InvalidRequirement as error:
            raise LockValidationError(
                f"{path}:{line_number}: invalid requirement: {line!r}"
            ) from error


def _is_active(requirement: Requirement) -> bool:
    return requirement.marker is None or requirement.marker.evaluate()


def _exact_version(
    path: Path, line_number: int, requirement: Requirement
) -> str:
    specifiers = list(requirement.specifier)
    if (
        len(specifiers) != 1
        or specifiers[0].operator != "=="
        or "*" in specifiers[0].version
    ):
        raise LockValidationError(
            f"{path}:{line_number}: active constraint must use one exact == pin: "
            f"{requirement}"
        )
    return specifiers[0].version


def load_active_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line_number, requirement in _requirements(path):
        if not _is_active(requirement):
            continue
        name = canonicalize_name(requirement.name)
        pinned_version = _exact_version(path, line_number, requirement)
        previous = pins.get(name)
        if previous is not None and previous != pinned_version:
            raise LockValidationError(
                f"{path}:{line_number}: conflicting pins for {name}: "
                f"{previous} and {pinned_version}"
            )
        pins[name] = pinned_version
    return pins


def validate_direct_requirements(
    requirement_paths: Iterable[Path], pins: dict[str, str]
) -> list[str]:
    errors: list[str] = []
    for path in requirement_paths:
        for line_number, requirement in _requirements(path):
            if not _is_active(requirement):
                continue
            name = canonicalize_name(requirement.name)
            pinned_version = pins.get(name)
            if pinned_version is None:
                errors.append(
                    f"{path}:{line_number}: {requirement.name} has no active exact pin"
                )
                continue
            if requirement.specifier and not requirement.specifier.contains(
                pinned_version, prereleases=True
            ):
                errors.append(
                    f"{path}:{line_number}: pin {requirement.name}=={pinned_version} "
                    f"does not satisfy {requirement.specifier}"
                )
    return errors


def validate_installed_versions(pins: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name, expected in sorted(pins.items()):
        try:
            actual = version(name)
        except PackageNotFoundError:
            errors.append(f"{name}=={expected} is pinned but not installed")
            continue
        if actual != expected:
            errors.append(f"{name}: installed {actual}, expected {expected}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify exact constraints, direct requirements, and installed versions."
    )
    parser.add_argument(
        "--constraints",
        type=Path,
        default=Path("constraints/py311.txt"),
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        action="append",
        default=[Path("requirements.txt"), Path("requirements-dev.txt")],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        pins = load_active_pins(args.constraints)
        errors = validate_direct_requirements(args.requirements, pins)
        errors.extend(validate_installed_versions(pins))
    except (LockValidationError, OSError) as error:
        print(f"dependency lock validation failed: {error}")
        return 1

    if errors:
        print("dependency lock validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"dependency lock validation passed: {len(pins)} active exact pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
