"""Tests for ledger/integrations/things3_sync.py — pure reconciliation engine."""

from __future__ import annotations

import unittest

from ledger.integrations.things3_sync import Action, LoopInfo, reconcile, _parse_marker, _make_marker


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _loop(
    slug="loop__test",
    title="Test Loop",
    status="open",
    scope="dev",
    things_uuid=None,
    updated="2026-01-01T00:00:00Z",
    path="/notes/loop__test.md",
):
    return LoopInfo(
        slug=slug,
        path=path,
        title=title,
        status=status,
        scope=scope,
        things_uuid=things_uuid,
        updated=updated,
    )


def _task(
    uuid="t1",
    title="Test Loop",
    notes="ledger:loop__test status:open",
    status="inbox",
):
    return {
        "uuid": uuid,
        "title": title,
        "notes": notes,
        "status": status,
    }


# ---------------------------------------------------------------------------
# _parse_marker
# ---------------------------------------------------------------------------

class TestParseMarker(unittest.TestCase):
    def test_basic_parse(self):
        result = _parse_marker("ledger:loop__fix_login status:open")
        self.assertEqual(result, ("loop__fix_login", "open"))

    def test_embedded_in_longer_notes(self):
        notes = "Some description\nledger:loop__foo status:blocked\nmore text"
        result = _parse_marker(notes)
        self.assertEqual(result, ("loop__foo", "blocked"))

    def test_returns_none_when_no_marker(self):
        self.assertIsNone(_parse_marker("no marker here"))
        self.assertIsNone(_parse_marker(""))
        self.assertIsNone(_parse_marker(None))

    def test_custom_prefix(self):
        result = _parse_marker("cl:loop__x status:open", prefix="cl:")
        self.assertEqual(result, ("loop__x", "open"))

    def test_slug_without_status(self):
        result = _parse_marker("ledger:loop__bare")
        self.assertIsNotNone(result)
        slug, status = result
        self.assertEqual(slug, "loop__bare")
        self.assertEqual(status, "")  # no status part


# ---------------------------------------------------------------------------
# _make_marker
# ---------------------------------------------------------------------------

class TestMakeMarker(unittest.TestCase):
    def test_default_prefix(self):
        m = _make_marker("loop__foo", "open")
        self.assertEqual(m, "ledger:loop__foo status:open")

    def test_custom_prefix(self):
        m = _make_marker("loop__bar", "blocked", prefix="cl:")
        self.assertEqual(m, "cl:loop__bar status:blocked")


# ---------------------------------------------------------------------------
# reconcile — create
# ---------------------------------------------------------------------------

class TestReconcileCreate(unittest.TestCase):
    def test_creates_loop_not_in_things(self):
        loops = [_loop()]
        tasks = []
        actions = reconcile(loops, tasks)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "create")
        self.assertEqual(actions[0].loop_slug, "loop__test")
        self.assertEqual(actions[0].things_title, "Test Loop")

    def test_create_uses_scope_routing(self):
        loops = [_loop(scope="work")]
        tasks = []
        actions = reconcile(loops, tasks, scope_routing={"work": "Work Tasks"})
        self.assertEqual(actions[0].kind, "create")
        self.assertEqual(actions[0].things_project, "Work Tasks")

    def test_create_uses_default_project(self):
        loops = [_loop(scope="unknown")]
        tasks = []
        actions = reconcile(loops, tasks, default_project="My Project")
        self.assertEqual(actions[0].things_project, "My Project")

    def test_blocked_loop_uses_blocked_project(self):
        loops = [_loop(status="blocked")]
        tasks = []
        actions = reconcile(loops, tasks, blocked_project="Blocked")
        self.assertEqual(actions[0].things_project, "Blocked")

    def test_create_embeds_marker(self):
        loops = [_loop()]
        tasks = []
        actions = reconcile(loops, tasks)
        self.assertIn("ledger:", actions[0].things_notes)
        self.assertIn("loop__test", actions[0].things_notes)


# ---------------------------------------------------------------------------
# reconcile — noop
# ---------------------------------------------------------------------------

class TestReconcileNoop(unittest.TestCase):
    def test_noop_when_in_sync(self):
        loops = [_loop(things_uuid="t1")]
        tasks = [_task(uuid="t1", title="Test Loop", notes="ledger:loop__test status:open")]
        actions = reconcile(loops, tasks)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "noop")

    def test_noop_matched_by_slug_marker(self):
        """Match by slug in notes field even without things_uuid."""
        loops = [_loop(things_uuid=None)]
        tasks = [_task(uuid="t1", notes="ledger:loop__test status:open")]
        actions = reconcile(loops, tasks)
        self.assertEqual(actions[0].kind, "noop")


# ---------------------------------------------------------------------------
# reconcile — update (drift)
# ---------------------------------------------------------------------------

