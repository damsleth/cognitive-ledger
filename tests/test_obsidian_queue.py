from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ledger.obsidian.cli import main as obsidian_main
from ledger.obsidian.config import load_config
from ledger.obsidian.importer import run_import
from ledger.obsidian.queue import sync_queue
from ledger.parsing import parse_frontmatter_text


def _make_vault(tmp: Path) -> Path:
  vault = tmp / "vault"
  vault.mkdir(parents=True)
  obsidian_dir = vault / ".obsidian"
  obsidian_dir.mkdir()
  (obsidian_dir / "core-plugins.json").write_text(
    json.dumps({"bases": True}), encoding="utf-8"
  )
  return vault


def _init_vault(vault: Path) -> None:
  obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])


def _write_candidate_file(inbox: Path, name: str, frontmatter: dict, body: str) -> Path:
  lines = ["---"]
  for key, value in frontmatter.items():
    if isinstance(value, list):
      lines.append(f"{key}:")
      for item in value:
        lines.append(f"  - {item}")
    else:
      lines.append(f"{key}: {value}")
  lines.append("---")
  lines.append("")
  lines.append(body)

  path = inbox / name
  path.write_text("\n".join(lines), encoding="utf-8")
  return path


class TestQueueFileCreationAndReading(unittest.TestCase):
  def test_import_creates_candidate_files_in_inbox(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      source = vault / "04-dev" / "tasks.md"
      source.parent.mkdir(parents=True, exist_ok=True)
      source.write_text(
        "# CI Stabilization\n\n"
        "Open question: we need to decide how to stabilize CI before release.\n"
        "- [ ] Investigate flaky CI integration tests across environments.\n",
        encoding="utf-8",
      )

      config = load_config(vault)
      result = run_import(config)

      self.assertGreaterEqual(result.queue_created, 1)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      candidates = list(inbox.glob("candidate__*.md"))
      self.assertTrue(candidates, "expected candidate files in inbox")

      for cpath in candidates:
        text = cpath.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter_text(text)
        self.assertIn("review_status", fm)
        self.assertEqual(str(fm["review_status"]).strip().lower(), "pending")

  def test_sync_queue_returns_pending_count_for_unreviewed_candidates(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      _write_candidate_file(inbox, "candidate__test_a.md", {
        "review_status": "pending",
        "ledger_kind": "fact",
        "confidence": 0.8,
        "scope": "dev",
        "lang": "en",
      }, "# Candidate: Test fact A\n\n## Statement\n\nThis is a test fact.")

      _write_candidate_file(inbox, "candidate__test_b.md", {
        "review_status": "pending",
        "ledger_kind": "pref",
        "confidence": 0.9,
        "scope": "personal",
        "lang": "en",
      }, "# Candidate: Test pref B\n\n## Statement\n\nPrefer dark mode.")

      config = load_config(vault)
      result = sync_queue(config)

      self.assertEqual(result["pending"], 2)
      self.assertEqual(result["promoted"], 0)
      self.assertEqual(result["rejected"], 0)


class TestQueueSyncWithPendingItems(unittest.TestCase):
  def test_approved_candidate_gets_promoted(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      _write_candidate_file(inbox, "candidate__promote_me.md", {
        "review_status": "approved",
        "ledger_kind": "fact",
        "confidence": 0.85,
        "scope": "dev",
        "lang": "en",
        "origin_path": "04-dev/workflow.md",
      }, "# Candidate: Promote me\n\n## Statement\n\nThis fact should be promoted.")

      config = load_config(vault)
      result = sync_queue(config)

      self.assertEqual(result["promoted"], 1)

      text = (inbox / "candidate__promote_me.md").read_text(encoding="utf-8")
      fm, _ = parse_frontmatter_text(text)
      self.assertEqual(str(fm["review_status"]).strip().lower(), "promoted")
      self.assertTrue(fm.get("promoted_path"))

      promoted_rel = str(fm["promoted_path"])
      promoted_abs = vault / promoted_rel
      self.assertTrue(promoted_abs.exists(), f"promoted note missing: {promoted_abs}")

  def test_rejected_candidate_is_counted_but_not_promoted(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      _write_candidate_file(inbox, "candidate__reject_me.md", {
        "review_status": "rejected",
        "ledger_kind": "fact",
        "confidence": 0.5,
        "scope": "dev",
        "lang": "en",
      }, "# Candidate: Reject me\n\n## Statement\n\nNot worth promoting.")

      config = load_config(vault)
      result = sync_queue(config)

      self.assertEqual(result["rejected"], 1)
      self.assertEqual(result["promoted"], 0)

  def test_mixed_queue_counts_are_correct(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      _write_candidate_file(inbox, "candidate__a_pending.md", {
        "review_status": "pending",
        "ledger_kind": "fact",
        "confidence": 0.8,
      }, "# Candidate: A\n\n## Statement\n\nPending fact.")

      _write_candidate_file(inbox, "candidate__b_approved.md", {
        "review_status": "approved",
        "ledger_kind": "pref",
        "confidence": 0.9,
        "scope": "dev",
        "lang": "en",
        "origin_path": "workflow.md",
      }, "# Candidate: B\n\n## Statement\n\nApproved pref.")

      _write_candidate_file(inbox, "candidate__c_rejected.md", {
        "review_status": "rejected",
        "ledger_kind": "goal",
        "confidence": 0.6,
      }, "# Candidate: C\n\n## Statement\n\nRejected goal.")

      config = load_config(vault)
      result = sync_queue(config)

      self.assertEqual(result["pending"], 1)
      self.assertEqual(result["promoted"], 1)
      self.assertEqual(result["rejected"], 1)


class TestQueueStateTransitions(unittest.TestCase):
  def test_pending_to_promoted_to_done(self):
    """Full lifecycle: pending -> approved -> promoted (idempotent re-sync)."""
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      cpath = _write_candidate_file(inbox, "candidate__lifecycle.md", {
        "review_status": "pending",
        "ledger_kind": "concept",
        "confidence": 0.8,
        "scope": "dev",
        "lang": "en",
        "origin_path": "concepts/lifecycle.md",
      }, "# Candidate: Lifecycle test\n\n## Statement\n\nA concept for lifecycle testing.")

      config = load_config(vault)

      # Phase 1: pending - not promoted
      r1 = sync_queue(config)
      self.assertEqual(r1["pending"], 1)
      self.assertEqual(r1["promoted"], 0)

      # Phase 2: approve the candidate
      text = cpath.read_text(encoding="utf-8")
      text = text.replace("review_status: pending", "review_status: approved")
      cpath.write_text(text, encoding="utf-8")

      r2 = sync_queue(config)
      self.assertEqual(r2["promoted"], 1)

      fm, _ = parse_frontmatter_text(cpath.read_text(encoding="utf-8"))
      self.assertEqual(str(fm["review_status"]).strip().lower(), "promoted")
      promoted_path = str(fm["promoted_path"])
      self.assertTrue((vault / promoted_path).exists())

      # Phase 3: re-sync is idempotent - already promoted, skip
      r3 = sync_queue(config)
      self.assertEqual(r3["promoted"], 0)
      self.assertEqual(r3["pending"], 0)

  def test_promoted_candidate_updates_timeline(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      _write_candidate_file(inbox, "candidate__timeline_check.md", {
        "review_status": "approved",
        "ledger_kind": "goal",
        "confidence": 0.9,
        "scope": "personal",
        "lang": "en",
        "origin_path": "goals/my_goal.md",
      }, "# Candidate: Timeline check\n\n## Statement\n\nGoal for timeline verification.")

      config = load_config(vault)
      sync_queue(config)

      timeline = config.timeline_path.read_text(encoding="utf-8")
      self.assertIn("promoted from candidate queue", timeline)
      self.assertIn("candidate promoted", timeline)

  def test_candidate_with_invalid_kind_stays_pending(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      _write_candidate_file(inbox, "candidate__bad_kind.md", {
        "review_status": "approved",
        "ledger_kind": "invalid_type",
        "confidence": 0.8,
        "scope": "dev",
        "lang": "en",
      }, "# Candidate: Bad kind\n\n## Statement\n\nThis has an invalid kind.")

      config = load_config(vault)
      result = sync_queue(config)

      # Invalid kind causes it to be counted as pending, not promoted
      self.assertEqual(result["promoted"], 0)
      self.assertEqual(result["pending"], 1)

  def test_candidate_without_statement_stays_pending(self):
    with TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      vault = _make_vault(tmp_path)
      _init_vault(vault)

      inbox = vault / "cognitive-ledger" / "notes" / "00_inbox"
      inbox.mkdir(parents=True, exist_ok=True)

      _write_candidate_file(inbox, "candidate__no_statement.md", {
        "review_status": "approved",
        "ledger_kind": "fact",
        "confidence": 0.8,
        "scope": "dev",
        "lang": "en",
      }, "")

      config = load_config(vault)
      result = sync_queue(config)

      self.assertEqual(result["promoted"], 0)
      self.assertEqual(result["pending"], 1)


if __name__ == "__main__":
  unittest.main()
