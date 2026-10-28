"""Compatibility imports used outside this package."""
from .policy import Decision, PairFacts, decide
from .state import ContradictionState, _content_hash, _pair_key, load_state, save_state
from .discovery import _get_semantic_neighbors
from .apply import PairResult, ScanResult, _make_conflict_note_filename, run_contradiction_scan
from .cli import cmd_sleep_contradictions, eval_t2
