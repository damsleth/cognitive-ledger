import json

import pytest
import yaml

from ledger.instruction_audit import build_bundle, validate_findings, queue_items, numbered_excerpts
from ledger.improvements import sync, queue, transition


def setup(tmp_path):
    (tmp_path / "pref.md").write_text("---\nscope: dev\n---\nUse the package manager.\n<private>\nSECRET\n</private>\nVisible end.\n")
    (tmp_path / "SKILL.md").write_text("Edit the lockfile by hand.\n")
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(yaml.safe_dump({"sources": [
        {"path": "pref.md", "scope": "meta"}, {"path": "SKILL.md", "scope": "dev"}]}))
    return manifest, build_bundle(manifest, tmp_path)


def review(bundle):
    return {"bundle_hash": bundle["bundle_hash"], "findings": [{
        "kind": "conflict", "subject": "Lockfile editing", "scope": "dev",
        "confidence": .9, "destination": "skill", "reason": "Both govern dependency work.",
        "suggested_action": "Regenerate through the package manager.", "evidence": [
            {"source": bundle["documents"][0]["source"], "line": 4, "quote": "Use the package manager."},
            {"source": bundle["documents"][1]["source"], "line": 1, "quote": "Edit the lockfile by hand."}]}]}


def test_redaction_mapping_scopes_and_no_writes(tmp_path):
    manifest, bundle = setup(tmp_path)
    before = sorted(tmp_path.rglob("*"))
    assert "SECRET" not in json.dumps(bundle)
    assert bundle["documents"][0]["scope"] == "dev"
    end = next(e for e in bundle["documents"][0]["excerpts"] if e["text"] == "Visible end.")
    assert end["line"] == 8
    assert build_bundle(manifest, tmp_path) == bundle
    assert before == sorted(tmp_path.rglob("*"))
    assert validate_findings(bundle, review(bundle))[0]["destination"] == "skill"


@pytest.mark.parametrize("tamper", ["hash", "quote", "line", "single", "confidence"])
def test_reject_unsupported_reviews(tmp_path, tamper):
    _, bundle = setup(tmp_path)
    data = review(bundle)
    f = data["findings"][0]
    if tamper == "hash":
        data["bundle_hash"] = "old"
    elif tamper == "quote":
        f["evidence"][0]["quote"] = "INVENTED"
    elif tamper == "line":
        f["evidence"][0]["line"] = 999
    elif tamper == "single":
        f["evidence"] = f["evidence"][:1]
    else:
        f["confidence"] = True
    with pytest.raises(ValueError):
        validate_findings(bundle, data)


def test_missing_sources_limits_and_explicit_queue(tmp_path):
    manifest, bundle = setup(tmp_path)
    findings = validate_findings(bundle, review(bundle))
    items = queue_items(findings)
    assert sync(tmp_path, items) == 1
    assert sync(tmp_path, items) == 0
    transition(tmp_path, items[0]["id"], "resolve", artifact="commit:fix")
    sync(tmp_path, items)
    assert queue(tmp_path)[0]["state"] == "resolved"
    limited = build_bundle(manifest, tmp_path, max_chars=1000, max_files=1)
    assert len(limited["documents"]) == 1 and limited["omitted"]
    manifest.write_text("sources:\n  - path: absent.md\n")
    missing = build_bundle(manifest, tmp_path)
    assert missing["deterministic_findings"][0]["kind"] == "missing_source"
    assert queue_items(missing["deterministic_findings"])


def test_inline_private_and_unclosed_fence():
    result = numbered_excerpts("keep <private>secret</private> tail\n```private\nSECRET")
    assert result == [{"line": 1, "end_line": 3, "text": "keep  tail"}]


def test_cli_manifest_json(tmp_path, capsys):
    from ledger.cli import main
    manifest, _ = setup(tmp_path)
    assert main(["audit", "instructions", "--manifest", str(manifest), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["semantic_reviewed"] is False
    assert len(data["bundle"]["documents"]) == 2


def test_model_cannot_directly_enqueue(tmp_path, capsys):
    from ledger.cli import main
    manifest, _ = setup(tmp_path)
    assert main(["audit", "instructions", "--manifest", str(manifest),
                 "--command", "not-executed", "--enqueue"]) == 1
    assert "reviewed report" in capsys.readouterr().err


def test_stale_review_rejected_after_source_edit(tmp_path):
    manifest, bundle = setup(tmp_path)
    data = review(bundle)
    (tmp_path / "SKILL.md").write_text("New procedure")
    with pytest.raises(ValueError, match="bundle_hash"):
        validate_findings(build_bundle(manifest, tmp_path), data)
