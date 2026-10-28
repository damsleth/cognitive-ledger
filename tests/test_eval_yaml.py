"""Safe YAML loading keeps the eval schema and diagnostics at the boundary."""
import pytest

from ledger.errors import EvalCaseValidationError
from ledger.eval import parse_eval_cases, validate_eval_cases
from ledger.notes.add import _build_frontmatter
from ledger.parsing.frontmatter import parse_frontmatter_text
from datetime import datetime, timezone


def test_yaml_values_and_validation(tmp_path):
    path = tmp_path / "cases.yaml"
    path.write_text('''- id: temporal
  query: "a: topic # with punctuation"
  lang: no
  as_of: 2023-07-01
  expected_none: yes
- query: missing paths
  scope: invalid
''')
    cases = parse_eval_cases(path)
    assert cases[0] == {
        "id": "temporal", "query": "a: topic # with punctuation", "scope": "all",
        "lang": "no", "as_of": "2023-07-01", "expected_none": True, "expected_any": [],
    }
    errors = validate_eval_cases(cases)
    assert "case_2: invalid scope 'invalid'" in errors
    assert "case_2: expected_any must contain at least one path" in errors


@pytest.mark.parametrize("text", ["[unterminated", "query: topic", "- scalar"])
def test_invalid_yaml_shape_has_eval_diagnostic(tmp_path, text):
    path = tmp_path / "cases.yaml"
    path.write_text(text)
    with pytest.raises(EvalCaseValidationError):
        parse_eval_cases(path)


def test_note_creation_quotes_metadata():
    text = _build_frontmatter(
        now=datetime(2026, 10, 28, tzinfo=timezone.utc),
        tags=["a, b", "x: y", "a, b"], confidence=0.8,
        source="user", scope="work", lang="no", status="open",
    )
    fields, _ = parse_frontmatter_text(text)
    assert fields["tags"] == ["a, b", "x: y"]
    assert fields["lang"] == "no"
    assert fields["status"] == "open"
