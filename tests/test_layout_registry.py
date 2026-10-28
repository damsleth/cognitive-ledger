"""Keep note metadata and executable folder literals in the central registry."""

import ast
import io
from pathlib import Path
import re
import tokenize

from ledger.layout import NOTE_LAYOUTS, path_kind


# (relative module, literal) -> reason. No executable folder literals are kept.
FOLDER_LITERAL_ALLOWLIST: dict[tuple[str, str], str] = {}


def test_layout_registry():
    layouts = list(NOTE_LAYOUTS.values())
    for field in ("subdir", "prefix", "kind"):
        assert len({getattr(layout, field) for layout in layouts}) == len(layouts)
    for layout in layouts:
        assert path_kind(f"notes/{layout.subdir}/example.md") == layout.kind

    package = Path(__file__).resolve().parents[1] / "ledger"
    violations = []
    used_allowlist = set()
    for module in sorted(package.rglob("*.py")):
        if module == package / "layout.py":
            continue
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Include attribute docstrings as well as module/function/class docs.
        docstrings = {
            (node.value.lineno, node.value.col_offset)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.STRING or token.start in docstrings:
                continue
            for literal in re.findall(r"0[1-9]_[a-z_]+", token.string):
                key = (module.relative_to(package).as_posix(), literal)
                if key in FOLDER_LITERAL_ALLOWLIST:
                    assert FOLDER_LITERAL_ALLOWLIST[key], "An exception needs a reason"
                    used_allowlist.add(key)
                else:
                    violations.append(f"{key[0]}:{token.start[0]}: {literal}")
    assert not violations, "Folder literals belong in ledger.layout:\n" + "\n".join(violations)
    assert used_allowlist == FOLDER_LITERAL_ALLOWLIST.keys(), "Remove stale exceptions"


def test_schema_yaml_types_agree_with_registry():
    import yaml
    from pathlib import Path

    from ledger.layout import NOTE_LAYOUTS, NOTES_ROOT_NAME

    schema = yaml.safe_load((Path(__file__).resolve().parents[1] / "schema.yaml").read_text())
    by_prefix = {spec["prefix"]: spec["folder"] for spec in schema["types"].values()}
    for layout in NOTE_LAYOUTS.values():
        assert by_prefix.get(layout.prefix) == f"{NOTES_ROOT_NAME}/{layout.subdir}", layout.name
