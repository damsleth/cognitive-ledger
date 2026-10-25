import json
from pathlib import Path
import sys

import pytest
import yaml

from ledger.behaviour import run_suite, load_cases


def write_cases(tmp_path, cases):
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump({"cases": cases}))
    return path


def test_real_cli_contract_cases(tmp_path):
    suite = Path(__file__).parent / "fixtures/behaviour_cases.yaml"
    result = run_suite(suite, tmp_path)
    assert result["totals"]["candidate"] == {"passed": 2, "total": 2, "pass_rate": 1.0}
    assert result["agent_pass_rate_delta"] is None


def test_paired_context_redaction_and_reproducibility(tmp_path):
    notes = tmp_path / "notes"
    (notes / "03_preferences").mkdir(parents=True)
    (notes / "03_preferences/pref__x.md").write_text("Use TASK_SYSTEM_X. <private>SECRET</private>")
    case = {"id": "routing", "kind": "agent", "scenario": 'Return {"destination": "..."}.',
            "setup": {"memory_refs": ["notes/03_preferences/pref__x.md"]},
            "checks": {"json_equals": {"destination": "task"}, "stdout_absent": ["SECRET"]}}
    suite = write_cases(tmp_path, [case])
    script = ('import sys,json; p=sys.stdin.read(); assert "SECRET" not in p; '
              'print(json.dumps({"destination":"task" if "TASK_SYSTEM_X" in p else "note"}))')
    result = run_suite(suite, notes, command=[sys.executable, "-c", script], model="fixture", runs=2)
    assert result["agent_pass_rate_delta"] == 1
    assert result["totals"]["candidate"]["passed"] == 2
    assert [r["arm"] for r in result["results"]] == ["baseline", "candidate", "candidate", "baseline"]
    assert all(r["context_hashes"] for r in result["results"] if r["arm"] == "candidate")


def test_missing_context_fails_before_subprocess(tmp_path):
    case = {"id": "missing", "kind": "agent", "scenario": "x",
            "setup": {"memory_refs": ["notes/no.md"]}, "checks": {"json_equals": {"ok": True}}}
    with pytest.raises(ValueError, match="Missing"):
        run_suite(write_cases(tmp_path, [case]), tmp_path, command=["not-executable"], model="fixture")


@pytest.mark.parametrize("case", [
    {"id": "x", "kind": "command", "command": ["true"], "checks": {}},
    {"id": "x", "kind": "command", "command": ["true"], "checks": {"stdout_contains": []}},
    {"id": "x", "kind": "command", "command": ["true"], "checks": {"files_absent": ["../x"]}},
    {"id": "x", "kind": "agent", "scenario": "x", "checks": {"stdout_absent": ["x"]}},
])
def test_vacuous_or_unsafe_cases_rejected(tmp_path, case):
    with pytest.raises(ValueError):
        load_cases(write_cases(tmp_path, [case]))


def test_zero_cases_invalid(tmp_path):
    with pytest.raises(ValueError):
        load_cases(write_cases(tmp_path, []))


def test_timeout_is_a_failure(tmp_path):
    case = {"id": "timeout", "kind": "command",
            "command": ["{python}", "-c", "import time; time.sleep(1)"], "checks": {"exit_code": 0}}
    result = run_suite(write_cases(tmp_path, [case]), tmp_path, timeout=.01)
    assert result["results"][0]["failures"] == ["timeout"]


def test_invalid_agent_output_is_not_a_pass(tmp_path):
    case = {"id": "json", "kind": "agent", "scenario": "x", "checks": {"json_equals": {"ok": True}}}
    result = run_suite(write_cases(tmp_path, [case]), tmp_path,
                       command=[sys.executable, "-c", "print('not json')"], model="fixture")
    assert result["totals"]["candidate"]["passed"] == 0


def test_missing_field_does_not_satisfy_null_assertion(tmp_path):
    case = {"id": "null", "kind": "agent", "scenario": "x", "checks": {"json_equals": {"absent": None}}}
    result = run_suite(write_cases(tmp_path, [case]), tmp_path,
                       command=[sys.executable, "-c", "print('{}')"], model="fixture")
    assert result["totals"]["candidate"]["passed"] == 0


def test_command_side_effect_assertions(tmp_path):
    case = {"id": "files", "kind": "command",
            "command": ["{python}", "-c", "from pathlib import Path; Path('result').write_text('done')"],
            "checks": {"files_contain": {"result": "done"}, "files_absent": ["wrong"]}}
    result = run_suite(write_cases(tmp_path, [case]), tmp_path)
    assert result["results"][0]["passed"]
    assert not (tmp_path / "result").exists()
