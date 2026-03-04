from __future__ import annotations

from pathlib import Path

from ledger.io import safe_write_text

from .models import ObsidianLedgerConfig


CANDIDATES_BASE = """filters:
  and:
    - 'file.path.startsWith("cognitive-ledger/notes/00_inbox/")'
properties:
  review_status:
    displayName: Review
  ledger_kind:
    displayName: Kind
  ledger_confidence:
    displayName: Confidence
  candidate_score:
    displayName: Score
  origin_path:
    displayName: Origin
  updated:
    displayName: Updated
views:
  - type: table
    name: Pending Candidates
    filters:
      and:
        - 'review_status == "pending"'
    order:
      - review_status
      - candidate_score
      - updated
"""


NOTES_BASE = """filters:
  and:
    - 'file.path.startsWith("cognitive-ledger/notes/02_facts/") || file.path.startsWith("cognitive-ledger/notes/03_preferences/") || file.path.startsWith("cognitive-ledger/notes/04_goals/") || file.path.startsWith("cognitive-ledger/notes/05_open_loops/") || file.path.startsWith("cognitive-ledger/notes/06_concepts/")'
properties:
  scope:
    displayName: Scope
  confidence:
    displayName: Confidence
  source:
    displayName: Source
  updated:
    displayName: Updated
views:
  - type: table
    name: Ledger Notes
    order:
      - updated
      - scope
      - confidence
"""


def write_bases(config: ObsidianLedgerConfig) -> list[Path]:
    candidates_path = config.bases_root / "ledger_candidates.base"
    notes_path = config.bases_root / "ledger_notes.base"
    safe_write_text(candidates_path, CANDIDATES_BASE)
    safe_write_text(notes_path, NOTES_BASE)
    return [candidates_path, notes_path]
