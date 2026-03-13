"""Shared repo-venv re-exec helper for scripts and TUI entrypoints."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence


def maybe_reexec_in_repo_venv(
    repo_root: Path,
    argv: Sequence[str] | None = None,
    *,
    script_path: Path | None = None,
    module: str | None = None,
) -> None:
    """Re-exec into ``repo_root/.venv`` when available and not already active."""
    if os.environ.get("COG_LEDGER_VENV_REEXEC") == "1":
        return

    repo_root = Path(repo_root).resolve()
    venv_dir = repo_root / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.is_file():
        return

    try:
        in_target_venv = Path(sys.prefix).resolve() == venv_dir
    except Exception:
        return
    if in_target_venv:
        return

    if argv is not None:
        exec_argv = list(argv)
    elif module is not None:
        exec_argv = [str(venv_python), "-m", module, *sys.argv[1:]]
    else:
        target = script_path or Path(sys.argv[0]).resolve()
        exec_argv = [str(venv_python), str(target), *sys.argv[1:]]
    env = os.environ.copy()
    env["COG_LEDGER_VENV_REEXEC"] = "1"
    env["VIRTUAL_ENV"] = str(venv_dir)
    env["PATH"] = f"{venv_dir / 'bin'}:{env.get('PATH', '')}"
    os.execve(str(venv_python), exec_argv, env)
