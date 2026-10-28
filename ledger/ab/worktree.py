from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
from .corpus import InvalidSetupError


def _run_git(repo_root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        stderr = process.stderr.strip()
        stdout = process.stdout.strip()
        message = stderr or stdout or f"git {' '.join(args)} failed"
        raise InvalidSetupError(message)
    return process.stdout.strip()


def _ensure_git_available() -> None:
    if shutil.which("git") is None:
        raise InvalidSetupError("git is not available on PATH")


def _resolve_ref(repo_root: Path, ref: str) -> str:
    return _run_git(repo_root, "rev-parse", "--verify", ref)


def _resolve_head_commit(repo_root: Path) -> str:
    """Resolve the commit currently checked out in ``repo_root``."""
    return _run_git(repo_root, "rev-parse", "HEAD")


def _create_worktree(repo_root: Path, ref: str, target_path: Path) -> None:
    _run_git(repo_root, "worktree", "add", "--detach", str(target_path), ref)


def _remove_worktree(repo_root: Path, target_path: Path) -> None:
    try:
        _run_git(repo_root, "worktree", "remove", "--force", str(target_path))
    except InvalidSetupError:
        pass


def _is_ledger_code_repo(path: Path) -> bool:
    """True if ``path`` is a git checkout that actually contains the ledger code.

    Guards against `LEDGER_ROOT` pointing at the note *corpus* (which may be a
    git repo of its own but has no `ledger/ab.py`): probing/worktree-ing there
    would resolve refs against the wrong tree.
    """
    return (path / ".git").exists() and ((path / "ledger" / "ab.py").is_file() or (path / "ledger" / "ab" / "__init__.py").is_file())


def _resolve_repo_root() -> Path:
    """Resolve the git repo root for A/B worktree operations.

    Prefers `LEDGER_ROOT` (via config) **only when it points at the ledger
    source clone** (has a `.git` and a `ledger/ab.py`); otherwise falls back to
    `git rev-parse --show-toplevel` from cwd. Never trust
    `Path(__file__).parent.parent` - that points into site-packages when the
    package is installed.
    """
    from ledger.config import get_config as _get_config

    candidate = _get_config().ledger_root
    if _is_ledger_code_repo(candidate):
        return candidate
    try:
        toplevel = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(Path.cwd()),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise InvalidSetupError(
            f"A/B harness requires a git repo. Set LEDGER_ROOT to the cognitive-ledger "
            f"source clone, or run from inside it. Tried: {candidate}"
        )
    resolved = Path(toplevel).resolve()
    if not _is_ledger_code_repo(resolved):
        raise InvalidSetupError(
            f"A/B harness resolved repo root {resolved} but it does not contain the "
            f"ledger source (ledger/ab.py). LEDGER_ROOT ({candidate}) likely points at "
            f"the note corpus instead of the cognitive-ledger code clone; set it to the "
            f"source clone or run from inside it."
        )
    return resolved
