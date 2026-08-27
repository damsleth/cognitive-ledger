from __future__ import annotations

import json
from pathlib import Path

from ledger.config import LedgerConfig, set_config, reset_config
from ledger import maintenance
from ledger.io.safe_write import append_timeline_entry


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_temp_config(tmp_path: Path) -> LedgerConfig:
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    return config


def test_status_handles_missing_timeline(tmp_path, capsys):
    _make_temp_config(tmp_path)
    try:
        rc = maintenance.cmd_status()
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert rc == 0
    assert "Timeline not found" in out


def test_lint_allows_lang_no(tmp_path):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__lang_no.md"
        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: personal
lang: no
---

# Fakt

## Statement

En testnotat.
""",
        )
        _write(
            config.timeline_path,
            """# Timeline

---
2026-02-01T00:00:00Z | created | notes/02_facts/fact__lang_no.md | seed
""",
        )

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


def test_lint_fails_missing_frontmatter(tmp_path):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__bad.md"
        _write(note, "# Missing frontmatter\n")
        _write(config.timeline_path, "# Timeline\n")

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 1


def test_alias_suggestions_from_tag_cooccurrence(tmp_path):
    config = _make_temp_config(tmp_path)
    try:
        note_one = config.ledger_notes_dir / "02_facts" / "fact__one.md"
        note_two = config.ledger_notes_dir / "02_facts" / "fact__two.md"
        for path, title in [(note_one, "Commute Planning"), (note_two, "Commute Scheduling")]:
            _write(
                path,
                f"""---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [commute, calendar]
confidence: 0.9
source: user
scope: personal
lang: en
---

# {title}

## Statement

Commute calendar planning details.
""",
            )

        indices_dir = config.ledger_notes_dir / "08_indices"
        indices_dir.mkdir(parents=True, exist_ok=True)
        maintenance._generate_alias_suggestions(indices_dir)

        data = json.loads((indices_dir / "aliases_suggested.json").read_text(encoding="utf-8"))
        assert "commute" in data
        assert "calendar" in data["commute"]
    finally:
        reset_config()


def test_generate_semantic_index_honors_configured_model(tmp_path, monkeypatch):
    config = LedgerConfig(
        ledger_root=tmp_path, embed_backend="local", embed_model="BAAI/bge-m3"
    )
    set_config(config)
    captured: dict[str, list[str]] = {}

    def fake_run(command, required=True):
        captured["cmd"] = command
        return 0, ""

    monkeypatch.setattr(maintenance, "_run_subprocess", fake_run)
    try:
        maintenance._generate_semantic_index()
    finally:
        reset_config()

    cmd = captured["cmd"]
    # Configured model is passed through; the hardcoded default is not used.
    assert "BAAI/bge-m3" in cmd
    assert "TaylorAI/bge-micro-v2" not in cmd
    assert cmd[cmd.index("--backend") + 1] == "local"


def test_generate_semantic_index_falls_back_to_backend_default(tmp_path, monkeypatch):
    # No configured model -> the backend default is used (not an error).
    config = LedgerConfig(ledger_root=tmp_path, embed_backend="local", embed_model=None)
    set_config(config)
    captured: dict[str, list[str]] = {}

    def fake_run(command, required=True):
        captured["cmd"] = command
        return 0, ""

    monkeypatch.setattr(maintenance, "_run_subprocess", fake_run)
    try:
        maintenance._generate_semantic_index()
    finally:
        reset_config()

    from ledger.embeddings import default_model_for_backend

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == default_model_for_backend("local")


def test_sync_reports_missing_state(tmp_path, capsys):
    _make_temp_config(tmp_path)
    try:
        rc = maintenance.cmd_sync(apply=False)
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert rc == 1
    assert "State not found." in out


def test_sync_apply_then_check_healthy(tmp_path, capsys):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__sync.md"
        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [sync]
confidence: 0.9
source: user
scope: personal
lang: en
---

# Sync Fact

## Statement

Seed.
""",
        )
        _write(config.timeline_path, "# Timeline\n\n---\n")

        apply_rc = maintenance.cmd_sync(apply=True)
        capsys.readouterr()  # clear apply output
        check_rc = maintenance.cmd_sync(apply=False)
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert apply_rc == 0
    assert check_rc == 0
    assert "-> Sync healthy" in out


