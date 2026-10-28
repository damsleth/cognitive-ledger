import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "projects_refresh", Path(__file__).resolve().parents[1] / "scripts" / "projects_refresh.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


def test_validate_drops_out_of_window_oversized_and_private_lines():
    proposal = {
        "state": "x" * 900,
        "log": [
            {"date": "2026-10-20", "text": "Released 1.2 (source: git repo)"},
            {"date": "2020-01-01", "text": "too old"},
            {"date": "2999-01-01", "text": "future"},
            {"date": "2026-10-21", "text": "<private>no</private>"},
            {"date": "2026-10-22", "text": "y" * 400},
        ],
        "status_suggestion": "done: shipped",
    }
    state, lines, suggestion = refresh.validate(proposal, "2026-10-01")
    assert state is None
    assert lines == [("2026-10-20", "Released 1.2 (source: git repo)")]
    assert suggestion == "done: shipped"
