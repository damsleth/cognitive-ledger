"""Things3 ⇄ open-loops sync engine — pure reconciliation logic.

The engine is deliberately free of side-effects: it computes a list of
``Action`` objects that the CLI layer then applies via the Things3 adapter
and the frontmatter writer.  This makes unit-testing trivial — no
subprocess calls, no filesystem writes.

Terminology
-----------
loop : a ``LoopNote`` from ``notes/05_open_loops/``
task : a Things3 task dict from ``ledger/integrations/things3.py``
marker : a ``ledger:<slug> status:<status>`` string embedded in task Notes
slug  : the loop filename stem, e.g. ``loop__fix_login_bug``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LoopInfo:
    """Minimal snapshot of a loop note for reconciliation."""

    slug: str                   # filename stem, e.g. loop__fix_login_bug
    path: str                   # absolute path to the .md file
    title: str
    status: str                 # open | blocked | snoozed | closed
    scope: str                  # work | dev | home | personal | …
    things_uuid: str | None = None  # set if previously synced
    updated: str = ""           # ISO 8601 timestamp for conflict guard
    list_id: str = ""           # explicit Things area/project UUID; overrides
    #                             scope routing when set (frontmatter
    #                             `things_list_id`) — content > scope routing.


@dataclass
class Action:
    """An operation to be applied by the CLI layer."""

    kind: str
    """One of: create | update | reverse_complete | reverse_cancel |
    orphan_flag | orphan_cancel | forward_complete | noop
    """

    # Things-side fields
    things_uuid: str | None = None
    things_title: str | None = None
    things_notes: str | None = None
    things_project: str | None = None

    # Ledger-side fields
    loop_path: str | None = None      # absolute path to .md file
    loop_slug: str | None = None
    new_loop_status: str | None = None  # for reverse_* actions
    new_things_uuid: str | None = None  # for create actions (filled after apply)

    reason: str = ""                    # human-readable explanation


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------

def _make_marker(slug: str, status: str, prefix: str = "ledger:") -> str:
    return f"{prefix}{slug} status:{status}"


def _parse_marker(notes: str, prefix: str = "ledger:") -> tuple[str, str] | None:
    """Extract (slug, status) from a Things task notes field.

    Returns None if the notes field does not contain a valid marker.
    """
    if not notes or prefix not in notes:
        return None
    for line in notes.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix):]
        parts = rest.split()
        if not parts:
            continue
        slug = parts[0]
        status = ""
        for part in parts[1:]:
            if part.startswith("status:"):
                status = part[len("status:"):]
                break
        return slug, status
    return None


# ---------------------------------------------------------------------------
# Scope → project routing
# ---------------------------------------------------------------------------

def _resolve_project(
    scope: str,
    status: str,
    *,
    list_id: str = "",
    scope_routing: dict[str, str],
    default_project: str,
    blocked_project: str,
) -> str:
    """Return the Things area/project id for a given loop.

    An explicit per-loop ``list_id`` (frontmatter ``things_list_id``) wins over
    scope routing — content-level routing beats coarse scope routing.
    """
    if list_id:
        return list_id
    if status == "blocked" and blocked_project:
        return blocked_project
    return scope_routing.get(scope, "") or default_project


# ---------------------------------------------------------------------------
# Core reconciliation
# ---------------------------------------------------------------------------

def reconcile(
    loops: list[LoopInfo],
    tasks: list[dict[str, Any]],
    *,
    marker_prefix: str = "ledger:",
    scope_routing: dict[str, str] | None = None,
    default_project: str = "",
    blocked_project: str = "",
    completed_maps_to: str = "closed",
    canceled_maps_to: str = "snoozed",
    orphan_action: str = "flag",
    closed_slugs: set[str] | None = None,
) -> list[Action]:
    """Compute the delta between ledger loops and Things tasks.

    This is a pure function — no subprocess calls, no filesystem I/O.

    Args:
        loops: All open/blocked loops from the ledger.
        tasks: Things3 tasks whose notes contain *marker_prefix*.
        marker_prefix: Prefix used in Things task notes.
        scope_routing: Ledger scope → Things project name.
        default_project: Fallback Things project for new tasks.
        blocked_project: Things project for blocked loops.
        completed_maps_to: Ledger status for completed Things tasks.
        canceled_maps_to: Ledger status for cancelled Things tasks.
        orphan_action: What to do with orphaned tasks (flag/cancel/ignore).
            closed_slugs: Slugs of loops that exist but are closed. Their tasks
                are completed, not flagged — a closed loop is finished work, not
                a deleted one.

    Returns:
        List of ``Action`` objects to apply.
    """
    scope_routing = scope_routing or {}
    actions: list[Action] = []

    # Index loops by things_uuid and slug for O(1) lookup
    loops_by_uuid: dict[str, LoopInfo] = {}
    loops_by_slug: dict[str, LoopInfo] = {}
    for loop in loops:
        loops_by_slug[loop.slug] = loop
        if loop.things_uuid:
            loops_by_uuid[loop.things_uuid] = loop

    # Index tasks by uuid and by slug (from marker)
    tasks_by_uuid: dict[str, dict] = {}
    tasks_by_slug: dict[str, dict] = {}
    for task in tasks:
        uuid = task.get("uuid") or task.get("id", "")
        if uuid:
            tasks_by_uuid[uuid] = task
        parsed = _parse_marker(task.get("notes") or "", prefix=marker_prefix)
        if parsed:
            slug, _ = parsed
            tasks_by_slug[slug] = task

    # --- Pass 1: Loops → Things (create / update / drift) ---
    for loop in loops:
        task = None
        if loop.things_uuid and loop.things_uuid in tasks_by_uuid:
            task = tasks_by_uuid[loop.things_uuid]
        elif loop.slug in tasks_by_slug:
            task = tasks_by_slug[loop.slug]

        desired_project = _resolve_project(
            loop.scope,
            loop.status,
            list_id=loop.list_id,
            scope_routing=scope_routing,
            default_project=default_project,
            blocked_project=blocked_project,
        )
        desired_notes = _make_marker(loop.slug, loop.status, prefix=marker_prefix)

        if task is None:
            # Loop not in Things yet — create
            actions.append(Action(
                kind="create",
                loop_path=loop.path,
                loop_slug=loop.slug,
                things_title=loop.title,
                things_notes=desired_notes,
                things_project=desired_project,
                reason=f"loop not found in Things",
            ))
        else:
            # Loop already in Things — check for drift
            current_title = task.get("title", "")
            current_notes = task.get("notes") or ""
            current_uuid = task.get("uuid") or task.get("id", "")
            task_status = task.get("status", "").lower()

            # Conflict guard: if Things task is completed/cancelled, handle reverse sync
            if task_status in ("completed", "logbook"):
                actions.append(Action(
                    kind="reverse_complete",
                    things_uuid=current_uuid,
                    loop_path=loop.path,
                    loop_slug=loop.slug,
                    new_loop_status=completed_maps_to,
                    new_things_uuid=current_uuid,
                    reason=f"Things task completed → loop status={completed_maps_to}",
                ))
                continue
            if task_status == "cancelled":
                actions.append(Action(
                    kind="reverse_cancel",
                    things_uuid=current_uuid,
                    loop_path=loop.path,
                    loop_slug=loop.slug,
                    new_loop_status=canceled_maps_to,
                    new_things_uuid=current_uuid,
                    reason=f"Things task cancelled → loop status={canceled_maps_to}",
                ))
                continue

            # Check for drift
            title_drifted = current_title != loop.title
            notes_drifted = desired_notes not in current_notes

            if title_drifted or notes_drifted:
                actions.append(Action(
                    kind="update",
                    things_uuid=current_uuid,
                    loop_path=loop.path,
                    loop_slug=loop.slug,
                    things_title=loop.title if title_drifted else None,
                    things_notes=desired_notes if notes_drifted else None,
                    new_things_uuid=current_uuid,
                    reason="title or notes drifted",
                ))
            else:
                actions.append(Action(
                    kind="noop",
                    things_uuid=current_uuid,
                    loop_path=loop.path,
                    loop_slug=loop.slug,
                    new_things_uuid=current_uuid,
                    reason="in sync",
                ))

    # --- Pass 2: Orphans (Things tasks whose loop no longer exists) ---
    for task in tasks:
        parsed = _parse_marker(task.get("notes") or "", prefix=marker_prefix)
        if not parsed:
            continue
        slug, _ = parsed
        uuid = task.get("uuid") or task.get("id", "")
        task_status = (task.get("status") or "").lower()

        # Skip already-completed/cancelled tasks (they're not orphans, just history)
        if task_status in ("completed", "cancelled", "logbook"):
            continue

        loop = loops_by_slug.get(slug)
        if loop is None and (not uuid or uuid not in loops_by_uuid):
            # The loop is absent from the *active* set. That happens two ways,
            # and they are not the same event: it was closed (finished work), or
            # it was deleted (genuine orphan). Completing a closed loop's task
            # is the forward half of the reverse_complete path — without it,
            # closing a loop in the ledger leaves its Things task open forever
            # and it eventually gets flagged "[orphan]", which reads as a
            # mistake rather than as done.
            if slug in (closed_slugs or set()):
                actions.append(Action(
                    kind="forward_complete",
                    things_uuid=uuid,
                    loop_slug=slug,
                    reason=f"loop {slug!r} is closed in the ledger",
                ))
                continue
            # Genuine orphan — loop deleted from ledger
            if orphan_action == "cancel":
                actions.append(Action(
                    kind="orphan_cancel",
                    things_uuid=uuid,
                    reason=f"orphan: loop {slug!r} no longer exists",
                ))
            elif orphan_action == "flag":
                actions.append(Action(
                    kind="orphan_flag",
                    things_uuid=uuid,
                    reason=f"orphan: loop {slug!r} no longer exists",
                ))
            # else: ignore

    return actions
