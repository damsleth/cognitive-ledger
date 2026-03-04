from __future__ import annotations

from pathlib import Path

import pytest

from ledger.obsidian.config import default_config
from ledger.obsidian.daemon import daemon_label, daemon_status, plist_path, start_daemon, stop_daemon


class _Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_daemon_start_status_stop_with_mocked_launchctl(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = default_config(vault)
    config.ledger_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("platform.system", lambda: "Darwin")

    calls: list[list[str]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        if cmd[:2] == ["launchctl", "print"]:
            return _Proc(0, stdout="state = running")
        return _Proc(0, stdout="ok")

    monkeypatch.setattr("subprocess.run", fake_run)

    start_msg = start_daemon(config)
    running, status_msg = daemon_status(config)
    stop_msg = stop_daemon(config)

    assert daemon_label(config) in start_msg
    assert running is True
    assert "running" in status_msg
    assert daemon_label(config) in stop_msg

    path = plist_path(config)
    assert not path.exists()
    assert any(cmd[:2] == ["launchctl", "bootstrap"] for cmd in calls)
    assert any(cmd[:2] == ["launchctl", "bootout"] for cmd in calls)


def test_daemon_requires_macos(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = default_config(vault)

    monkeypatch.setattr("platform.system", lambda: "Linux")

    with pytest.raises(RuntimeError):
        start_daemon(config)
