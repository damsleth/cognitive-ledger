"""``ledger --doctor`` - data-class health check per the CLI contract.

Schema matches the doctor JSON contract: tool, version, config_path,
data_path, models, findings[]. Exit codes follow the standard table
(0 ok, 1 user-fixable). Auth is not a ledger concern (no token surface).
"""

from __future__ import annotations

import json
import sys

from ledger.conventions import DoctorFinding, DoctorPayload


def run_doctor(config_path_arg: str | None = None, fix: bool = False) -> DoctorPayload:
  """Run all checks and return the structured payload. No printing.

  Args:
      config_path_arg: Optional path to config (unused, reserved for future).
      fix: If True, attempt to auto-repair safe fixable issues.
  """
  payload = DoctorPayload()

  try:
    from ledger.config import get_config
    cfg = get_config()
    payload.config_path = str(getattr(cfg, "config_path", "") or "")
    payload.data_path = str(getattr(cfg, "ledger_root", "") or "")
  except Exception as exc:
    payload.findings.append(DoctorFinding(
      id="config_unreadable",
      severity="error",
      message=f"Could not load ledger config: {exc}",
      hint="Run: ledger init",
    ))
    return payload

  from pathlib import Path

  try:
    ledger_root = Path(payload.data_path) if payload.data_path else None
    if ledger_root and not ledger_root.exists():
      payload.findings.append(DoctorFinding(
        id="ledger_root_missing",
        severity="warning",
        message=f"Ledger root does not exist: {ledger_root}",
        hint="Run: ledger init --root <path>",
      ))
  except Exception:
    pass

  # Check ledger notes subdirectory exists.
  try:
    notes_dir = getattr(cfg, "ledger_notes_dir", None)
    if notes_dir:
      notes_dir = Path(notes_dir)
      if not notes_dir.exists():
        finding = DoctorFinding(
          id="notes_subdir_missing",
          severity="warning",
          message=f"Ledger notes directory does not exist: {notes_dir}",
          hint="Run: ledger init to re-create directory structure",
          fixable=True,
        )
        payload.findings.append(finding)
        if fix:
          notes_dir.mkdir(parents=True, exist_ok=True)
          finding.fixed = True
          finding.fix_detail = f"Created {notes_dir}"
  except Exception:
    pass

  # Check the timeline: timeline.jsonl is the source of truth, timeline.md a
  # generated view of it.
  try:
    from ledger.layout import timeline_jsonl_path, timeline_path
    from ledger import timeline as timeline_lib
    notes_dir_p = getattr(cfg, "ledger_notes_dir", None)
    if notes_dir_p:
      notes_dir_p = Path(notes_dir_p)
      if notes_dir_p.exists():
        tl_path = timeline_path(notes_dir_p)
        jsonl_path = timeline_jsonl_path(notes_dir_p)
        if not jsonl_path.is_file() and not tl_path.is_file():
          finding = DoctorFinding(
            id="timeline_missing",
            severity="warning",
            message=f"Timeline missing: {jsonl_path}",
            hint="Run: ledger --doctor --fix (or ledger init)",
            fixable=True,
          )
          payload.findings.append(finding)
          if fix:
            from ledger.io.safe_write import safe_write_text
            tl_path.parent.mkdir(parents=True, exist_ok=True)
            safe_write_text(jsonl_path, "")
            safe_write_text(tl_path, timeline_lib.TIMELINE_MARKDOWN_HEADER)
            finding.fixed = True
            finding.fix_detail = f"Created {jsonl_path.name} and {tl_path.name}"
        elif not jsonl_path.is_file():
          finding = DoctorFinding(
            id="timeline_jsonl_missing",
            severity="warning",
            message="timeline.md exists but timeline.jsonl (the source of truth) does not",
            hint="Run: ledger --doctor --fix to migrate the markdown events into timeline.jsonl",
            fixable=True,
          )
          payload.findings.append(finding)
          if fix:
            events = timeline_lib.ensure_timeline_jsonl(tl_path, jsonl_path)
            finding.fixed = True
            finding.fix_detail = f"Migrated {len(events)} event(s) into {jsonl_path.name}"
        else:
          jsonl_errors, jsonl_warnings = timeline_lib.check_timeline_jsonl(jsonl_path)
          if jsonl_errors:
            payload.findings.append(DoctorFinding(
              id="timeline_jsonl_malformed",
              severity="error",
              message=f"{len(jsonl_errors)} malformed timeline.jsonl line(s); first: {jsonl_errors[0]}",
              hint="Fix the named line(s) by hand; these events are invisible to every timeline view",
            ))
          if jsonl_warnings:
            payload.findings.append(DoctorFinding(
              id="timeline_jsonl_out_of_order",
              severity="warning",
              message=f"{len(jsonl_warnings)} out-of-order timeline.jsonl event(s); first: {jsonl_warnings[0]}",
              hint="Usually clock skew between writers; the markdown view sorts, but ts-based readers may not",
            ))
          if not timeline_lib.timeline_markdown_is_current(jsonl_path, tl_path):
            finding = DoctorFinding(
              id="timeline_md_stale",
              severity="warning",
              message="timeline.md is missing or out of date with timeline.jsonl",
              hint="Run: ledger --doctor --fix to regenerate it",
              fixable=True,
            )
            payload.findings.append(finding)
            if fix:
              timeline_lib.regenerate_timeline_markdown(jsonl_path, tl_path)
              finding.fixed = True
              finding.fix_detail = f"Regenerated {tl_path.name} from {jsonl_path.name}"
  except Exception:
    pass

  # Check note index files.
  try:
    notes_dir_p = getattr(cfg, "ledger_notes_dir", None)
    if notes_dir_p:
      notes_dir_p = Path(notes_dir_p)
      if notes_dir_p.exists():
        index_dir = notes_dir_p / "08_indices"
        index_md = index_dir / "index.md"
        index_json = index_dir / "note_index.json"
        for idx_path, check_id in [
          (index_md, "note_index_missing"),
        ]:
          if not idx_path.is_file():
            finding = DoctorFinding(
              id=check_id,
              severity="info",
              message=f"Index file missing: {idx_path.name} (run: ledger sleep index)",
              hint="Run: ledger sleep index",
              fixable=False,
            )
            payload.findings.append(finding)
  except Exception:
    pass

  # Check semantic index (embeddings).
  try:
    notes_dir_p = getattr(cfg, "ledger_notes_dir", None)
    if notes_dir_p:
      notes_dir_p = Path(notes_dir_p)
      if notes_dir_p.exists():
        from ledger.embeddings import (
          configured_model_for_backend,
          semantic_index_path,
          semantic_vectors_path,
        )
        backend = str(getattr(cfg, "embed_backend", "local") or "local")
        model = configured_model_for_backend(backend)
        index_file = semantic_index_path("ledger", backend, model)
        vectors_file = semantic_vectors_path("ledger", backend, model)
        if not (index_file.is_file() and vectors_file.is_file()):
          payload.findings.append(DoctorFinding(
            id="semantic_index_missing",
            severity="info",
            message=f"Semantic embedding index not found for {backend}/{model} ({index_file.parent})",
            hint="Run: ledger embed build --target ledger",
          ))
  except Exception:
    pass

  # Check sentence-transformers availability.
  try:
    import importlib
    if importlib.util.find_spec("sentence_transformers") is None:
      payload.findings.append(DoctorFinding(
        id="sentence_transformers_missing",
        severity="info",
        message="sentence-transformers not installed; semantic search unavailable",
        hint="Run: ./scripts/setup-venv.sh --embeddings or pip install sentence-transformers",
      ))
  except Exception:
    pass

  # Check for accumulated lock files nobody holds. FileLock never unlinks on
  # release, so these pile up; only a lock a non-blocking flock proves unheld
  # may go, or a waiter and a newcomer could both believe they hold it.
  try:
    notes_dir_p = getattr(cfg, "ledger_notes_dir", None)
    if notes_dir_p:
      notes_dir_p = Path(notes_dir_p)
      if notes_dir_p.exists():
        from ledger.inbox import reap_unheld_locks

        unheld = reap_unheld_locks(notes_dir=notes_dir_p, apply=False)
        if unheld:
          finding = DoctorFinding(
            id="stale_lock_files",
            severity="warning",
            message=f"Found {len(unheld)} unheld lock file(s) in notes directory",
            hint="Run: ledger --doctor --fix (or ledger inbox cleanup --apply); held locks are left alone",
            fixable=True,
          )
          payload.findings.append(finding)
          if fix:
            removed = reap_unheld_locks(notes_dir=notes_dir_p, apply=True)
            finding.fixed = True
            finding.fix_detail = f"Removed {len(removed)} unheld lock file(s)"
  except Exception:
    pass

  # Check for private-fenced content that has leaked into note_index.json.
  try:
    notes_dir_p = getattr(cfg, "ledger_notes_dir", None)
    if notes_dir_p:
      notes_dir_p = Path(notes_dir_p)
      index_json = notes_dir_p / "08_indices" / "note_index.json"
      if index_json.is_file():
        raw = index_json.read_text(encoding="utf-8", errors="replace")
        lowered = raw.lower()
        if "```private" in lowered or "<private>" in lowered or "<!-- private" in lowered:
          payload.findings.append(DoctorFinding(
            id="private_fence_in_index",
            severity="error",
            message="note_index.json appears to contain private-fenced content",
            hint="Run: ledger sleep index to rebuild the index with fences stripped",
          ))
  except Exception:
    pass

  # Embedding-model availability (shallow - never download).
  try:
    embed_model = getattr(cfg, "embed_model", None)
    if embed_model:
      payload.models = {"embedding": {"configured": embed_model}}
  except Exception:
    pass

  # Redaction sentinel smoke test.
  try:
    from ledger.conventions import redact
    sentinel = "CANARY_SECRET_xxxx"
    jwt_like = "eyJalg.payload-" + sentinel + ".sig-padding-123"
    out = redact(f"Bearer {jwt_like}")
    if sentinel in out:
      payload.findings.append(DoctorFinding(
        id="redact_sentinel_leak",
        severity="error",
        message="Redaction sentinel leaked through redact()",
        hint="redact() is not catching expected patterns",
      ))
  except Exception as exc:
    payload.findings.append(DoctorFinding(
      id="redact_unavailable",
      severity="error",
      message=f"redact() is not callable: {exc}",
    ))

  return payload


