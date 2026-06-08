"""Interactive batch triage UI for the inbox (Plan 37, Phase B).

A plain stdout/stdin prompt loop (no curses) that renders the inbox as a
single-screen table and applies bulk actions at quit time. An optional
``--fzf`` backend pipes the table to ``fzf --multi`` for reject-by-selection.

Data ops live in ``ledger.inbox``; this module owns only the UI and the
command parser.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ledger.config import get_config
from ledger.inbox import (
    InboxCandidate,
    TriageAction,
    apply_actions,
    load_candidates_for_triage,
)
from ledger.layout import resolve_path
from ledger.validation import validate_note_type


_ACTION_MARKERS: dict[str, str] = {
    "accept": "A",
    "reject": "R",
    "merge": "M",
    "defer": "D",
    "skip": "S",
}

_HELP_TEXT = """\
Commands:
  a <range>[:type]  accept (promote). Optional type override, e.g. a 1:preferences
  r <range>         reject (logs rejection signature + deletes)
  m <idx>           merge into the candidate's merge_with: target
  d <idx>           defer (leave in inbox)
  s <idx>           skip (no-op, leave in inbox)
  i <idx>           inspect: print the candidate body
  p <idx>           inspect (alias for i)
  u <range>         unset a queued action
  ?                 show this help
  q                 quit, applying queued actions
  Q                 quit, discarding queued actions

Range syntax: 5-12 (inclusive), 5- (5 to end), comma-mixed (1,3-5,8).
Latest decision per row wins."""


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

def _parse_range(spec: str, count: int) -> list[int]:
    """Parse a 1-based index range/list spec into a sorted unique list.

    Raises ValueError on any invalid token (no silent partial application).
    """
    if not spec.strip():
        raise ValueError("missing index")

    indices: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            raise ValueError(f"empty index in {spec!r}")
        if "-" in token:
            lo_s, _, hi_s = token.partition("-")
            lo_s = lo_s.strip()
            hi_s = hi_s.strip()
            if not lo_s:
                raise ValueError(f"invalid range: {token!r}")
            try:
                lo = int(lo_s)
            except ValueError as exc:
                raise ValueError(f"invalid index: {lo_s!r}") from exc
            if not hi_s:
                hi = count
            else:
                try:
                    hi = int(hi_s)
                except ValueError as exc:
                    raise ValueError(f"invalid index: {hi_s!r}") from exc
            if lo > hi:
                raise ValueError(f"reversed range: {token!r}")
            for i in range(lo, hi + 1):
                indices.add(i)
        else:
            try:
                i = int(token)
            except ValueError as exc:
                raise ValueError(f"invalid index: {token!r}") from exc
            indices.add(i)

    for i in indices:
        if i < 1 or i > count:
            raise ValueError(f"index out of range: {i} (1-{count})")

    return sorted(indices)


def _merge_target_for(candidate: InboxCandidate) -> Path:
    """Resolve a candidate's ``merge_with:`` hint to an absolute path."""
    if not candidate.merge_with:
        raise ValueError("no merge target")
    config = get_config()
    return resolve_path(
        candidate.merge_with,
        ledger_root=config.ledger_root,
        ledger_notes_dir=config.ledger_notes_dir,
    )


def _parse_command(line: str, candidates: list[InboxCandidate]) -> list[TriageAction]:
    """Parse one command line into a list of TriageAction.

    Raises ValueError on any malformed command or invalid index/type.
    Inspect/help/quit verbs are handled by the loop, not here.
    """
    count = len(candidates)
    parts = line.split(None, 1)
    cmd = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "a":
        spec, _, type_override = arg.partition(":")
        target_type: str | None = None
        if type_override.strip():
            target_type = validate_note_type(type_override.strip())
        rows = _parse_range(spec, count)
        return [
            TriageAction(row=r, action="accept", target_type=target_type)
            for r in rows
        ]

    if cmd == "r":
        rows = _parse_range(arg, count)
        return [TriageAction(row=r, action="reject") for r in rows]

    if cmd in ("d", "s"):
        rows = _parse_range(arg, count)
        action = "defer" if cmd == "d" else "skip"
        return [TriageAction(row=r, action=action) for r in rows]  # type: ignore[arg-type]

    if cmd == "m":
        rows = _parse_range(arg, count)
        actions: list[TriageAction] = []
        for r in rows:
            candidate = candidates[r - 1]
            if not candidate.merge_with:
                raise ValueError(f"#{r}: no merge target")
            actions.append(
                TriageAction(
                    row=r,
                    action="merge",
                    target_note=_merge_target_for(candidate),
                )
            )
        return actions

    raise ValueError(f"unknown command: {cmd!r}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_table(
    candidates: list[InboxCandidate],
    actions: dict[int, TriageAction],
) -> None:
    n_yaams = sum(1 for c in candidates if c.promoted_by == "yaams")
    n_manual = len(candidates) - n_yaams
    print(f"\nInbox ({n_yaams} candidates from yaams + {n_manual} manual):")

    width = shutil.get_terminal_size((100, 24)).columns
    # Fixed columns: idx(3) act(3) type(12) conf(4) sig(8) + separators.
    fixed = 3 + 3 + 12 + 4 + 8 + 5 * 3
    title_w = max(12, width - fixed)

    header = (
        f"{'#':>3} | {'act':<3} | {'type':<12} | "
        f"{'title':<{title_w}} | {'conf':<4} | {'sig':<8}"
    )
    print(header)
    print("-" * min(width, len(header)))

    for idx, c in enumerate(candidates, 1):
        action = actions.get(idx)
        marker = _ACTION_MARKERS.get(action.action, "") if action else ""
        title = c.title
        if c.merge_with:
            title = f"{title} [merge?]"
        if len(title) > title_w:
            title = title[: title_w - 1] + "…"
        sig = (c.signature or "")[:8]
        print(
            f"{idx:>3} | {marker:<3} | {c.type:<12} | "
            f"{title:<{title_w}} | {c.confidence:<4.1f} | {sig:<8}"
        )


