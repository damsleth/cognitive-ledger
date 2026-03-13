from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

from ledger.io import safe_write_text

from .models import ObsidianLedgerConfig


def _require_macos() -> None:
    if platform.system().lower() != "darwin":
        raise RuntimeError("daemon commands are currently supported on macOS only")


def _vault_hash(vault_root: Path) -> str:
    return hashlib.sha1(str(vault_root.resolve()).encode("utf-8")).hexdigest()[:12]


def daemon_label(config: ObsidianLedgerConfig) -> str:
    return f"com.cognitiveledger.obsidian.{_vault_hash(config.vault_root)}"


def plist_path(config: ObsidianLedgerConfig) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{daemon_label(config)}.plist"


def _domain_target() -> str:
    uid = os.getuid()
    return f"gui/{uid}"


def _service_target(config: ObsidianLedgerConfig) -> str:
    return f"{_domain_target()}/{daemon_label(config)}"


def _plist_content(config: ObsidianLedgerConfig) -> str:
    logs_dir = config.ledger_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "watch.stdout.log"
    stderr_path = logs_dir / "watch.stderr.log"

    args = [
        sys.executable,
        "-m",
        "ledger.obsidian.cli",
        "watch",
        "--vault",
        str(config.vault_root),
        "--debounce-seconds",
        str(config.debounce_seconds),
    ]

    from xml.sax.saxutils import escape

    arg_xml = "\n".join(f"    <string>{escape(str(arg))}</string>" for arg in args)
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key>
  <string>{escape(daemon_label(config))}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{escape(str(stdout_path))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(stderr_path))}</string>
  <key>WorkingDirectory</key>
  <string>{escape(str(config.vault_root))}</string>
</dict>
</plist>
"""


def start_daemon(config: ObsidianLedgerConfig) -> str:
    _require_macos()

    path = plist_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(path, _plist_content(config))

    domain = _domain_target()
    service = _service_target(config)

    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False, capture_output=True, text=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True, capture_output=True, text=True)
    subprocess.run(["launchctl", "kickstart", "-k", service], check=False, capture_output=True, text=True)

    return f"started: {service}"


def stop_daemon(config: ObsidianLedgerConfig) -> str:
    _require_macos()

    path = plist_path(config)
    domain = _domain_target()

    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False, capture_output=True, text=True)
    if path.exists():
        path.unlink(missing_ok=True)

    return f"stopped: {_service_target(config)}"


def daemon_status(config: ObsidianLedgerConfig) -> tuple[bool, str]:
    _require_macos()

    service = _service_target(config)
    proc = subprocess.run(["launchctl", "print", service], check=False, capture_output=True, text=True)
    if proc.returncode == 0:
        return True, proc.stdout.strip() or service

    if plist_path(config).exists():
        return False, f"installed but not running: {service}"
    return False, f"not installed: {service}"
