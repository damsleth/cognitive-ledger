"""cognitive-ledger binding to the shared hugr CLI contract.

The wire contract (action/error envelopes, NDJSON streaming, the
doctor payload shape, the 0-5 exit-code taxonomy, redact()) lives in
the ``hugr-conventions`` package - the executable form of
CONVENTIONS.md in the hugr repo. This module binds it to the ledger
tool name and version. ``ledger``, ``ledger-obsidian``, and ``sheep``
all import from here; the ``tool=`` override on the envelope helpers
lets ``sheep`` stamp its own name onto otherwise-shared plumbing.

See https://github.com/damsleth/hugr/blob/main/CONVENTIONS.md.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import hugr_conventions as _hc
from hugr_conventions import (  # re-export: identical wire shapes
  EXIT_AUTH,
  EXIT_NOT_FOUND,
  EXIT_OK,
  EXIT_PARTIAL,
  EXIT_TRANSIENT,
  EXIT_USER_ERROR,
  DoctorFinding,
  emit_action,
  emit_data_error,
  redact,
  stream_progress,
  stream_result,
  stream_warning,
)

__all__ = [
  "EXIT_OK",
  "EXIT_USER_ERROR",
  "EXIT_TRANSIENT",
  "EXIT_AUTH",
  "EXIT_NOT_FOUND",
  "EXIT_PARTIAL",
  "TOOL_NAME",
  "redact",
  "action_envelope",
  "emit_action",
  "data_error",
  "emit_data_error",
  "stream_progress",
  "stream_warning",
  "stream_result",
  "DoctorFinding",
  "DoctorPayload",
]

TOOL_NAME = "ledger"


def _version() -> str:
  # Prefer the in-tree __version__: it tracks the code that's running,
  # not whatever wheel metadata pip happened to install (which can drift
  # behind editable-install source - the doctor output then lies about
  # which version is actually executing).
  try:
    from ledger import __version__ as _v
    return _v
  except Exception:
    pass
  try:
    import importlib.metadata as _md
    return _md.version("cognitive-ledger")
  except Exception:
    return "0.0.0"


def action_envelope(
  *,
  command: str,
  ok: bool,
  stats: Mapping[str, Any] | None = None,
  warnings: Iterable[str] | None = None,
  error: Mapping[str, Any] | None = None,
  duration_ms: float | None = None,
  tool: str | None = None,
) -> dict[str, Any]:
  return _hc.action_envelope(
    tool=tool or TOOL_NAME,
    version=_version,
    command=command,
    ok=ok,
    stats=stats,
    warnings=warnings,
    error=error,
    duration_ms=duration_ms,
  )


def data_error(
  *,
  command: str,
  code: str,
  message: str,
  hint: str | None = None,
  tool: str | None = None,
) -> dict[str, Any]:
  return _hc.data_error(
    tool=tool or TOOL_NAME,
    version=_version,
    command=command,
    code=code,
    message=message,
    hint=hint,
  )


def DoctorPayload(**kwargs: Any) -> _hc.DoctorPayload:  # noqa: N802 - preserves call site
  """ledger-bound :class:`hugr_conventions.DoctorPayload`.

  Defaults ``tool`` to ``"ledger"`` and ``version`` to the live
  in-tree version so existing call sites construct it unchanged.
  """
  kwargs.setdefault("tool", TOOL_NAME)
  kwargs.setdefault("version", _version)
  return _hc.DoctorPayload(**kwargs)