def test_sync_detects_unlogged_note_change(tmp_path, capsys):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__sync_drift.md"
        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [sync]
confidence: 0.9
source: user
scope: personal
lang: en
---

# Drift Fact

## Statement

Before.
""",
        )
        _write(config.timeline_path, "# Timeline\n\n---\n")
        maintenance.cmd_sync(apply=True)

        _write(
            note,
            """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-02T00:00:00Z
tags: [sync]
confidence: 0.9
source: user
scope: personal
lang: en
---

# Drift Fact

## Statement

After.
""",
        )

        check_rc = maintenance.cmd_sync(apply=False)
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert check_rc == 1
    assert "Unlogged note changes: 1" in out


def test_sync_matches_timeline_event_to_current_file_version(tmp_path, capsys):
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__versioned.md"
        _write(note, "before\n")
        _write(config.timeline_jsonl_path, "")
        maintenance.cmd_sync(apply=True)
        capsys.readouterr()

        _write(note, "logged version\n")
        append_timeline_entry(
            config.timeline_path,
            "updated",
            note,
            "logged",
            root_dir=config.ledger_root,
            ledger_notes_dir=config.ledger_notes_dir,
        )
        assert maintenance._compute_sync_report()["unlogged_paths"] == []

        _write(note, "later unlogged version\n")
        report = maintenance._compute_sync_report()
    finally:
        reset_config()

    assert report["logged_paths"] == []
    assert report["unlogged_paths"] == ["notes/02_facts/fact__versioned.md"]


def test_status_recommends_sleep_for_large_unlogged_drift(tmp_path, monkeypatch):
    config = _make_temp_config(tmp_path)
    try:
        _write(
            config.timeline_jsonl_path,
            '{"ts":"2026-08-27T00:00:00Z","action":"sleep","path":"-","desc":"done"}\n',
        )
        monkeypatch.setattr(
            maintenance,
            "_compute_sync_report",
            lambda: {
                "state_invalid": False,
                "state_exists": True,
                "timeline_rewound": False,
                "unlogged_paths": [f"notes/02_facts/fact__{i}.md" for i in range(50)],
            },
        )

        payload = maintenance._status_payload()
    finally:
        reset_config()

    assert payload["unlogged_change_count"] == 50
    assert payload["sleep_recommended"] is True
    assert payload["sleep_recommendation_reasons"] == ["unlogged_change_count"]
    drift_gate = next(
        gate
        for gate in payload["sleep_gate_evaluations"]
        if gate["code"] == "unlogged_change_count"
    )
    assert drift_gate == {
        "code": "unlogged_change_count",
        "observed": 50,
        "operator": ">=",
        "threshold": 50,
        "met": True,
    }


def test_status_prints_gate_reasoning(tmp_path, monkeypatch, capsys):
    config = _make_temp_config(tmp_path)
    try:
        _write(
            config.timeline_jsonl_path,
            '{"ts":"2026-08-27T00:00:00Z","action":"sleep","path":"-","desc":"done"}\n',
        )
        monkeypatch.setattr(
            maintenance,
            "_compute_sync_report",
            lambda: {
                "state_invalid": False,
                "state_exists": True,
                "timeline_rewound": False,
                "unlogged_paths": [],
            },
        )
        maintenance.cmd_status()
    finally:
        reset_config()

    out = capsys.readouterr().out
    assert "No sleep needed" in out
    assert "days_since=0 >= 7 with work_present=False" in out
    assert "changes_since=0 >= 25" in out
    assert "unlogged_change_count=0 >= 50" in out
    assert "sync_drift=clean in ['state_invalid', 'timeline_rewound']" in out


def test_age_gate_requires_work_since_last_sleep(tmp_path, monkeypatch):
    config = _make_temp_config(tmp_path)
    clean_report = {
        "state_invalid": False,
        "state_exists": True,
        "timeline_rewound": False,
        "unlogged_paths": [],
    }
    try:
        _write(
            config.timeline_jsonl_path,
            '{"ts":"2026-08-01T00:00:00Z","action":"sleep","path":"-","desc":"done"}\n',
        )
        monkeypatch.setattr(maintenance, "_compute_sync_report", lambda: clean_report)

        idle_payload = maintenance._status_payload()
        with_work_gate = next(
            gate
            for gate in idle_payload["sleep_gate_evaluations"]
            if gate["code"] == "days_since"
        )
        assert idle_payload["days_since"] >= 7
        assert with_work_gate["work_present"] is False
        assert idle_payload["sleep_recommended"] is False

        with config.timeline_jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(
                '{"ts":"2026-08-02T00:00:00Z","action":"updated",'
                '"path":"notes/02_facts/fact__one.md","desc":"changed"}\n'
            )
        active_payload = maintenance._status_payload()
    finally:
        reset_config()

    age_gate = next(
        gate
        for gate in active_payload["sleep_gate_evaluations"]
        if gate["code"] == "days_since"
    )
    assert age_gate["work_present"] is True
    assert active_payload["sleep_recommendation_reasons"] == ["days_since"]


def test_status_counts_changes_from_jsonl_not_generated_markdown(tmp_path, monkeypatch):
    config = _make_temp_config(tmp_path)
    try:
        _write(
            config.timeline_jsonl_path,
            "\n".join(
                [
                    '{"ts":"2026-08-26T00:00:00Z","action":"sleep","path":"-","desc":"done"}',
                    '{"ts":"2026-08-27T00:00:00Z","action":"updated","path":"notes/02_facts/fact__one.md","desc":"one"}',
                    "",
                ]
            ),
        )
        _write(
            config.timeline_path,
            "# Timeline\n\n---\n"
            "2026-08-26T00:00:00Z | sleep | - | done\n"
            "2026-08-27T00:00:00Z | updated | notes/02_facts/fact__md_only.md | direct edit\n"
            "2026-08-27T00:00:01Z | updated | notes/02_facts/fact__also_md_only.md | direct edit\n",
        )
        monkeypatch.setattr(
            maintenance,
            "_compute_sync_report",
            lambda: {
                "state_invalid": False,
                "state_exists": True,
                "timeline_rewound": False,
                "unlogged_paths": [],
            },
        )

        payload = maintenance._status_payload()
    finally:
        reset_config()

    assert payload["entries_total"] == 2
    assert payload["changes_since"] == 1


def test_status_handles_every_sync_state_without_missing_drift_count(tmp_path, monkeypatch):
    config = _make_temp_config(tmp_path)
    try:
        _write(
            config.timeline_jsonl_path,
            '{"ts":"2026-08-27T00:00:00Z","action":"sleep","path":"-","desc":"done"}\n',
        )
        for state in ("state_invalid", "unknown", "timeline_rewound", "clean"):
            report = {
                "state_invalid": state == "state_invalid",
                "state_exists": state != "unknown",
                "timeline_rewound": state == "timeline_rewound",
                "unlogged_paths": [],
            }
            monkeypatch.setattr(maintenance, "_compute_sync_report", lambda: report)
            payload = maintenance._status_payload()
            assert payload["sync_drift"] == state
            assert payload["unlogged_change_count"] == 0
            assert payload["sleep_recommended"] is (
                state in {"state_invalid", "timeline_rewound"}
            )
    finally:
        reset_config()
