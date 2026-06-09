"""Tests for the CLI contract helpers in ledger/conventions.py."""
from __future__ import annotations

import io
import json

from ledger.conventions import (
  DoctorFinding,
  DoctorPayload,
  EXIT_OK,
  EXIT_PARTIAL,
  EXIT_USER_ERROR,
  action_envelope,
  data_error,
  emit_action,
  emit_data_error,
  redact,
  stream_progress,
  stream_result,
  stream_warning,
)


def test_redact_jwt_like():
  jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
  out = redact(f"token={jwt}")
  assert jwt not in out
  assert "<redacted-jwt>" in out


def test_redact_bearer():
  out = redact("Authorization: Bearer abc123def456")
  assert "abc123def456" not in out
  assert "Bearer <redacted>" in out


def test_redact_token_field():
  payload = '{"access_token":"xyz","refresh_token":"qrs","other":"keep"}'
  out = redact(payload)
  assert "xyz" not in out
  assert "qrs" not in out
  assert "keep" in out


def test_redact_body_field():
  payload = '{"body":"secret content here","subject":"ok"}'
  out = redact(payload)
  assert "secret content here" not in out
  assert '"body":"<redacted>"' in out


def test_redact_handles_non_string():
  assert redact(None) == ""


def test_redact_is_idempotent():
  jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
  once = redact(f"x={jwt}")
  assert once == redact(once)


def test_redaction_sentinel_does_not_leak():
  jwt = "eyJfake." + "CANARY_SECRET_xxxx" + "." + "padding1234"
  out = redact(f"Authorization: Bearer {jwt}")
  assert "CANARY_SECRET_xxxx" not in out


def test_action_envelope_shape_success():
  env = action_envelope(command="init", ok=True, stats={"created": True}, duration_ms=12.5)
  assert env["tool"] == "ledger"
  assert env["command"] == "init"
  assert env["ok"] is True
  assert env["duration_ms"] == 12.5
  assert env["stats"] == {"created": True}
  assert env["warnings"] == []
  assert env["error"] is None


def test_action_envelope_failure_has_error_object():
  env = action_envelope(
    command="init", ok=False,
    error={"code": "x", "message": "boom"},
  )
  assert env["ok"] is False
  assert env["error"]["code"] == "x"


def test_action_envelope_tool_override():
  # Used so sheep emits its own name.
  env = action_envelope(command="status", ok=True, tool="sheep")
  assert env["tool"] == "sheep"


def test_emit_action_one_line():
  buf = io.StringIO()
  emit_action(action_envelope(command="init", ok=True), stream=buf)
  out = buf.getvalue()
  assert out.endswith("\n")
  assert out.count("\n") == 1
  json.loads(out)


def test_data_error_shape():
  err = data_error(command="paths", code="missing", message="m", hint="run init")
  assert err["tool"] == "ledger"
  assert err["ok"] is False
  assert err["error"]["hint"] == "run init"


def test_emit_data_error_one_line():
  buf = io.StringIO()
  emit_data_error(data_error(command="x", code="c", message="m"), stream=buf)
  payload = json.loads(buf.getvalue())
  assert payload["ok"] is False


def test_stream_progress_schema():
  buf = io.StringIO()
  stream_progress(source="notes", stage="index", done=10, total=100, stream=buf)
  payload = json.loads(buf.getvalue())
  assert payload["type"] == "progress"
  assert payload["source"] == "notes"
  assert payload["done"] == 10
  assert "ts" in payload


def test_stream_warning_redacts():
  buf = io.StringIO()
  stream_warning("Bearer secrettoken in auth header", stream=buf)
  payload = json.loads(buf.getvalue())
  assert "secrettoken" not in payload["message"]


def test_stream_result_envelope():
  buf = io.StringIO()
  stream_result(action_envelope(command="ingest", ok=True), stream=buf)
  payload = json.loads(buf.getvalue())
  assert payload["type"] == "result"
  assert payload["command"] == "ingest"


def test_doctor_payload_minimal():
  d = DoctorPayload().to_dict()
  assert d["tool"] == "ledger"
  assert d["findings"] == []


def test_doctor_findings_serialize():
  d = DoctorPayload(
    config_path="/tmp/cfg.yaml",
    findings=[
      DoctorFinding(id="x", severity="error", message="bad", hint="fix it"),
    ],
  )
  out = d.to_dict()
  assert out["config_path"] == "/tmp/cfg.yaml"
  assert out["findings"][0]["severity"] == "error"
  assert out["findings"][0]["hint"] == "fix it"


def test_doctor_exit_code_error():
  d = DoctorPayload(findings=[DoctorFinding(id="x", severity="error", message="m")])
  assert d.exit_code() == EXIT_USER_ERROR


def test_doctor_exit_code_clean():
  assert DoctorPayload().exit_code() == EXIT_OK


def test_exit_constants():
  assert EXIT_OK == 0
  assert EXIT_USER_ERROR == 1
  assert EXIT_PARTIAL == 5