class TestReconcileUpdate(unittest.TestCase):
    def test_update_on_title_drift(self):
        loops = [_loop(title="New Title", things_uuid="t1")]
        tasks = [_task(uuid="t1", title="Old Title", notes="ledger:loop__test status:open")]
        actions = reconcile(loops, tasks)
        self.assertEqual(actions[0].kind, "update")
        self.assertEqual(actions[0].things_title, "New Title")

    def test_update_on_notes_drift(self):
        loops = [_loop(status="blocked", things_uuid="t1")]
        tasks = [_task(uuid="t1", notes="ledger:loop__test status:open")]
        actions = reconcile(loops, tasks)
        self.assertEqual(actions[0].kind, "update")
        self.assertIn("blocked", actions[0].things_notes)


# ---------------------------------------------------------------------------
# reconcile — reverse complete / cancel
# ---------------------------------------------------------------------------

class TestReconcileReverse(unittest.TestCase):
    def test_reverse_complete(self):
        loops = [_loop(things_uuid="t1")]
        tasks = [_task(uuid="t1", status="completed")]
        actions = reconcile(loops, tasks, completed_maps_to="closed")
        self.assertEqual(actions[0].kind, "reverse_complete")
        self.assertEqual(actions[0].new_loop_status, "closed")

    def test_reverse_cancel(self):
        loops = [_loop(things_uuid="t1")]
        tasks = [_task(uuid="t1", status="cancelled")]
        actions = reconcile(loops, tasks, canceled_maps_to="snoozed")
        self.assertEqual(actions[0].kind, "reverse_cancel")
        self.assertEqual(actions[0].new_loop_status, "snoozed")

    def test_logbook_treated_as_completed(self):
        loops = [_loop(things_uuid="t1")]
        tasks = [_task(uuid="t1", status="logbook")]
        actions = reconcile(loops, tasks, completed_maps_to="closed")
        self.assertEqual(actions[0].kind, "reverse_complete")


# ---------------------------------------------------------------------------
# reconcile — orphan
# ---------------------------------------------------------------------------

class TestReconcileOrphan(unittest.TestCase):
    def test_orphan_flag(self):
        """Task with ledger: marker, no matching loop → orphan_flag."""
        loops = []
        tasks = [_task(uuid="t1", notes="ledger:loop__gone status:open")]
        actions = reconcile(loops, tasks, orphan_action="flag")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "orphan_flag")
        self.assertEqual(actions[0].things_uuid, "t1")

    def test_orphan_cancel(self):
        loops = []
        tasks = [_task(uuid="t1", notes="ledger:loop__gone status:open")]
        actions = reconcile(loops, tasks, orphan_action="cancel")
        self.assertEqual(actions[0].kind, "orphan_cancel")

    def test_orphan_ignore(self):
        loops = []
        tasks = [_task(uuid="t1", notes="ledger:loop__gone status:open")]
        actions = reconcile(loops, tasks, orphan_action="ignore")
        self.assertEqual(len(actions), 0)

    def test_completed_task_not_orphan(self):
        """Completed/cancelled tasks are not flagged as orphans."""
        loops = []
        tasks = [_task(uuid="t1", notes="ledger:loop__gone status:open", status="completed")]
        actions = reconcile(loops, tasks, orphan_action="flag")
        orphans = [a for a in actions if "orphan" in a.kind]
        self.assertEqual(len(orphans), 0)


# ---------------------------------------------------------------------------
# reconcile — marker-match without uuid (slug fallback)
# ---------------------------------------------------------------------------

class TestReconcileSlugFallback(unittest.TestCase):
    def test_match_by_slug_when_no_uuid(self):
        """Loop has no things_uuid but slug matches task notes marker."""
        loops = [_loop(things_uuid=None, slug="loop__test")]
        tasks = [_task(uuid="t99", title="Test Loop", notes="ledger:loop__test status:open")]
        actions = reconcile(loops, tasks)
        self.assertEqual(len(actions), 1)
        self.assertNotEqual(actions[0].kind, "create")


# ---------------------------------------------------------------------------
# reconcile — multiple loops
# ---------------------------------------------------------------------------

class TestReconcileMultiple(unittest.TestCase):
    def test_mixed_batch(self):
        loops = [
            _loop(slug="loop__a", title="A", things_uuid="t1"),       # in sync
            _loop(slug="loop__b", title="B", things_uuid=None),        # missing → create
            _loop(slug="loop__c", title="C New", things_uuid="t3"),    # title drift
        ]
        tasks = [
            _task(uuid="t1", title="A", notes="ledger:loop__a status:open"),
            _task(uuid="t3", title="C Old", notes="ledger:loop__c status:open"),
        ]
        actions = reconcile(loops, tasks)
        kinds = {a.kind for a in actions}
        self.assertIn("noop", kinds)
        self.assertIn("create", kinds)
        self.assertIn("update", kinds)


if __name__ == "__main__":
    unittest.main()