def apply_fixes(payload: DoctorPayload) -> list[str]:
  """Return descriptions of fixes that were applied (already done in run_doctor with fix=True)."""
  fixed = []
  for f in payload.findings:
    if getattr(f, "fixed", False):
      fixed.append(f.fix_detail or f.id)
  return fixed


def _print_human(payload: DoctorPayload, fix: bool = False) -> None:
  data = payload.to_dict()
  print(f"ledger doctor (v{data['version']})")
  if payload.config_path:
    print(f"  config: {payload.config_path}")
  if payload.data_path:
    print(f"  root:   {payload.data_path}")
  if payload.models:
    print(f"  models: {payload.models}")
  if not payload.findings:
    print("  status: ok")
    return
  print(f"  findings: {len(payload.findings)}")
  for f in payload.findings:
    marker = {"error": "x", "warning": "!", "info": "."}.get(f.severity, ".")
    fixed_tag = " [fixed]" if getattr(f, "fixed", False) else ""
    print(f"    {marker} [{f.severity}] {f.id}: {f.message}{fixed_tag}")
    if getattr(f, "fix_detail", None):
      print(f"        fixed: {f.fix_detail}")
    elif f.hint:
      print(f"        hint: {f.hint}")


def emit_doctor(config_path: str | None, as_json: bool, fix: bool = False) -> int:
  payload = run_doctor(config_path, fix=fix)
  if as_json:
    sys.stdout.write(json.dumps(payload.to_dict(), ensure_ascii=False) + "\n")
    sys.stdout.flush()
  else:
    _print_human(payload, fix=fix)
  return payload.exit_code()
