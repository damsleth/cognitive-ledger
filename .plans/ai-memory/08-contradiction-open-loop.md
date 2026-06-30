# Plan 08 — Contradiction detection → supersession-candidate open loop

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** A2 (STALE supersession-candidate framing; ECon silent-resolution warning). **Gate:** **T2**. **Needs:** Plan 02 (T2/T3 fixtures), Plan 03.

## Status — scanner exists and is wired; the framing needs changing
Already built and wired into `ledger sleep` (step 5b):
- `ledger/contradiction.py` — `cmd_sleep_contradictions(apply=False)` at line 1009, state at `notes/08_indices/contradiction_state.json`, decision enum `IGNORE`/`REVIEW`/`AUTO_SUPERSEDE` (line 98-106), hard rule: identity notes never auto-superseded, scans fact/identity/goals/concepts.
- `ledger/nli.py` — `get_nli_pipeline()` (line 48), `contradiction_score(premise, hypothesis)`; model `mDeBERTa-v3-base-mnli-xnli`.
- Config `ledger/config.py:874-926`: `contradiction_enabled: False`, `_auto_threshold 0.85`, `_review_threshold 0.60`, `_auto_threshold_lang_no 0.95`, `_protect_higher_confidence: True`, `_neighbors_k 8`.

Two changes: **(1) validate before enabling (T2), (2) reframe AUTO_SUPERSEDE → open loop** so a contradiction surfaces as a "needs decision" rather than silently archiving a note (report A2: avoid ECon one-sided resolution; matches AGENTS.md "surface conflicts as explicit open loops").

## Steps

### (1) T2 validation (the gate)
Build a contradiction-eval runner that scores the Plan-02 T2 fixtures through `contradiction_score()` and reports precision/recall at thresholds 0.85 and 0.60, **split by `lang:en` vs `lang:no`**:
```bash
.venv/bin/python -m ledger.contradiction --eval tests/fixtures/contradiction_t2.yaml   # add this eval subcommand
```
Report precision/recall per language per threshold. Expect `lang:no` to fail (no Norwegian XNLI) — that's the finding, not a bug.

### (2) Reframe resolution
- Change the `AUTO_SUPERSEDE` path: instead of calling `supersede()` directly, create an **open-loop note** in `05_open_loops` (`loop__` prefix, `status: open`) describing the supersession candidate ("Note A may be superseded by Note B — needs decision"), linking both notes. `REVIEW` already routes to inbox; consolidate both into the open-loop surface.
- Keep `contradiction_protect_higher_confidence: True`.
- Gate the *only* path that may still call `supersede()` automatically on: `contradiction_enabled AND lang == "en" AND score ≥ auto_threshold AND T2-precision-for-en > 0.95`. If T2 precision ≤ 0.95 for en, **no auto-resolution at all** — everything becomes an open loop. `lang:no` is **always** advisory (open loop only), never auto.

## Acceptance gate — T2
```bash
.venv/bin/python -m ledger.contradiction --eval tests/fixtures/contradiction_t2.yaml
```
**Ship rule (report hard gate):** enable auto-supersession for `lang:en` *only if* precision > 0.95. If precision stays < 0.9 even for en → **contradiction stays advisory-only indefinitely** (open loops, never auto-resolve); ship the open-loop reframing but leave `contradiction_enabled: False` for auto and document the permanent advisory decision.
Regression check that the open-loop reframing doesn't corrupt the curated tier:
```bash
ledger ab run --baseline-ref main --candidate-ref HEAD --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 ; echo $?
```

## Regression risk
**High if auto-archiving is enabled** — the whole point of the gate and the open-loop reframing is to neutralize it. With auto off, risk is low (advisory only). Never auto-resolve `lang:no`. Lean on git history.

## Done when
T2 precision/recall reported per language; AUTO_SUPERSEDE reframed to open-loop "needs decision"; auto-supersession enabled for `lang:en` only if precision>0.95 (else advisory-only documented); `lang:no` always advisory; no retrieval regression.
