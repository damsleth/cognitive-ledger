"""`ledger loops sync`: mirror open loops to Things 3."""

from __future__ import annotations

import sys
from pathlib import Path

from ledger.layout import ARCHIVE_SUBDIR, NOTE_LAYOUTS

def handle_loops_sync(args) -> None:
    """Run the Things3 ⇄ open-loops sync."""
    from ledger.config import get_config
    from ledger.integrations.things3_sync import LoopInfo, reconcile

    cfg = get_config()

    if not cfg.things3_sync_enabled:
        print(
            "Things3 sync disabled "
            "(set things3_sync_enabled: true in ~/.config/ledger/config.yaml)"
        )
        return

    dry_run = getattr(args, "dry_run", False)

    # Import adapter (will fail clearly if things not on PATH)
    from ledger.integrations import things3 as adapter

    if not adapter.things_available():
        print(
            "ledger loops sync: 'things' CLI not found on PATH.\n"
            "Install from: https://culturedcode.com/things/support/articles/2803573/",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Load loops
    from ledger.notes import get_notes

    notes_dir = cfg.ledger_notes_dir
    all_loops_raw = get_notes("loops", loop_status="all", notes_dir=notes_dir)

    # status/scope frontmatter are enums (LoopStatus/Scope); coerce to their
    # string value so comparisons and scope-routing lookups work.
    def _enum_val(v, default=""):
        return getattr(v, "value", v) if v is not None else default

    # Include only open + blocked loops in the desired set
    active_statuses = {"open", "blocked"}
    active_loops = [
        n for n in all_loops_raw
        if _enum_val(getattr(n.frontmatter, "status", None), "open") in active_statuses
    ]
    # Closed loops are not synced as tasks, but their slugs are needed so a
    # closed loop's task is completed rather than mistaken for a deleted one.
    closed_slugs = {
        n.path.stem for n in all_loops_raw
        if _enum_val(getattr(n.frontmatter, "status", None), "open") == "closed"
    }
    # Closing a loop archives it out of 05_open_loops, which get_notes() is the
    # only place that scans — so the set above misses exactly the loops that
    # were finished properly, and their tasks get flagged "[orphan]" instead of
    # completed. Anything still named loop__*.md in the archive is a retired
    # loop, which is all this needs to know.
    closed_slugs |= {
        p.stem for p in (Path(notes_dir) / ARCHIVE_SUBDIR).glob(f"{NOTE_LAYOUTS['loops'].prefix}*.md")
    }
    # Snoozed loops are neither active nor closed. Without their own set they
    # fall through to the orphan branch and get flagged as if deleted.
    snoozed_slugs = {
        n.path.stem for n in all_loops_raw
        if _enum_val(getattr(n.frontmatter, "status", None), "open") == "snoozed"
    }

    # The Frontmatter model only exposes known schema fields, so custom keys
    # (things_uuid, things_list_id) must be read from the raw YAML.
    from ledger.parsing.frontmatter import parse_frontmatter_text

    def _raw_fm(path) -> dict:
        try:
            meta, _ = parse_frontmatter_text(Path(path).read_text(encoding="utf-8"))
            return meta or {}
        except OSError:
            return {}

    loop_infos: list[LoopInfo] = []
    for ln in active_loops:
        fm = ln.frontmatter
        raw = _raw_fm(ln.path)
        loop_infos.append(LoopInfo(
            slug=ln.path.stem,
            path=str(ln.path),
            title=ln.title or ln.path.stem,
            status=_enum_val(getattr(fm, "status", None), "open"),
            scope=_enum_val(getattr(fm, "scope", None), ""),
            things_uuid=(raw.get("things_uuid") or None),
            updated=str(getattr(fm, "updated", "")),
            list_id=str(raw.get("things_list_id", "") or ""),
        ))

    # Read tasks from Things
    tasks = adapter.read_tasks(marker_prefix=cfg.things3_marker_prefix)

    # Reconcile
    actions = reconcile(
        loop_infos,
        tasks,
        marker_prefix=cfg.things3_marker_prefix,
        scope_routing=cfg.things3_scope_routing or {},
        default_project=cfg.things3_default_project,
        blocked_project=cfg.things3_blocked_project,
        completed_maps_to=cfg.things3_completed_maps_to,
        canceled_maps_to=cfg.things3_canceled_maps_to,
        orphan_action=cfg.things3_orphan_action,
        closed_slugs=closed_slugs,
        snoozed_slugs=snoozed_slugs,
    )

    # Apply actions
    stats = {"create": 0, "update": 0, "reverse": 0, "complete": 0,
             "cancel": 0, "orphan": 0, "noop": 0, "error": 0}

    for action in actions:
        try:
            if action.kind == "noop":
                stats["noop"] += 1
                continue

            elif action.kind == "create":
                new_uuid = adapter.create_task(
                    title=action.things_title or "",
                    notes=_build_task_notes(action.loop_path, action.things_notes or ""),
                    project=action.things_project or "",
                    dry_run=dry_run,
                    marker_prefix=cfg.things3_marker_prefix,
                )
                stats["create"] += 1
                if new_uuid and action.loop_path:
                    _write_things_uuid_to_loop(action.loop_path, new_uuid)
                elif dry_run:
                    print(f"  [dry-run] would write things_uuid to {action.loop_path}")

            elif action.kind == "update":
                adapter.update_task(
                    action.things_uuid,
                    title=action.things_title,
                    notes=action.things_notes,
                    dry_run=dry_run,
                )
                stats["update"] += 1
                if action.new_things_uuid and action.loop_path and not dry_run:
                    _write_things_uuid_to_loop(action.loop_path, action.new_things_uuid)

            elif action.kind in ("reverse_complete", "reverse_cancel"):
                stats["reverse"] += 1
                if action.loop_path and action.new_loop_status:
                    if dry_run:
                        print(
                            f"  [dry-run] would set status={action.new_loop_status} "
                            f"on {action.loop_path}"
                        )
                    else:
                        _write_loop_status(
                            action.loop_path,
                            action.new_loop_status,
                            action.new_things_uuid,
                        )

            elif action.kind == "forward_complete":
                stats["complete"] += 1
                adapter.complete_task(action.things_uuid, dry_run=dry_run)
                if dry_run:
                    print(
                        f"  [dry-run] would complete {action.things_uuid} "
                        f"({action.loop_slug} is closed)"
                    )

            elif action.kind == "forward_cancel":
                stats["cancel"] += 1
                adapter.cancel_task(action.things_uuid, dry_run=dry_run)
                if dry_run:
                    print(
                        f"  [dry-run] would cancel {action.things_uuid} "
                        f"({action.loop_slug} is snoozed)"
                    )

            elif action.kind == "orphan_cancel":
                stats["orphan"] += 1
                adapter.cancel_task(action.things_uuid, dry_run=dry_run)

            elif action.kind == "orphan_flag":
                stats["orphan"] += 1
                if dry_run:
                    print(f"  [dry-run] would flag orphan {action.things_uuid}")
                else:
                    task_data = adapter.get_task_by_uuid(action.things_uuid)
                    if task_data:
                        current_title = task_data.get("title", "")
                        if "[orphan]" not in current_title:
                            adapter.update_task(
                                action.things_uuid,
                                title=f"[orphan] {current_title}",
                            )

        except Exception as exc:
            stats["error"] += 1
            print(
                f"  ERROR ({action.kind} {action.things_uuid or action.loop_slug}): {exc}",
                file=sys.stderr,
            )

    dry_label = " (dry-run)" if dry_run else ""
    total = sum(stats.values())
    print(f"\nThings3 sync{dry_label}: {total} actions")
    for key, count in stats.items():
        if count:
            print(f"  {key:12s}: {count}")


def _build_task_notes(loop_path: str | None, marker: str) -> str:
    """Compose human-readable Things task notes from a loop note.

    The task notes become: the loop's markdown body (readable) + an absolute
    link back to the loop file + the ``ledger:<slug> status:<status>`` marker
    (kept as a machine reference / dedup key on its own trailing line so
    ``_parse_marker`` still finds it).  Falls back to just the marker if the
    file can't be read.
    """
    if not loop_path:
        return marker
    try:
        from ledger.parsing.frontmatter import parse_frontmatter_text
        text = Path(loop_path).read_text(encoding="utf-8")
        _, body = parse_frontmatter_text(text)
        body = (body or "").strip()
        link = f"file://{loop_path}"
        parts = [p for p in (body, f"🔗 {link}", marker) if p]
        return "\n\n".join(parts)
    except OSError:
        return marker


def _write_things_uuid_to_loop(loop_path: str, uuid: str) -> None:
    """Write things_uuid to a loop note's frontmatter."""
    from ledger.io.safe_write import safe_write_text
    from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter

    path = Path(loop_path)
    text = path.read_text(encoding="utf-8")
    fm_dict, body = parse_frontmatter_text(text)
    fm_dict["things_uuid"] = uuid
    new_text = serialize_frontmatter(fm_dict) + "\n" + body
    safe_write_text(path, new_text)


def _write_loop_status(loop_path: str, new_status: str, things_uuid: str | None) -> None:
    """Update status (and optionally things_uuid) in a loop note's frontmatter."""
    from ledger.io.safe_write import safe_write_text
    from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter

    path = Path(loop_path)
    text = path.read_text(encoding="utf-8")
    fm_dict, body = parse_frontmatter_text(text)
    fm_dict["status"] = new_status
    if things_uuid:
        fm_dict["things_uuid"] = things_uuid
    new_text = serialize_frontmatter(fm_dict) + "\n" + body
    safe_write_text(path, new_text)
