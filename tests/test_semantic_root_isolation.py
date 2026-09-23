"""Each corpus owns its embedding index (the ab harness clobbered the live one)."""

from __future__ import annotations

import json

from ledger.config import LedgerConfig, reset_config, set_config
from ledger import embeddings


def _legacy_index(cfg, source_root):
    d = cfg.legacy_semantic_root / "ledger" / "local__m"
    d.mkdir(parents=True)
    (d / "index.json").write_text(json.dumps({"source_root": str(source_root), "items": []}), encoding="utf-8")
    (d / "vectors.npy").write_bytes(b"")
    return d


def test_two_corpora_on_one_checkout_do_not_share_an_index(tmp_path):
    live = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=tmp_path / "live")
    fixture = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=tmp_path / "fixture")
    assert live.semantic_root != fixture.semantic_root


def test_legacy_index_is_adopted_only_by_its_owner(tmp_path):
    owner = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=tmp_path / "live")
    other = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=tmp_path / "fixture")
    legacy = _legacy_index(owner, owner.ledger_notes_dir)
    try:
        set_config(other)
        embeddings._adopt_legacy_index("ledger", "local", "m")
        assert legacy.exists(), "a foreign corpus must not take the index"

        set_config(owner)
        embeddings._adopt_legacy_index("ledger", "local", "m")
        assert not legacy.exists()
        assert (owner.semantic_root / "ledger" / "local__m" / "index.json").is_file()
    finally:
        reset_config()
