"""Eval cases: per-case ``as_of`` (B3) and missing-path warnings (B2)."""
from __future__ import annotations

import datetime as dt
import textwrap
from pathlib import Path

import pytest

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.eval import parse_case_as_of, parse_eval_cases, run_eval, validate_eval_cases
from ledger.retrieval import clear_candidate_cache


def _write_cases(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "cases.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


@pytest.fixture()
def tmp_cfg(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    for sub in ("02_facts", "09_archive", "08_indices"):
        (config.ledger_notes_dir / sub).mkdir(parents=True, exist_ok=True)
    yield config
    clear_candidate_cache()
    reset_config()


def test_parse_case_as_of_forms():
    utc = dt.timezone.utc
    assert parse_case_as_of("") is None
    assert parse_case_as_of(None) is None
    assert parse_case_as_of("2024-06-01") == dt.datetime(2024, 6, 1, tzinfo=utc)
    assert parse_case_as_of("2024-06-01T12:00:00Z") == dt.datetime(2024, 6, 1, 12, tzinfo=utc)
    with pytest.raises(ValueError):
        parse_case_as_of("last june")


def test_invalid_as_of_is_a_validation_error(tmp_cfg, tmp_path):
    cases = parse_eval_cases(_write_cases(tmp_path, """\
        - id: h1
          query: anything
          as_of: last june
          expected_any:
            - notes/02_facts/fact__x.md
    """))
    errors = validate_eval_cases(cases)
    assert any("invalid as_of" in e for e in errors)


def test_as_of_reaches_the_ranker(tmp_cfg, tmp_path):
    seen = {}

    def ranker(query, scope="all", limit=50, **kwargs):
        seen["as_of"] = kwargs.get("as_of")
        return {"results": []}

    p = _write_cases(tmp_path, """\
        - id: h1
          query: anything
          as_of: 2024-06-01
          expected_any:
            - notes/02_facts/fact__x.md
    """)
    run_eval(p, k=3, rank_query_fn=ranker)
    assert seen["as_of"] == dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc)


def test_ranker_without_as_of_raises_instead_of_dropping_it(tmp_cfg, tmp_path):
    def ranker(query, scope="all", limit=50):
        return {"results": []}

    p = _write_cases(tmp_path, """\
        - id: h1
          query: anything
          as_of: 2024-06-01
          expected_any:
            - notes/02_facts/fact__x.md
    """)
    with pytest.raises(TypeError):
        run_eval(p, k=3, rank_query_fn=ranker)


def test_missing_expected_path_warns_without_strict(tmp_cfg, tmp_path, capsys):
    cases = parse_eval_cases(_write_cases(tmp_path, """\
        - id: gone
          query: anything
          expected_any:
            - notes/05_open_loops/loop__deleted.md
    """))
    assert validate_eval_cases(cases, strict_cases=False) == []
    err = capsys.readouterr().err
    assert "warning: gone: expected_any path not found" in err
    assert any("not found" in e for e in validate_eval_cases(cases, strict_cases=True))


def test_as_of_case_respects_the_validity_window(tmp_cfg, tmp_path):
    """Hit inside the window; miss after valid_to; miss with no as_of."""
    note = tmp_cfg.ledger_notes_dir / "09_archive" / "fact__old_employer.md"
    note.write_text(
        "---\n"
        "created: 2024-01-01T00:00:00Z\n"
        "updated: 2025-01-01T00:00:00Z\n"
        "tags: [work]\n"
        "confidence: 0.9\n"
        "source: user\n"
        "scope: work\n"
        "lang: en\n"
        "valid_from: 2024-01-01T00:00:00Z\n"
        "valid_to: 2025-01-01T00:00:00Z\n"
        "---\n\n# Employer was Zephyrcorp\n\n## Statement\n\nWorked at Zephyrcorp.\n",
        encoding="utf-8",
    )

    def score(as_of_line: str) -> float:
        clear_candidate_cache()
        p = _write_cases(tmp_path, f"""\
            - id: employer
              query: zephyrcorp employer
              {as_of_line}
              expected_any:
                - notes/09_archive/fact__old_employer.md
        """)
        return run_eval(p, k=3)["hitk"]

    assert score("as_of: 2024-06-01") == 1.0
    assert score("as_of: 2025-06-01") == 0.0
    assert score("scope: all") == 0.0
