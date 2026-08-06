from __future__ import annotations

from pathlib import Path

import scripts.verify_locked_requirements as lock


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_direct_requirement_must_have_active_exact_pin(tmp_path: Path) -> None:
    constraints = _write(tmp_path / "constraints.txt", "requests==2.34.2\n")
    requirements = _write(
        tmp_path / "requirements.txt",
        "requests>=2.31.0\nPyYAML>=6.0\n",
    )

    pins = lock.load_active_pins(constraints)

    assert lock.validate_direct_requirements([requirements], pins) == [
        f"{requirements}:2: PyYAML has no active exact pin"
    ]


def test_pin_must_satisfy_direct_requirement_range(tmp_path: Path) -> None:
    constraints = _write(tmp_path / "constraints.txt", "requests==2.30.0\n")
    requirements = _write(tmp_path / "requirements.txt", "requests>=2.31.0\n")

    pins = lock.load_active_pins(constraints)

    assert lock.validate_direct_requirements([requirements], pins) == [
        f"{requirements}:1: pin requests==2.30.0 does not satisfy >=2.31.0"
    ]


def test_inactive_platform_pin_is_ignored(tmp_path: Path) -> None:
    constraints = _write(
        tmp_path / "constraints.txt",
        'requests==2.34.2\ncolorama==0.4.6 ; python_version < "0"\n',
    )

    assert lock.load_active_pins(constraints) == {"requests": "2.34.2"}


def test_active_constraint_must_be_exact(tmp_path: Path) -> None:
    constraints = _write(tmp_path / "constraints.txt", "requests>=2.31.0\n")

    try:
        lock.load_active_pins(constraints)
    except lock.LockValidationError as error:
        assert "active constraint must use one exact == pin" in str(error)
    else:
        raise AssertionError("non-exact constraint was accepted")