def _print_help() -> None:
    print(_HELP_TEXT)


def _print_summary(summary: dict) -> None:
    print("\nApplied:")
    print(f"  accepted: {summary['accepted']}")
    print(f"  rejected: {summary['rejected']}")
    print(f"  merged:   {summary['merged']}")
    print(f"  deferred: {summary['deferred']}")
    print(f"  skipped:  {summary['skipped']}")
    print(f"  failed:   {summary['failed']}")
    for path in summary.get("accepted_paths", []):
        print(f"  -> {path}")
    for backup in summary.get("backups", []):
        print(f"  backup: {backup}")
    for err in summary.get("errors", []):
        print(f"  ! {err}")
    print("\nRun `ledger sleep index` to refresh the tier-2 index.")


def _confirm_merges(
    candidates: list[InboxCandidate],
    actions: dict[int, TriageAction],
) -> bool:
    """If any merge is queued, show a summary and require a 'y'."""
    merges = [(r, a) for r, a in actions.items() if a.action == "merge"]
    if not merges:
        return True
    print("\nThe following merges will edit existing promoted notes:")
    for row, action in sorted(merges):
        candidate = candidates[row - 1]
        print(f"  #{row}: {candidate.filename} -> {action.target_note}")
    try:
        reply = input("Apply these merges? [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    return reply == "y"


def _preview_accepts(
    candidates: list[InboxCandidate],
    actions: dict[int, TriageAction],
) -> None:
    """Show destination paths for accepts (esp. with type override)."""
    config = get_config()
    accepts = [(r, a) for r, a in actions.items() if a.action == "accept"]
    if not accepts:
        return
    print("\nAccepts will be promoted to:")
    for row, action in sorted(accepts):
        candidate = candidates[row - 1]
        target_type = action.target_type or candidate.type
        try:
            dest_dir = config.note_types[target_type]["dir"]
        except KeyError:
            dest_dir = target_type
        print(f"  #{row}: {candidate.filename} -> {dest_dir}/")


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------

def run_interactive_triage(notes_dir: Path | None = None) -> int:
    candidates = load_candidates_for_triage(notes_dir)
    if not candidates:
        print("Inbox is empty.")
        return 0

    actions: dict[int, TriageAction] = {}  # row -> action

    while True:
        _render_table(candidates, actions)
        try:
            line = input("> ").strip()
        except EOFError:
            line = "Q"

        if not line:
            continue
        if line == "q":
            _preview_accepts(candidates, actions)
            if not _confirm_merges(candidates, actions):
                print("Aborted (merges not confirmed).")
                continue
            summary = apply_actions(
                candidates, list(actions.values()), notes_dir=notes_dir
            )
            _print_summary(summary)
            return 0
        if line == "Q":
            print("Discarded.")
            return 0
        if line == "?":
            _print_help()
            continue

        verb = line.split(None, 1)[0]
        if verb in ("i", "p"):
            _handle_inspect(line, candidates)
            continue
        if verb == "u":
            _handle_unset(line, candidates, actions)
            continue

        try:
            new_actions = _parse_command(line, candidates)
        except ValueError as exc:
            print(f"error: {exc}")
            continue
        for action in new_actions:
            actions[action.row] = action  # latest decision wins


def _handle_inspect(line: str, candidates: list[InboxCandidate]) -> None:
    parts = line.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        rows = _parse_range(arg, len(candidates))
    except ValueError as exc:
        print(f"error: {exc}")
        return
    for row in rows:
        candidate = candidates[row - 1]
        print(f"\n--- #{row}: {candidate.filename} ---")
        print(candidate.body.strip())


def _handle_unset(
    line: str,
    candidates: list[InboxCandidate],
    actions: dict[int, TriageAction],
) -> None:
    parts = line.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        rows = _parse_range(arg, len(candidates))
    except ValueError as exc:
        print(f"error: {exc}")
        return
    for row in rows:
        actions.pop(row, None)


# ---------------------------------------------------------------------------
# Optional fzf backend
# ---------------------------------------------------------------------------

def run_fzf_triage(notes_dir: Path | None = None) -> int:
    if shutil.which("fzf") is None:
        print("warning: fzf not found on PATH; falling back to prompt loop.")
        return run_interactive_triage(notes_dir)

    candidates = load_candidates_for_triage(notes_dir)
    if not candidates:
        print("Inbox is empty.")
        return 0

    lines = [
        f"{i:3d} | {c.type:10s} | {c.title:60s} | {c.confidence:.1f}"
        for i, c in enumerate(candidates, 1)
    ]
    proc = subprocess.run(
        ["fzf", "--multi", "--prompt", "Reject> "],
        input="\n".join(lines),
        capture_output=True,
        text=True,
    )
    selected = proc.stdout.strip().splitlines()
    rejected_rows = {int(s.split(" | ")[0].strip()) for s in selected if s.strip()}

    actions: list[TriageAction] = []
    for i in range(1, len(candidates) + 1):
        if i in rejected_rows:
            actions.append(TriageAction(row=i, action="reject"))
        else:
            actions.append(TriageAction(row=i, action="accept"))

    summary = apply_actions(candidates, actions, notes_dir=notes_dir)
    _print_summary(summary)
    return 0
