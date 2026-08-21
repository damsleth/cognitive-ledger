"""Tests for ledger/integrations/things3.py."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch, MagicMock

from ledger.integrations import things3 as adapter


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _task(uuid="abc123", title="Test Task", notes="ledger:loop__test status:open", status="inbox"):
    return {
        "uuid": uuid,
        "title": title,
        "notes": notes,
        "status": status,
    }


def _make_run_mock(return_value: str):
    """Return a mock for adapter._run that returns *return_value*."""
    return patch("ledger.integrations.things3._run", return_value=return_value)


# ---------------------------------------------------------------------------
# things_available
# ---------------------------------------------------------------------------

class TestThingsAvailable(unittest.TestCase):
    def test_true_when_on_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/things"):
            self.assertTrue(adapter.things_available())

    def test_false_when_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(adapter.things_available())


# ---------------------------------------------------------------------------
# read_tasks
# ---------------------------------------------------------------------------

class TestReadTasks(unittest.TestCase):
    def test_filters_on_marker(self):
        tasks = [
            _task(uuid="1", notes="ledger:loop__a status:open"),
            _task(uuid="2", notes="some other note"),
            _task(uuid="3", notes="ledger:loop__b status:blocked"),
        ]
        with _make_run_mock(json.dumps(tasks)):
            result = adapter.read_tasks(marker_prefix="ledger:")
        uuids = {t["uuid"] for t in result}
        self.assertIn("1", uuids)
        self.assertIn("3", uuids)
        self.assertNotIn("2", uuids)

    def test_empty_tasks(self):
        with _make_run_mock("[]"):
            result = adapter.read_tasks()
        self.assertEqual(result, [])

    def test_empty_response(self):
        with _make_run_mock(""):
            result = adapter.read_tasks()
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

class TestCreateTask(unittest.TestCase):
    def test_dry_run_returns_none(self):
        with _make_run_mock(""):
            result = adapter.create_task("My Task", dry_run=True)
        self.assertIsNone(result)

    def test_creates_and_confirms(self):
        new_task = _task(uuid="new-uuid", notes="ledger:loop__x status:open")
        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            # Return task list on confirmation read
            if "tasks" in args:
                return json.dumps([new_task])
            return ""

        with patch("ledger.integrations.things3._run", side_effect=fake_run):
            result = adapter.create_task(
                "My Task",
                notes="ledger:loop__x status:open",
                marker_prefix="ledger:",
            )
        self.assertEqual(result, "new-uuid")
        # First call was things add
        self.assertIn("add", run_calls[0])

    def test_creates_with_project(self):
        run_calls = []
        new_task = _task(uuid="p-uuid", notes="ledger:loop__y status:open")

        def fake_run(args, **kwargs):
            run_calls.append(args)
            if "tasks" in args:
                return json.dumps([new_task])
            return ""

        with patch("ledger.integrations.things3._run", side_effect=fake_run):
            adapter.create_task("Title", notes="ledger:loop__y status:open", project="Work")
        first = run_calls[0]
        # things3-cli: project/area via --list-id=ID, title positional after --
        self.assertIn("--list-id=Work", first)
        self.assertIn("Title", first)


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------

class TestUpdateTask(unittest.TestCase):
    def test_dry_run_no_run(self):
        with patch("ledger.integrations.things3._run") as mock_run:
            adapter.update_task("uuid1", title="New Title", dry_run=True)
        mock_run.assert_not_called()

    def test_updates_and_confirms(self):
        existing = _task(uuid="uuid1")

        def fake_run(args, **kwargs):
            # confirm reads `things tasks --json` (a list)
            if "tasks" in args:
                return json.dumps([existing])
            return ""

        with patch("ledger.integrations.things3._run", side_effect=fake_run) as mock_run:
            adapter.update_task("uuid1", title="New Title")
        first = mock_run.call_args_list[0][0][0]
        self.assertIn("update", first)
        self.assertIn("--id=uuid1", first)
        self.assertIn("New Title", first)


# ---------------------------------------------------------------------------
# complete_task / cancel_task
# ---------------------------------------------------------------------------

class TestCompleteCancel(unittest.TestCase):
    def test_complete_dry_run(self):
        with patch("ledger.integrations.things3._run") as mock_run:
            adapter.complete_task("u1", dry_run=True)
        mock_run.assert_not_called()

    def test_cancel_dry_run(self):
        with patch("ledger.integrations.things3._run") as mock_run:
            adapter.cancel_task("u1", dry_run=True)
        mock_run.assert_not_called()

    def test_complete_calls_correct_command(self):
        # A completed task leaves the active list, so the confirmation read
        # must come back empty — that absence IS the success signal.
        done = {"updated": False}

        def fake_run(args, **kwargs):
            if "tasks" in args:
                return json.dumps([] if done["updated"] else [_task(uuid="u1")])
            if "update" in args:
                done["updated"] = True
            return ""

        with patch("ledger.integrations.things3._run", side_effect=fake_run) as mock_run:
            adapter.complete_task("u1")
        first_call_args = mock_run.call_args_list[0][0][0]
        # things3-cli has no `complete` command: it's `update --id=U --completed`
        self.assertIn("update", first_call_args)
        self.assertIn("--id=u1", first_call_args)
        self.assertIn("--completed", first_call_args)

    def test_complete_raises_if_task_stays_active(self):
        """A write that silently did nothing must not report success."""
        def fake_run(args, **kwargs):
            if "tasks" in args:
                return json.dumps([_task(uuid="u1")])
            return ""

        with patch("ledger.integrations.things3._run", side_effect=fake_run):
            with patch("ledger.integrations.things3.time.sleep"):
                with self.assertRaises(RuntimeError):
                    adapter.complete_task("u1")


# ---------------------------------------------------------------------------
# list_projects / ensure_project
# ---------------------------------------------------------------------------

class TestProjects(unittest.TestCase):
    def test_list_projects(self):
        projects = [{"uuid": "p1", "title": "Work"}, {"uuid": "p2", "title": "Home"}]
        with _make_run_mock(json.dumps(projects)):
            result = adapter.list_projects()
        self.assertEqual(len(result), 2)

    def test_ensure_project_already_exists(self):
        projects = [{"uuid": "p1", "title": "Work"}]
        with _make_run_mock(json.dumps(projects)) as mock_run:
            adapter.ensure_project("Work")
        # Should only call projects --json, not add-project
        mock_run.assert_called_once()

    def test_ensure_project_creates_missing(self):
        projects: list = []
        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            if "projects" in args:
                return json.dumps(projects)
            return ""

        with patch("ledger.integrations.things3._run", side_effect=fake_run):
            adapter.ensure_project("New Project")
        commands = [" ".join(a) for a in run_calls]
        self.assertTrue(any("add-project" in c for c in commands))

    def test_ensure_project_dry_run(self):
        with _make_run_mock("[]") as mock_run:
            adapter.ensure_project("X", dry_run=True)
        # Only called once (projects list), not add-project
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# get_task_by_uuid
# ---------------------------------------------------------------------------

class TestGetTaskByUuid(unittest.TestCase):
    def test_returns_task(self):
        task = _task(uuid="abc")
        # get_task_by_uuid scans `things tasks --json` (a list)
        with _make_run_mock(json.dumps([task])):
            result = adapter.get_task_by_uuid("abc")
        self.assertEqual(result["uuid"], "abc")

    def test_returns_none_on_error(self):
        with patch("ledger.integrations.things3._run", side_effect=RuntimeError("not found")):
            result = adapter.get_task_by_uuid("missing")
        self.assertIsNone(result)

    def test_returns_none_on_bad_json(self):
        with _make_run_mock("not-json"):
            result = adapter.get_task_by_uuid("bad")
        self.assertIsNone(result)
