"""schedulerのPID lockがWindowsでも二重起動を防ぐことを検証する。"""

import os

import pytest

import scheduler


def test_acquire_lock_rejects_running_pid(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "scheduler.lock"
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(scheduler, "_lock_file", lock_path)
    monkeypatch.setattr(scheduler, "_pid_is_running", lambda pid: True)

    assert scheduler.acquire_lock() is False
    assert lock_path.read_text(encoding="utf-8") == str(os.getpid())


def test_acquire_lock_replaces_stale_pid(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "scheduler.lock"
    lock_path.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(scheduler, "_lock_file", lock_path)
    monkeypatch.setattr(scheduler, "_pid_is_running", lambda pid: False)

    try:
        assert scheduler.acquire_lock() is True
        assert lock_path.read_text(encoding="utf-8") == str(os.getpid())
    finally:
        scheduler.release_lock()


@pytest.mark.skipif(os.name != "nt", reason="Windows PID probe only")
def test_windows_pid_probe_distinguishes_live_and_missing_process() -> None:
    assert scheduler._pid_is_running(os.getpid()) is True
    assert scheduler._pid_is_running(999999) is False
