"""schema.yaml is the documented vocabulary; schema_values.py is the Python
mirror the code actually reads. Nothing kept them in sync, and an unquoted
`no` in the YAML silently became boolean False. Both failures look identical
from the code side: a language that quietly stops matching.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ledger.schema_values import LANG_VALUES, SCOPE_VALUES, SOURCE_VALUES, STATUS_VALUES

SCHEMA = Path(__file__).resolve().parent.parent / "schema.yaml"


def _enum(name: str) -> set:
    def find(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == name and isinstance(value, list):
                    return value
                found = find(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = find(item)
                if found is not None:
                    return found
        return None

    values = find(yaml.safe_load(SCHEMA.read_text(encoding="utf-8")))
    assert values is not None, f"no {name!r} enum in schema.yaml"
    return set(values)


def test_enums_match_python_mirror():
    for name, mirror in (
        ("lang", LANG_VALUES),
        ("scope", SCOPE_VALUES),
        ("source", SOURCE_VALUES),
        ("status", STATUS_VALUES),
    ):
        assert _enum(name) == set(mirror), f"schema.yaml {name!r} drifted from schema_values"


def test_enum_values_are_all_strings():
    """Catches the YAML 1.1 bool trap: bare no/yes/on/off parse as booleans."""
    for name in ("lang", "scope", "source", "status"):
        for value in _enum(name):
            assert isinstance(value, str), f"schema.yaml {name!r} has non-string {value!r}"
