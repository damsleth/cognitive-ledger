import json
from datetime import date, timedelta

import pytest

from ledger import improvements as imp


def seed(root, count=3, kind="retrieval_miss", **extra):
    path = root / "08_indices/signals.jsonl"
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as f:
        for _ in range(count):
            f.write(json.dumps({"ts": "2026-10-01T00:00:00Z", "type": kind,
                                **extra}) + "\n")


def test_reads_pure_and_lifecycle_recurrence(tmp_path):
    seed(tmp_path, query="deploy")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    item, = imp.queue(tmp_path)
    assert item["state"] == "open"
    assert before == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert imp.sync(tmp_path) == 1
    assert imp.sync(tmp_path) == 0
    imp.transition(tmp_path, item["id"], "resolve", artifact="commit:abc")
    assert imp.queue(tmp_path)[0]["state"] == "resolved"
    assert imp.sync(tmp_path) == 0
    seed(tmp_path, 1, query="deploy")  # Identical timestamp/content is still new evidence.
    item, = imp.queue(tmp_path)
    assert item["state"] == "open" and item["recurrence"]
    imp.sync(tmp_path)
    imp.transition(tmp_path, item["id"], "dismiss", reason="not useful")
    assert imp.queue(tmp_path)[0]["state"] == "dismissed"


def test_unrelated_signal_does_not_reopen(tmp_path):
    seed(tmp_path, 2, kind="correction", note="notes/02_facts/fact__x.md")
    item, = imp.queue(tmp_path)
    imp.transition(tmp_path, item["id"], "resolve", reason="corrected")
    seed(tmp_path, 1, kind="affirmation", note="notes/02_facts/fact__x.md")
    assert imp.queue(tmp_path)[0]["state"] == "resolved"


def test_disappeared_patterns_preserve_history_and_deferral(tmp_path):
    seed(tmp_path, query="deploy")
    item, = imp.queue(tmp_path)
    tomorrow = date.today() + timedelta(days=1)
    imp.transition(tmp_path, item["id"], "defer", until=tomorrow.isoformat())
    (tmp_path / "08_indices/signals.jsonl").unlink()
    assert imp.queue(tmp_path)[0]["state"] == "deferred"
    assert imp.queue(tmp_path, today=tomorrow)[0]["state"] == "open"


def test_validation_and_corrupt_log(tmp_path):
    seed(tmp_path, query="deploy")
    ident = imp.queue(tmp_path)[0]["id"]
    with pytest.raises(ValueError):
        imp.transition(tmp_path, ident, "resolve")
    with pytest.raises(ValueError):
        imp.transition(tmp_path, ident, "dismiss", reason="<private>secret</private>")
    with pytest.raises(ValueError):
        imp.transition(tmp_path, ident, "defer", until="yesterday")
    with pytest.raises(KeyError):
        imp.transition(tmp_path, "missing", "reopen")
    (tmp_path / "08_indices" / imp.LOG).write_text("broken\n")
    with pytest.raises(ValueError, match="line 1"):
        imp.queue(tmp_path)


def test_private_content_stripped(tmp_path):
    seed(tmp_path, query="deploy <private>SECRET</private>")
    imp.sync(tmp_path)
    assert "SECRET" not in (tmp_path / "08_indices" / imp.LOG).read_text()


def test_cli_json_and_exit_codes(tmp_path, monkeypatch, capsys):
    from ledger import learning_cli
    from argparse import Namespace
    monkeypatch.setattr(learning_cli, "get_config", lambda: Namespace(ledger_notes_dir=tmp_path))
    seed(tmp_path, query="deploy")
    args = Namespace(improvement_command="list", json=True, state="open")
    assert learning_cli.handle(args) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    args.improvement_command = "show"
    args.id = "missing"
    assert learning_cli.handle(args) == 4
