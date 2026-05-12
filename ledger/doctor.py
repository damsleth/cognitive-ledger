"""``ledger --doctor`` - data-class health check per mnem CONVENTIONS.md.

Schema matches the doctor JSON contract: tool, version, config_path,
data_path, models, findings[]. Exit codes follow the standard table
(0 ok, 1 user-fixable). Auth is not a ledger concern (no token surface).
"""

from __future__ import annotations

import json
import sys

from ledger.conventions import DoctorFinding, DoctorPayload


def run_doctor(config_path_arg: str | None = None) -> DoctorPayload:
  """Run all checks and return the structured payload. No printing."""
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

  try:
    from pathlib import Path
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


def _print_human(payload: DoctorPayload) -> None:
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
    print(f"    {marker} [{f.severity}] {f.id}: {f.message}")
    if f.hint:
      print(f"        hint: {f.hint}")


def emit_doctor(config_path: str | None, as_json: bool) -> int:
  payload = run_doctor(config_path)
  if as_json:
    sys.stdout.write(json.dumps(payload.to_dict(), ensure_ascii=False) + "\n")
    sys.stdout.flush()
  else:
    _print_human(payload)
  return payload.exit_code()
