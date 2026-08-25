"""infer_lang produces the lang codes the contradiction eval keys on; a wrong
answer silently swaps which auto-supersede threshold applies."""
from __future__ import annotations

from ledger.text import infer_lang


def test_norwegian_with_preposition_i():
    # "i" is Norwegian for "in"; it used to trip the English detector and make
    # this come back "mixed", bypassing the strict lang:no threshold.
    assert infer_lang("Jeg bor i Oslo") == "no"
    assert infer_lang("Vi skal ikke gjøre det i dag") == "no"


def test_english():
    assert infer_lang("I live in Oslo and work here") == "en"
    assert infer_lang("What should we do if it fails") == "en"


def test_mixed():
    assert infer_lang("Jeg bor i Oslo and I work here") == "mixed"


def test_no_markers_is_mixed():
    assert infer_lang("12345") == "mixed"
