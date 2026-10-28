"""Expansion operations for retrieval."""

from __future__ import annotations

from typing import Any
from pathlib import Path
import json
from ledger.parsing import tokenize


def load_aliases(path: Path) -> dict[str, list[str]]:
    """Load query aliases from JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

    aliases: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for key, values in data.items():
            if not isinstance(values, list):
                continue
            k = str(key).strip().lower()
            if not k:
                continue
            aliases[k] = [str(v).strip() for v in values if str(v).strip()]
    return aliases



def expand_query_tokens(
    query_tokens: set[str],
    aliases: dict[str, list[str]],
) -> tuple[set[str], list[dict[str, Any]]]:
    """Expand query tokens using aliases."""
    expanded = set(query_tokens)
    expansion_events = []

    for token in sorted(query_tokens):
        for phrase in aliases.get(token, []):
            phrase_tokens = tokenize(phrase)
            newly_added = sorted(phrase_tokens - expanded)
            if newly_added:
                expansion_events.append(
                    {
                        "alias": token,
                        "phrase": phrase,
                        "tokens": newly_added,
                    }
                )
            expanded |= phrase_tokens

    return expanded, expansion_events

