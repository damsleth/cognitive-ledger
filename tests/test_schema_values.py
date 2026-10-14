"""schema.yaml is the spec; ledger.schema_values is what lint enforces. Keep them equal."""
from pathlib import Path

import yaml

from ledger import schema_values as sv

SCHEMA = yaml.safe_load((Path(__file__).resolve().parents[1] / "schema.yaml").read_text(encoding="utf-8"))
ENUMS = SCHEMA["frontmatter"]["enums"]


def _enum(name):
    return {str(v) for v in ENUMS[name]}


def test_enums_match_schema_yaml():
    assert sv.SOURCE_VALUES == _enum("source")
    assert sv.SCOPE_VALUES == _enum("scope")
    assert sv.LANG_VALUES == _enum("lang")
    assert sv.STATUS_VALUES == _enum("status")
    assert sv.PROVENANCE_VALUES == _enum("provenance")
    assert sv.IDENTITY_TYPE_VALUES == _enum("identity_type")


def test_limits_match_schema_yaml():
    assert sv.MAX_TAGS == SCHEMA["limits"]["max_tags"]
    assert sv.MAX_IDENTITY_NOTES == SCHEMA["limits"]["max_identity_notes"]


def test_via_values_documented_in_schema():
    # `via` is documented as a comment list in schema.yaml, not a YAML enum.
    raw = (Path(__file__).resolve().parents[1] / "schema.yaml").read_text(encoding="utf-8")
    line = next(l for l in raw.splitlines() if l.strip().startswith("via:"))
    documented = {v.strip() for v in line.split("#", 1)[1].split(":", 1)[1].split("|")}
    assert sv.VIA_VALUES == documented
