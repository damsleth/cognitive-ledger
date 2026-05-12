"""Reserved-key contract regression for ledger data commands.

Per mnem CONVENTIONS.md, data-class success documents MUST NOT
contain a top-level `ok` key. The key is reserved as the error-vs-
success discriminator for JSON-aware callers.

This file pins that contract for the JSON shapes consumed by mnem
query, mnem ledger paths, etc.
"""
from __future__ import annotations

from ledger.query import query_result_to_json


def test_query_result_to_json_has_no_top_level_ok():
  payload = {
    "query": "anything",
    "scope": "all",
    "retrieval_mode": "legacy",
    "results": [],
  }
  out = query_result_to_json(payload, view="context")
  assert "ok" not in out, (
    "ledger query JSON success output has a top-level `ok` key, "
    "which is reserved as the data-class error discriminator per "
    "mnem CONVENTIONS.md. Move it under a non-reserved key."
  )


def test_query_result_to_json_with_bundle_has_no_top_level_ok():
  payload = {
    "query": "x",
    "scope": "all",
    "retrieval_mode": "legacy",
    "results": [],
  }
  out = query_result_to_json(payload, view="context", include_bundle=True)
  assert "ok" not in out
