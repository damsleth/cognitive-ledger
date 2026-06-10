"""Things3 CLI adapter for the ledger loops sync integration.

All subprocess calls are routed through the ``_run()`` seam so tests can
monkeypatch it.  Every write call re-reads the DB after the fact to confirm
the change landed (2 retries, 1 s apart).

CLI reference: https://culturedcode.com/things/support/articles/2803573/
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any


# ---------------------------------------------------------------------------
# Subprocess seam
# ---------------------------------------------------------------------------

def _run(args: list[str], *, timeout: int = 10) -> str:
    """Execute *args* and return stdout.  Raises ``RuntimeError`` on failure."""
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"things CLI error (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def things_available() -> bool:
    """Return True if the Things CLI is on PATH."""
    return shutil.which("things") is not None


def read_tasks(*, marker_prefix: str = "ledger:") -> list[dict[str, Any]]:
    """Return all Things tasks whose notes contain *marker_prefix*.

    Uses ``things tasks --json`` (returns all non-trashed tasks) and
    filters client-side.
    """
    raw = _run(["things", "tasks", "--json"])
    all_tasks: list[dict] = json.loads(raw or "[]")
    return [t for t in all_tasks if marker_prefix in (t.get("notes") or "")]


def create_task(
    title: str,
    notes: str = "",
    project: str = "",
    *,
    dry_run: bool = False,
    marker_prefix: str = "ledger:",
) -> str | None:
    """Create a new Things task and return its UUID.

    Args:
        title: Task title.
        notes: Notes field (should contain the ``ledger:<slug>`` marker).
        project: Project name or UUID.  Empty = Inbox.
        dry_run: If True, print the command and return None.
        marker_prefix: Prefix used to locate the new task for confirmation.

    Returns:
        UUID string on success, None on dry_run.
    """
    cmd = ["things", "add", "--title", title]
    if notes:
        cmd += ["--notes", notes]
    if project:
        cmd += ["--project", project]

    if dry_run:
        print(f"[dry-run] things create: {' '.join(cmd)}")
        return None

    _run(cmd)
    return _confirm_created(notes, marker_prefix=marker_prefix)


def update_task(
    uuid: str,
    *,
    title: str | None = None,
    notes: str | None = None,
    dry_run: bool = False,
) -> None:
    """Update task fields by UUID."""
    cmd = ["things", "update", uuid]
    if title is not None:
        cmd += ["--title", title]
    if notes is not None:
        cmd += ["--notes", notes]

    if dry_run:
        print(f"[dry-run] things update {uuid}: {cmd}")
        return

    _run(cmd)
    _confirm_by_uuid(uuid, op="update")


def complete_task(uuid: str, *, dry_run: bool = False) -> None:
    """Mark a Things task as completed."""
    if dry_run:
        print(f"[dry-run] things complete {uuid}")
        return
    _run(["things", "complete", uuid])
    _confirm_by_uuid(uuid, op="complete")


def cancel_task(uuid: str, *, dry_run: bool = False) -> None:
    """Mark a Things task as cancelled."""
    if dry_run:
        print(f"[dry-run] things cancel {uuid}")
        return
    _run(["things", "cancel", uuid])
    _confirm_by_uuid(uuid, op="cancel")


def ensure_project(name: str, *, dry_run: bool = False) -> None:
    """Create a Things project if it does not already exist."""
    projects = list_projects()
    if any(p.get("title") == name for p in projects):
        return
    if dry_run:
        print(f"[dry-run] things add-project --title {name!r}")
        return
    _run(["things", "add-project", "--title", name])


def list_projects() -> list[dict[str, Any]]:
    """Return all Things projects as a list of dicts."""
    raw = _run(["things", "projects", "--json"])
    return json.loads(raw or "[]")


def get_task_by_uuid(uuid: str) -> dict[str, Any] | None:
    """Fetch a single task by UUID; returns None if not found."""
    try:
        raw = _run(["things", "task", uuid, "--json"])
        data = json.loads(raw or "null")
        if isinstance(data, list):
            return data[0] if data else None
        return data
    except (RuntimeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Internal confirmation helpers
# ---------------------------------------------------------------------------

def _confirm_created(
    notes_snippet: str,
    *,
    marker_prefix: str = "ledger:",
    retries: int = 2,
    delay: float = 1.0,
) -> str | None:
    """Re-read Things tasks to find the newly created task by its notes field.

    Returns UUID on success or None if confirmation fails after retries.
    """
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(delay)
        tasks = read_tasks(marker_prefix=marker_prefix)
        for task in tasks:
            if notes_snippet and notes_snippet in (task.get("notes") or ""):
                return task.get("uuid") or task.get("id")
    return None


def _confirm_by_uuid(
    uuid: str,
    *,
    op: str = "update",
    retries: int = 2,
    delay: float = 1.0,
) -> None:
    """Re-read task by UUID; raise RuntimeError if not found after write."""
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(delay)
        task = get_task_by_uuid(uuid)
        if task is not None:
            return
    raise RuntimeError(
        f"things {op}: task {uuid!r} not found after {retries + 1} re-read attempts"
    )
