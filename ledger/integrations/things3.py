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


# Things CLI numeric status codes -> reconcile-friendly strings.
# 0 = open/incomplete, 2 = cancelled, 3 = completed.
_STATUS_MAP = {0: "open", 2: "cancelled", 3: "completed"}


def _normalize_status(task: dict) -> dict:
    """Coerce the numeric ``status`` field to the string reconcile expects."""
    st = task.get("status")
    if isinstance(st, int):
        task = {**task, "status": _STATUS_MAP.get(st, "open")}
    return task


def read_tasks(*, marker_prefix: str = "ledger:") -> list[dict[str, Any]]:
    """Return all Things tasks whose notes contain *marker_prefix*.

    ``things tasks --json`` lists only *active* todos; completed live in
    ``logbook`` and cancelled in ``canceled``.  Reverse-sync needs those too
    (otherwise a task completed in Things is invisible → its loop never
    closes and a duplicate is recreated each run), so union all three.
    ``--limit=0`` disables the CLI's default 200-row cap.  Dedupe by uuid;
    the numeric ``status`` is normalized to a string for reconcile.
    """
    by_uuid: dict[str, dict] = {}
    for section in ("tasks", "logbook", "canceled"):
        raw = _run(["things", section, "--json", "--limit=0"])
        for t in json.loads(raw or "[]"):
            if marker_prefix not in (t.get("notes") or ""):
                continue
            uuid = t.get("uuid") or t.get("id") or ""
            # tasks (active) wins over logbook/canceled on uuid collision
            by_uuid.setdefault(uuid, _normalize_status(t))
    return list(by_uuid.values())


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
        project: Project/area **UUID** for the ``--list-id`` flag (stable
            across title renames).  Empty = Inbox.
        dry_run: If True, print the command and return None.
        marker_prefix: Prefix used to locate the new task for confirmation.

    Returns:
        UUID string on success, None on dry_run.
    """
    # Installed `things` CLI: title is positional, notes via --notes=,
    # project/area targeted by --list-id=ID.
    cmd = ["things", "add"]
    if notes:
        cmd += [f"--notes={notes}"]
    if project:
        cmd += [f"--list-id={project}"]
    cmd += ["--", title]

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
    """Update task fields by UUID.

    Installed `things` CLI: target by ``--id=UUID``, title is positional,
    notes via ``--notes=``.  Modifying existing todos may require a Things
    auth token (set via the THINGS_AUTH_TOKEN env / CLI config).
    """
    cmd = ["things", "update", "--yes", f"--id={uuid}"]
    if notes is not None:
        cmd += [f"--notes={notes}"]
    if title is not None:
        cmd += ["--allow-unsafe-title", "--", title]

    if dry_run:
        print(f"[dry-run] things update {uuid}: {cmd}")
        return

    _run(cmd)
    _confirm_by_uuid(uuid, op="update")


def complete_task(uuid: str, *, dry_run: bool = False) -> None:
    """Mark a Things task as completed (via ``things update --completed``)."""
    if dry_run:
        print(f"[dry-run] things update --id={uuid} --completed")
        return
    _run(["things", "update", "--yes", f"--id={uuid}", "--completed"])
    _confirm_gone_from_active(uuid, op="complete")


def cancel_task(uuid: str, *, dry_run: bool = False) -> None:
    """Mark a Things task as cancelled (via ``things update --canceled``)."""
    if dry_run:
        print(f"[dry-run] things update --id={uuid} --canceled")
        return
    _run(["things", "update", "--yes", f"--id={uuid}", "--canceled"])
    _confirm_gone_from_active(uuid, op="cancel")


def ensure_project(name: str, *, dry_run: bool = False) -> None:
    """Create a Things project if it does not already exist."""
    projects = list_projects()
    if any(p.get("title") == name for p in projects):
        return
    if dry_run:
        print(f"[dry-run] things add-project {name!r}")
        return
    _run(["things", "add-project", "--", name])


def list_projects() -> list[dict[str, Any]]:
    """Return all Things projects as a list of dicts."""
    raw = _run(["things", "projects", "--json"])
    return json.loads(raw or "[]")


def get_task_by_uuid(uuid: str) -> dict[str, Any] | None:
    """Fetch a single active task by UUID; returns None if not found.

    The installed CLI has no per-UUID fetch, so scan ``things tasks --json``.
    """
    try:
        raw = _run(["things", "tasks", "--json", "--limit=0"])
        for t in json.loads(raw or "[]"):
            if (t.get("uuid") or t.get("id")) == uuid:
                return _normalize_status(t)
        return None
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
    # Match on the parsed marker slug (robust to Things reformatting the notes
    # body), falling back to raw-substring match if no marker is present.
    from ledger.integrations.things3_sync import _parse_marker
    want = _parse_marker(notes_snippet, prefix=marker_prefix)
    want_slug = want[0] if want else None

    for attempt in range(retries + 1):
        if attempt:
            time.sleep(delay)
        tasks = read_tasks(marker_prefix=marker_prefix)
        for task in tasks:
            task_notes = task.get("notes") or ""
            if want_slug:
                parsed = _parse_marker(task_notes, prefix=marker_prefix)
                if parsed and parsed[0] == want_slug:
                    return task.get("uuid") or task.get("id")
            elif notes_snippet and notes_snippet in task_notes:
                return task.get("uuid") or task.get("id")
    return None


def _confirm_gone_from_active(
    uuid: str,
    *,
    op: str,
    retries: int = 2,
    delay: float = 1.0,
) -> None:
    """Confirm a completed/cancelled task has left the active list.

    ``get_task_by_uuid`` scans ``things tasks --json``, which lists *active*
    tasks only. Confirming a complete/cancel by re-reading it there can never
    succeed — success is precisely the task no longer being active. Using the
    presence check made every completion report a false error, which would
    also have hidden a genuine one.
    """
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(delay)
        if get_task_by_uuid(uuid) is None:
            return
    raise RuntimeError(
        f"things {op}: task {uuid!r} is still active after "
        f"{retries + 1} re-read attempts"
    )


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
