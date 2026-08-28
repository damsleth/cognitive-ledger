"""Cognitive Ledger CLI - installed entry point for the `ledger` command."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from ledger.config import LedgerConfig, get_config
from ledger.validation import validate_query, validate_scope, validate_limit
from ledger.errors import QueryValidationError, ScopeValidationError
from ledger import browse as browse_lib
from ledger import eval as eval_lib
from ledger import query as query_lib
from ledger import semantic as semantic_lib
from ledger.parsing import extract_link_tokens, normalize_section_name, shorten
from ledger.retrieval import (
    now_utc,
    resolve_retrieval_mode,
    resolve_embed_backend,
)

# Module-level config attributes route through get_config() at access time
# (PEP 562). Tests that mutate config via set_config() now affect these.
_LIVE_ATTRS = {
    "SHORTLIST_MIN_CANDIDATES": "shortlist_min_candidates",
    "SHORTLIST_MAX_CANDIDATES": "shortlist_max_candidates",
    "SHORTLIST_LIMIT_MULTIPLIER": "shortlist_limit_multiplier",
    "PROGRESSIVE_RATIONALE_TOP": "progressive_rationale_top",
    "RETRIEVAL_MODES": "retrieval_modes",
}


def __getattr__(name):
    field = _LIVE_ATTRS.get(name)
    if field is not None:
        return getattr(get_config(), field)
    raise AttributeError(f"module 'ledger.cli' has no attribute {name!r}")


def _maybe_print_traceback():
    """Print the current exception traceback to stderr when LEDGER_DEBUG=1."""
    if os.environ.get("LEDGER_DEBUG"):
        print(traceback.format_exc(), file=sys.stderr)


def load_embeddings_module():
    return semantic_lib.load_embeddings_module()


def resolve_embed_model(backend, embed_model):
    return semantic_lib.resolve_embed_model(
        backend,
        embed_model,
        load_embeddings_module_fn=lambda: load_embeddings_module(),
    )


loop_item = browse_lib.loop_item
generic_item = browse_lib.generic_item
read_note = browse_lib.read_note
sorted_items = browse_lib.sorted_items
compact_line = browse_lib.compact_line
compact_loop_line = browse_lib.compact_loop_line
compact_generic_line = browse_lib.compact_generic_line
format_detail = browse_lib.format_detail


def maybe_log_query_telemetry(**_kwargs):
    pass


def rank_query(*args, **kwargs):
    return query_lib.rank_query(
        *args,
        load_embeddings_module=load_embeddings_module,
        resolve_embed_model=resolve_embed_model,
        **kwargs,
    )


def run_eval(
    cases_path,
    k,
    strict_cases=False,
    retrieval_mode="legacy",
    embed_backend="local",
    embed_model=None,
    emit_ranks=False,
):
    return eval_lib.run_eval(
        cases_path=cases_path,
        k=k,
        strict_cases=strict_cases,
        retrieval_mode=retrieval_mode,
        embed_backend=embed_backend,
        embed_model=embed_model,
        rank_query_fn=rank_query,
        emit_ranks=emit_ranks,
    )


def list_items(args, note_type, loop_status=None):
    items = sorted_items(note_type, loop_status=loop_status)
    as_json = bool(getattr(args, "json", False))

    if as_json:
        # Data class: raw document on stdout, no top-level `ok`.
        # `status` lives in frontmatter (BrowseItem has no status attribute),
        # so fall back to it for loops.
        items_out = []
        for item in items[: args.limit]:
            if isinstance(item, dict):
                fm = item.get("frontmatter") or {}
                items_out.append({
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "path": item.get("path"),
                    "status": item.get("status") or fm.get("status"),
                })
            else:
                fm = getattr(item, "frontmatter", None) or {}
                items_out.append({
                    "type": getattr(item, "type", None),
                    "title": getattr(item, "title", None),
                    "path": getattr(item, "path", None),
                    "status": getattr(item, "status", None) or fm.get("status"),
                })
        out = {
            "note_type": note_type,
            "loop_status": loop_status,
            "count": len(items),
            "items": items_out,
        }
        print(json.dumps(out, ensure_ascii=False, default=str))
        return

    if not items:
        print("No notes found.")
        return

    label = note_type.replace("_", " ")
    if note_type == "loops" and loop_status and loop_status != "all":
        label = f"{label} [{loop_status}]"
    print(f"{label} ({len(items)}):")
    for item in items[: args.limit]:
        line = compact_line(
            item,
            width=args.width,
            show_path=args.paths,
            prefix_type=(note_type == "all"),
        )
        print(f"- {line}")


def verbose_items(args, note_type, loop_status=None):
    items = sorted_items(note_type, loop_status=loop_status)
    if not items:
        print("No notes found.")
        return

    for item in items[: args.limit]:
        print(
            f"- {compact_line(item, width=args.width, show_path=args.paths, prefix_type=(note_type == 'all'))}"
        )
        detail_lines = format_detail(item, width=args.width)
        for line in detail_lines:
            print(f"  {line}")
        print("")


def _parse_as_of(raw: str | None):
    """Parse --as-of argument to a timezone-aware datetime or None."""
    if not raw:
        return None
    import datetime as _dt
    # Try full ISO timestamp first.
    try:
        return _dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc
        )
    except ValueError:
        pass
    # Try date-only.
    try:
        d = _dt.date.fromisoformat(raw)
        return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc)
    except ValueError:
        raise ValueError(
            f"--as-of: invalid date {raw!r}. Expected YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ."
        )


def _resolve_query_args_from_profile(args) -> tuple[str, int, str]:
    """Resolve scope, limit, and retrieval_mode by merging profile defaults
    with explicit CLI flags.  Explicit flags always win over profile defaults.
    """
    cfg = get_config()
    profile_name = getattr(args, "profile", None)
    profile = cfg.resolve_profile(profile_name) if profile_name else {}

    _scope_raw = getattr(args, "scope", None)
    scope = _scope_raw if _scope_raw is not None else profile.get("scope", "all")
    # Use `is None` check (not `or`) so explicit 0 is not replaced by the
    # profile default — validate_limit will then catch it as invalid.
    _limit_raw = getattr(args, "limit", None)
    limit = _limit_raw if _limit_raw is not None else profile.get("limit", 8)
    _mode_raw = getattr(args, "retrieval_mode", None)
    retrieval_mode = (
        _mode_raw
        if _mode_raw is not None
        else (profile.get("retrieval_mode") or resolve_retrieval_mode(None))
    )
    return str(scope), int(limit), str(retrieval_mode)


def _warn_if_index_stale(retrieval_mode: str) -> None:
    """Warn when notes have changed since the semantic index was built.

    Only meaningful for semantic modes: those draw candidates from the
    embedding index, so a note that was never embedded cannot be retrieved at
    all, and an edited one is ranked on its stale content. Silence here is what
    makes "I imported it and the query still misses" look like a retrieval
    failure rather than a stale index.
    """
    if "semantic" not in (retrieval_mode or ""):
        return
    try:
        from ledger.embeddings import notes_newer_than_index
        stale = notes_newer_than_index()
    except Exception:
        return
    if not stale:
        return
    shown = ", ".join(stale[:3]) + ("..." if len(stale) > 3 else "")
    print(
        f"warning: {len(stale)} note(s) changed since the semantic index was "
        f"built ({shown}). In {retrieval_mode} the candidate pool comes from "
        f"the index, so a new note is unreachable and an edited one ranks on "
        f"its old content. Rebuild with: ledger sleep index",
        file=sys.stderr,
    )


def handle_query_command(args):
    # Resolve scope/limit/retrieval_mode from profile + explicit flags.
    _scope, _limit, _retrieval_mode = _resolve_query_args_from_profile(args)

    try:
        validated_query = validate_query(args.text)
        validated_scope = validate_scope(_scope)
        validated_limit = validate_limit(_limit, min_val=1, max_val=1000)
    except QueryValidationError as e:
        print(f"error: {e.message}", file=sys.stderr)
        raise SystemExit(2)
    except ScopeValidationError as e:
        print(f"error: {e.message}", file=sys.stderr)
        raise SystemExit(2)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)

    try:
        as_of = _parse_as_of(getattr(args, "as_of", None))
        changed_since = _parse_as_of(getattr(args, "changed_since", None))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)

    _warn_if_index_stale(_retrieval_mode)

    payload = rank_query(
        query=validated_query,
        scope=validated_scope,
        limit=validated_limit,
        aliases_path=get_config().aliases_path,
        retrieval_mode=_retrieval_mode,
        embed_backend=args.embed_backend,
        embed_model=args.embed_model,
        as_of=as_of,
        changed_since=changed_since,
        prf_enabled=True if getattr(args, "prf", False) else None,
    )

    view = getattr(args, "view", "context")
    results = query_lib.payload_results(payload)
    _capture_retrieval_miss(validated_query, results)

    # --- Tier-1 fusion (opt-in: --include-tier1) ---
    # CRITICAL: this block must not be reached unless --include-tier1 is set.
    # No tier1 module import at module level; import happens here lazily.
    if getattr(args, "include_tier1", False):
        from ledger.tier1 import fetch_yaams_results, fuse_results  # noqa: PLC0415

        tier1_results, unavailable = fetch_yaams_results(
            validated_query,
            limit=getattr(args, "tier1_limit", 10),
            min_score=getattr(args, "tier1_min_score", None),
        )
        if unavailable:
            print(
                f"warning: tier-1 unavailable ({unavailable}); showing tier-2 only",
                file=sys.stderr,
            )
        # Convert payload to dict before fusion so fuse_results can operate on it.
        payload = query_lib.query_result_to_json(payload, include_bundle=False, view=view)
        payload = fuse_results(
            payload,
            tier1_results,
            tier2_boost=getattr(args, "tier1_boost", 0.0),
            unavailable_reason=unavailable,
        )
        # Tier-1 entries must never reach --pick / signal capture.
        # results (tier-2 only) was already captured above for _capture_retrieval_miss.
    # --- end tier-1 fusion ---

    if args.json:
        if getattr(args, "include_tier1", False):
            # payload is already a dict (converted above)
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(
                json.dumps(
                    query_lib.query_result_to_json(payload, include_bundle=args.bundle, view=view),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        return

    print(query_lib.format_query_results_human(payload, include_bundle=args.bundle, view=view))

    if getattr(args, "pick", False):
        # Pass only tier-2 results so tier-1 entries cannot be picked.
        _capture_retrieval_hit_pick(validated_query, results)


def handle_answer_command(args):
    """`ledger answer "<question>"` — grounded synthesis with note citations."""
    import json as _json
    from ledger.synthesize import answer as synth_answer

    try:
        validated_query = validate_query(args.text)
        validated_scope = validate_scope(getattr(args, "scope", None) or "all")
        validated_limit = validate_limit(getattr(args, "limit", 5) or 5, min_val=1, max_val=50)
        as_of = _parse_as_of(getattr(args, "as_of", None))
    except (QueryValidationError, ScopeValidationError) as e:
        print(f"error: {e.message}", file=sys.stderr)
        raise SystemExit(2)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)

    result = synth_answer(
        validated_query,
        scope=validated_scope,
        limit=validated_limit,
        as_of=as_of,
        backend=getattr(args, "backend", None),
        use_voice=not getattr(args, "no_voice", False),
    )

    if getattr(args, "json", False):
        print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return

    print(result.answer_body or "(no answer)")
    print(f"\nconfidence: {result.confidence}" + (f" — {result.confidence_reason}" if result.confidence_reason else ""))
    if result.gaps:
        print("gaps:")
        for gap in result.gaps:
            print(f"  - {gap}")
    if result.cited_paths:
        print("sources:")
        for rank, path in zip(result.cited_ranks, result.cited_paths):
            print(f"  [{rank}] {path}")
    print(f"\n(backend: {result.backend}" + (f"/{result.model}" if result.model else "") + ")")


def _capture_retrieval_miss(query, results):
    """Auto-log a retrieval_miss when a query finds nothing useful.

    Gated on ``signals_auto_capture`` to honor the no-noise principle: a
    query that returns no results, or whose best hit scores below the
    configured floor, is recorded as a coverage gap.
    """
    config = get_config()
    if not config.signals_auto_capture:
        return
    top = max(
        (float(query_lib.result_get(r, "score", 0.0) or 0.0) for r in results),
        default=0.0,
    )
    if not results or top < config.signals_miss_score_floor:
        from ledger import signals

        signals.append_signal("retrieval_miss", query=query)


def _capture_retrieval_hit_pick(query, results):
    """Interactively log a retrieval_hit for the result the user picks.

    This is an explicit user action (like ``signal add``), so it logs
    regardless of ``signals_auto_capture``.
    """
    if not results:
        return
    try:
        raw = input(f"which result helped? [1-{len(results)}, enter=none]: ").strip()
    except EOFError:
        return
    if not raw.isdigit():
        return
    n = int(raw)
    if not (1 <= n <= len(results)):
        return
    chosen = results[n - 1]
    rel = str(
        query_lib.result_get(chosen, "rel_path", "")
        or query_lib.result_get(chosen, "path", "")
    )
    if not rel:
        return
    from ledger import signals

    signals.append_signal("retrieval_hit", query=query, note=rel)
    print(f"logged retrieval_hit for {rel}")


def handle_embed_build_command(args):
    import time as _time
    t0 = _time.monotonic()
    try:
        payload = semantic_lib.build_semantic_index(
            target=args.target,
            backend=args.backend,
            model=args.model,
            source_root=args.source_notes_dir,
            text_template=getattr(args, "text_template", None),
            device=getattr(args, "device", None),
            batch_size=getattr(args, "batch_size", None),
            load_embeddings_module_fn=lambda: load_embeddings_module(),
            resolve_embed_model_fn=semantic_lib.resolve_embed_model,
        )
    except Exception as exc:
        _maybe_print_traceback()
        if args.json:
            from ledger.conventions import (
                EXIT_USER_ERROR, action_envelope, emit_action,
            )
            emit_action(action_envelope(
                command="embed build", ok=False,
                error={"code": "embed_build_failed", "message": str(exc)},
                duration_ms=(_time.monotonic() - t0) * 1000.0,
            ))
            raise SystemExit(EXIT_USER_ERROR)
        raise
    if args.json:
        from ledger.conventions import action_envelope, emit_action
        emit_action(action_envelope(
            command="embed build", ok=True,
            stats=dict(payload),
            duration_ms=(_time.monotonic() - t0) * 1000.0,
        ))
        return
    print(semantic_lib.format_embed_build_human(payload))


def handle_embed_status_command(args):
    try:
        payload = semantic_lib.semantic_index_status(
            target=args.target,
            load_embeddings_module_fn=lambda: load_embeddings_module(),
        )
    except Exception as exc:
        _maybe_print_traceback()
        if args.json:
            from ledger.conventions import (
                EXIT_USER_ERROR, data_error, emit_data_error,
            )
            emit_data_error(data_error(
                command="embed status", code="status_failed", message=str(exc),
            ))
            raise SystemExit(EXIT_USER_ERROR)
        raise
    if args.json:
        # Data class: raw document on stdout. The current payload has
        # no top-level `ok`; the conventions test pins the contract.
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(semantic_lib.format_embed_status_human(payload))


def handle_embed_clean_command(args):
    import sys as _sys
    import time as _time

    # Destructive: deletes semantic indices. Require --yes when not on
    # a TTY so an accidental machine invocation can't wipe state.
    yes = bool(getattr(args, "yes", False))
    if not yes and not _sys.stdin.isatty():
        if args.json:
            from ledger.conventions import (
                EXIT_USER_ERROR, action_envelope, emit_action,
            )
            emit_action(action_envelope(
                command="embed clean", ok=False,
                error={
                    "code": "confirmation_required",
                    "message": "embed clean is destructive; pass --yes to confirm",
                    "hint": "ledger embed clean --yes --json",
                },
            ))
            raise SystemExit(EXIT_USER_ERROR)
        print("embed clean requires --yes when not on a TTY.", file=_sys.stderr)
        raise SystemExit(1)

    t0 = _time.monotonic()
    try:
        payload = semantic_lib.clean_semantic_indices(
            target=args.target,
            load_embeddings_module_fn=lambda: load_embeddings_module(),
        )
    except Exception as exc:
        _maybe_print_traceback()
        if args.json:
            from ledger.conventions import (
                EXIT_USER_ERROR, action_envelope, emit_action,
            )
            emit_action(action_envelope(
                command="embed clean", ok=False,
                error={"code": "clean_failed", "message": str(exc)},
                duration_ms=(_time.monotonic() - t0) * 1000.0,
            ))
            raise SystemExit(EXIT_USER_ERROR)
        raise
    if args.json:
        from ledger.conventions import action_envelope, emit_action
        emit_action(action_envelope(
            command="embed clean", ok=True,
            stats=dict(payload),
            duration_ms=(_time.monotonic() - t0) * 1000.0,
        ))
        return
    print(semantic_lib.format_embed_clean_human(payload))


def handle_embed_search_command(args):
    try:
        validated_query = validate_query(args.query)
        validated_limit = validate_limit(args.limit, min_val=1, max_val=100)
    except QueryValidationError:
        raise SystemExit(2)
    except ValueError:
        raise SystemExit(2)
    backend = resolve_embed_backend(args.embed_backend)
    payload = semantic_lib.semantic_search_target(
        validated_query,
        target=args.target,
        limit=validated_limit,
        embed_backend=backend,
        embed_model=args.embed_model,
        allow_api_on_source=args.allow_api_on_source,
        load_embeddings_module_fn=lambda: load_embeddings_module(),
        resolve_embed_model_fn=semantic_lib.resolve_embed_model,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(semantic_lib.format_embed_search_human(payload))


def handle_discover_source_command(args):
    try:
        validated_query = validate_query(args.text)
        validated_limit = validate_limit(args.limit, min_val=1, max_val=1000)
    except QueryValidationError as e:
        print(f"error: {e.message}", file=sys.stderr)
        raise SystemExit(2)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)

    backend = resolve_embed_backend(args.embed_backend)
    result = semantic_lib.semantic_search_source(
        query=validated_query,
        limit=validated_limit,
        source_root=args.source_notes_dir,
        embed_backend=backend,
        embed_model=args.embed_model,
        allow_api_on_source=args.allow_api_on_source,
        load_embeddings_module_fn=lambda: load_embeddings_module(),
        resolve_embed_model_fn=semantic_lib.resolve_embed_model,
    )

    if args.json:
        print(
            json.dumps(
                semantic_lib.source_search_result_to_dict(result),
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    print(semantic_lib.format_source_search_human(result))


def handle_eval_command(args):
    if args.write_baseline:
        ledger_root = get_config().ledger_root
        baseline_path = Path(args.write_baseline).resolve()
        try:
            baseline_path.relative_to(ledger_root)
        except ValueError:
            print(
                f"error: --write-baseline path must be within ledger root ({ledger_root})",
                file=sys.stderr,
            )
            print("hint: use a path like 'notes/08_indices/baseline.json'", file=sys.stderr)
            raise SystemExit(2)

    try:
        backend = resolve_embed_backend(args.embed_backend)
        model = resolve_embed_model(backend, args.embed_model)
        result = run_eval(
            args.cases,
            k=args.k,
            strict_cases=args.strict_cases,
            retrieval_mode=args.retrieval_mode,
            embed_backend=backend,
            embed_model=model,
            emit_ranks=getattr(args, "emit_ranks", False),
        )
    except eval_lib.EvalCaseValidationError as exc:
        print("eval_case_validation_errors:")
        for error in exc.errors:
            print(f"- {error}")
        raise SystemExit(2)

    if getattr(args, "emit_ranks", False):
        for case_row in result.get("per_case", []):
            print(json.dumps(case_row, ensure_ascii=False))
        return

    if args.json:
        baseline_written = None
        if args.write_baseline:
            eval_lib.write_baseline_snapshot(
                result,
                cases_path=args.cases,
                output_path=args.write_baseline,
                generated_at=now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            baseline_written = args.write_baseline
        out = eval_lib.eval_result_to_json(
            result,
            default_k=args.k,
            default_retrieval_mode=args.retrieval_mode,
            embed_backend=backend,
            embed_model=model,
            baseline_path=args.baseline,
            baseline_written=baseline_written,
        )
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    eval_lib.print_eval_result(result)

    if args.baseline:
        cmp = eval_lib.compare_with_baseline(result, args.baseline)
        print(eval_lib.format_baseline_comparison(cmp, k=result["k"]))
        if cmp.get("available") and cmp.get("regressed"):
            raise SystemExit(2)

    if args.write_baseline:
        eval_lib.write_baseline_snapshot(
            result,
            cases_path=args.cases,
            output_path=args.write_baseline,
            generated_at=now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        print(f"baseline_written: {args.write_baseline}")


def handle_context_command(args):
    import time as _time

    context_command = getattr(args, "context_command", None)
    as_json = bool(getattr(args, "json", False))

    if context_command == "build":
        from ledger.context import write_context
        notes_dir = Path(args.ledger_notes_dir)
        output = Path(args.output)
        t0 = _time.monotonic()
        try:
            write_context(output, notes_dir)
        except Exception as exc:
            _maybe_print_traceback()
            if as_json:
                from ledger.conventions import (
                    EXIT_USER_ERROR, action_envelope, emit_action,
                )
                emit_action(action_envelope(
                    command="context build", ok=False,
                    error={"code": "build_failed", "message": str(exc)},
                    duration_ms=(_time.monotonic() - t0) * 1000.0,
                ))
                raise SystemExit(EXIT_USER_ERROR)
            raise
        if as_json:
            from ledger.conventions import action_envelope, emit_action
            emit_action(action_envelope(
                command="context build", ok=True,
                stats={"output": str(output), "notes_dir": str(notes_dir)},
                duration_ms=(_time.monotonic() - t0) * 1000.0,
            ))
        return

    if context_command == "profiles":
        from ledger.context import write_context_profiles
        notes_dir = Path(args.ledger_notes_dir)
        output_dir = Path(args.output_dir)
        t0 = _time.monotonic()
        try:
            write_context_profiles(output_dir, notes_dir)
        except Exception as exc:
            _maybe_print_traceback()
            if as_json:
                from ledger.conventions import (
                    EXIT_USER_ERROR, action_envelope, emit_action,
                )
                emit_action(action_envelope(
                    command="context profiles", ok=False,
                    error={"code": "profiles_failed", "message": str(exc)},
                    duration_ms=(_time.monotonic() - t0) * 1000.0,
                ))
                raise SystemExit(EXIT_USER_ERROR)
            raise
        if as_json:
            from ledger.conventions import action_envelope, emit_action
            emit_action(action_envelope(
                command="context profiles", ok=True,
                stats={"output_dir": str(output_dir), "notes_dir": str(notes_dir)},
                duration_ms=(_time.monotonic() - t0) * 1000.0,
            ))
        return

    from ledger.context import build_context
    from ledger.notes import get_notes

    cfg = get_config()
    notes_dir = cfg.ledger_notes_dir
    fmt = getattr(args, "format", "boot")

    if fmt == "boot":
        print(build_context(notes_dir))

        import io
        from ledger import maintenance as _maint
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            _maint.cmd_status()
        finally:
            sys.stdout = old_stdout
        status_output = buf.getvalue().strip()
        if status_output:
            print("## Maintenance\n")
            print(status_output)
            print()

        signals_path = cfg.signals_path
        if signals_path.is_file():
            try:
                from ledger import signals as sig
                stats = sig.signal_stats(signals_path=signals_path)
                if stats["total"] > 0:
                    print("## Signals\n")
                    print(f"- Total signals: {stats['total']}")
                    if stats["corrections_pending"]:
                        print(f"- Corrections pending review: {stats['corrections_pending']}")
                    misses = stats.get("retrieval_misses", {})
                    if misses:
                        top_miss = next(iter(misses))
                        print(f"- Top retrieval miss: \"{top_miss}\" ({misses[top_miss]}x)")
                    print()
            except Exception:
                _maybe_print_traceback()
    elif fmt == "identity":
        identity = get_notes("identity", notes_dir=notes_dir)
        if not identity:
            print("No identity notes found in notes/01_identity/")
            return
        for note in identity:
            print(f"- `{note.path.name}` - {shorten(note.statement or note.title, 160)}")
    elif fmt == "json":
        from ledger.context import collect_profile_items
        items = collect_profile_items(notes_dir)
        rows = [
            {
                "path": str(item.path),
                "type": item.type,
                "title": item.title,
                "summary": item.summary,
                "score": round(item.score, 4),
                "scope": item.scope,
            }
            for item in sorted(items, key=lambda i: i.score, reverse=True)[:20]
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))


def handle_paths_command(args):
    cfg = get_config()
    payload = {
        "ledger_root": str(cfg.ledger_root),
        "ledger_notes_dir": str(cfg.ledger_notes_dir),
        "source_notes_dir": str(cfg.source_notes_dir),
        "timeline_path": str(cfg.timeline_path),
    }

    field = getattr(args, "field", None)
    if field:
        print(payload[field])
        return

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    for key, value in payload.items():
        print(f"{key}: {value}")


def handle_signal_command(args):
    from ledger import signals as sig

    sub = getattr(args, "signal_command", None)

    if sub == "add":
        entry = sig.append_signal(
            args.type,
            query=args.query or "",
            note=args.note or "",
            detail=args.detail or "",
            rating=args.rating,
            session=args.session or "",
        )
        print(json.dumps(entry, ensure_ascii=False))

    elif sub == "summarize":
        path = sig.write_summary()
        print(f"Signal summary written to {path}")

    elif sub == "stats":
        stats = sig.signal_stats()
        print(f"Total signals: {stats['total']} (real: {stats.get('real_total', stats['total'])})")
        activation = sig.activation_status(
            stats["total"], real_signals=stats.get("real_total")
        )
        print(f"Activation: [{activation.state.value}] {activation.message}")
        print(f"By type: {json.dumps(stats['by_type'], indent=2)}")
        if stats["top_notes"]:
            print("\nTop notes by hit count:")
            for note_path, hits in stats["top_notes"][:5]:
                print(f"  {float(hits):3g} hits  {note_path}")
        if stats["corrections_pending"]:
            print(f"\nNotes with corrections pending review: {stats['corrections_pending']}")
        misses = stats.get("retrieval_misses", {})
        if misses:
            print("\nTop retrieval miss queries:")
            for query, count in list(misses.items())[:5]:
                print(f"  {float(count):3g}x  {query}")

    elif sub == "seed":
        _handle_signal_seed(args)

    elif sub == "purge":
        _handle_signal_purge(args)

    else:
        print("Usage: ledger signal {add|summarize|stats|seed|purge}")
        raise SystemExit(1)


def _handle_signal_seed(args):
    """Implement ``ledger signal seed --from-history / --queries-file``."""
    from ledger import signals as sig
    from ledger.llm_judge import seed_from_queries
    from ledger.layout import indices_dir

    cfg = get_config()
    notes_dir = cfg.ledger_notes_dir
    backend = getattr(args, "backend", None) or cfg.judge_backend
    subprocess_cmd = getattr(args, "judge_command", None) or cfg.judge_subprocess_command
    top_k = getattr(args, "top_k", None) or cfg.judge_seed_top_k
    limit: int | None = getattr(args, "limit", None)

    # Collect queries: --from-history reads query_log.jsonl; --queries-file reads a file
    queries: list[str] = []
    if getattr(args, "from_history", False):
        log_path = indices_dir(notes_dir) / "query_log.jsonl"
        if not log_path.is_file():
            print(
                f"No query history found at {log_path}.\n"
                "Enable query logging first: LEDGER_QUERY_LOG=1 ledger query <q>\n"
                "Or supply queries directly: ledger signal seed --queries-file <path>"
            )
            raise SystemExit(1)
        seen: set[str] = set()
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            q = entry.get("query", "")
            if q and q not in seen:
                seen.add(q)
                queries.append(q)

    queries_file: str | None = getattr(args, "queries_file", None)
    if queries_file:
        p = Path(queries_file)
        if not p.is_file():
            print(f"Queries file not found: {queries_file}")
            raise SystemExit(1)
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(line)

    if not queries:
        print(
            "No queries supplied. Use --from-history or --queries-file <path>."
        )
        raise SystemExit(1)

    if limit is not None and limit > 0:
        queries = queries[:limit]

    print(
        f"Seeding signals for {len(queries)} query(ies) "
        f"(backend={backend!r}, top_k={top_k}) …"
    )

    events = seed_from_queries(
        queries=queries,
        notes_dir=notes_dir,
        top_k=top_k,
        backend=backend,
        subprocess_command=subprocess_cmd,
    )

    signals_path = cfg.signals_path
    for event in events:
        sig.append_signal_raw(event, signals_path)

    print(f"Seeded {len(events)} synthetic signal(s) → {signals_path}")
    if events:
        print("Run 'ledger signal summarize' to refresh signal_summary.json.")


def _handle_signal_purge(args):
    """Implement ``ledger signal purge --synthetic``."""
    from ledger import signals as sig

    if not getattr(args, "synthetic", False):
        print(
            "ledger signal purge requires a scope flag.\n"
            "  --synthetic   remove all LLM-seeded (synthetic) events"
        )
        raise SystemExit(1)

    removed = sig.purge_synthetic_signals()
    print(f"Purged {removed} synthetic signal(s).")
    if removed:
        print("Run 'ledger signal summarize' to refresh signal_summary.json.")


def handle_review_command(args):
    from ledger import review

    if getattr(args, "stats", False):
        print(review.render_dashboard(review.dashboard_data()))
        return

    queue = review.build_review_queue(
        type_filter=getattr(args, "review_type", None),
        scope=getattr(args, "scope", None),
        stale_days=getattr(args, "stale_days", 180),
        limit=getattr(args, "limit", None),
        unjudged_only=getattr(args, "unjudged_only", False),
    )

    if getattr(args, "queue", False):
        if not queue:
            print("Review queue is empty.")
            return
        for i, item in enumerate(queue, 1):
            print(f"{i:3d}. [{item.note_type}] {item.stem}  —  {item.reason}")
        return

    summary = review.run_review_tui(queue)
    if summary["judged"]:
        print(f"Logged {summary['judged']} signal(s) to {summary['signals_path']}")
        print()
    print(review.render_dashboard(review.dashboard_data()))


def handle_init_command(args):
    import time as _time
    from ledger.init import init_ledger

    as_json = bool(getattr(args, "json", False))
    t0 = _time.monotonic()

    try:
        # init_ledger runs `sheep index`, which prints progress to stdout.
        # In --json mode that would corrupt the envelope, so capture it.
        _demo = bool(getattr(args, "demo", False))
        if as_json:
            import io as _io
            from contextlib import redirect_stdout
            with redirect_stdout(_io.StringIO()):
                report = init_ledger(
                    root=args.root,
                    voice_dna_path=args.voice_dna,
                    source_notes_dir=args.source_notes_dir,
                    ledger_notes_dir=args.ledger_notes_dir,
                    demo=_demo,
                )
        else:
            report = init_ledger(
                root=args.root,
                voice_dna_path=args.voice_dna,
                source_notes_dir=args.source_notes_dir,
                ledger_notes_dir=args.ledger_notes_dir,
                demo=_demo,
            )
    except Exception as exc:
        _maybe_print_traceback()
        if as_json:
            from ledger.conventions import (
                EXIT_USER_ERROR, action_envelope, emit_action,
            )
            emit_action(action_envelope(
                command="init", ok=False,
                error={"code": "init_failed", "message": str(exc)},
                duration_ms=(_time.monotonic() - t0) * 1000.0,
            ))
            raise SystemExit(EXIT_USER_ERROR)
        raise

    if as_json:
        from ledger.conventions import action_envelope, emit_action
        ok = not report.get("errors")
        envelope = action_envelope(
            command="init",
            ok=ok,
            stats={
                "created": list(report.get("created") or []),
                "skipped": list(report.get("skipped") or []),
                "errors": list(report.get("errors") or []),
            },
            error=None if ok else {
                "code": "partial_init",
                "message": f"init completed with {len(report['errors'])} error(s)",
            },
            duration_ms=(_time.monotonic() - t0) * 1000.0,
        )
        emit_action(envelope)
        if not ok:
            raise SystemExit(1)
        return

    if report["created"]:
        print("Created:")
        for item in report["created"]:
            print(f"  + {item}")
    if report["skipped"]:
        print("Skipped (already exists):")
        for item in report["skipped"]:
            print(f"  = {item}")
    if report["errors"]:
        print("Errors:")
        for item in report["errors"]:
            print(f"  ! {item}")

    demo_created = [i for i in report.get("created", []) if i.startswith("demo: ")]
    if demo_created:
        print("\nDemo notes created:")
        for item in demo_created:
            print(f"  + {item[6:]}")
        print("  (Delete these once you have created your own notes.)")

    print("\nNext steps:")
    print("  1. Run: ledger paths")
    print("  2. Edit ~/.config/ledger/config.yaml if needed")
    print("  3. Run: ./skills/install-skill.sh")
    print("  4. Create your first note with /notes")


def handle_ingest_command(args):
    import time as _time
    from ledger.ingest import scan_sources, diff_manifest, load_manifest, record_ingest

    sub = getattr(args, "ingest_command", None)
    as_json = bool(getattr(args, "json", False))

    if sub == "scan":
        sources = scan_sources(args.source_notes_dir)
        if as_json:
            print(json.dumps({"sources": list(sources)}, ensure_ascii=False, default=str))
            return
        if not sources:
            print("No source files found.")
            return
        print(f"Sources ({len(sources)}):")
        for s in sources:
            print(f"  {s['path']} ({s['size']} bytes, {s['modified'][:10]})")

    elif sub == "diff":
        manifest = load_manifest()
        scan = scan_sources(args.source_notes_dir)
        d = diff_manifest(manifest, scan)
        if as_json:
            # Reserved-key contract: data success has no top-level `ok`.
            print(json.dumps({
                "new": list(d["new"]),
                "modified": list(d["modified"]),
                "deleted": list(d["deleted"]),
            }, ensure_ascii=False, default=str))
            return
        print(f"New: {len(d['new'])}, Modified: {len(d['modified'])}, Deleted: {len(d['deleted'])}")
        for s in d["new"][:10]:
            print(f"  + {s['path']}")
        for s in d["modified"][:10]:
            print(f"  ~ {s['path']}")
        for s in d["deleted"][:10]:
            print(f"  - {s['path']}")

    elif sub == "record":
        t0 = _time.monotonic()
        try:
            record_ingest(args.source, args.notes, source_root=args.source_notes_dir)
        except Exception as exc:
            _maybe_print_traceback()
            if as_json:
                from ledger.conventions import (
                    EXIT_USER_ERROR, action_envelope, emit_action,
                )
                emit_action(action_envelope(
                    command="ingest record", ok=False,
                    error={"code": "record_failed", "message": str(exc)},
                    duration_ms=(_time.monotonic() - t0) * 1000.0,
                ))
                raise SystemExit(EXIT_USER_ERROR)
            raise
        if as_json:
            from ledger.conventions import action_envelope, emit_action
            emit_action(action_envelope(
                command="ingest record", ok=True,
                stats={"source": args.source, "notes": list(args.notes), "note_count": len(args.notes)},
                duration_ms=(_time.monotonic() - t0) * 1000.0,
            ))
            return
        print(f"Recorded ingest: {args.source} -> {len(args.notes)} note(s)")

    else:
        if as_json:
            from ledger.conventions import data_error, emit_data_error
            emit_data_error(data_error(
                command="ingest", code="usage",
                message="Usage: ledger ingest {scan|diff|record}",
            ))
        else:
            print("Usage: ledger ingest {scan|diff|record}")
        raise SystemExit(1)


def handle_links_command(args):
    from ledger.maintenance import _generate_links_index, _config_paths

    _notes_dir, indices_dir, _timeline = _config_paths()
    links_data, orphans, broken = _generate_links_index(indices_dir)

    if args.note_path:
        entry = links_data.get(args.note_path)
        if entry is None:
            print(f"Note not found in links index: {args.note_path}")
            raise SystemExit(1)
        print(f"Links for {args.note_path}:")
        print(f"  Outgoing ({len(entry['outgoing'])}):")
        for link in entry["outgoing"]:
            print(f"    -> {link}")
        print(f"  Incoming ({len(entry['incoming'])}):")
        for link in entry["incoming"]:
            print(f"    <- {link}")
    else:
        total_links = sum(len(d["outgoing"]) for d in links_data.values())
        print(f"Link graph: {len(links_data)} notes, {total_links} outgoing links")
        print(f"Orphans: {len(orphans)}")
        print(f"Broken links: {len(broken)}")
        if orphans:
            print("\nOrphan notes:")
            for w in orphans[:10]:
                print(f"  {w}")
        if broken:
            print("\nBroken links:")
            for e in broken[:10]:
                print(f"  {e}")


def handle_briefing_command(args):
    import json as _json
    from ledger.briefing import daily_briefing, daily_briefing_data, weekly_review

    if getattr(args, "json", False):
        if args.weekly:
            print(_json.dumps({"error": "--json is not supported for --weekly"}, indent=2))
        else:
            print(_json.dumps(daily_briefing_data(), indent=2, ensure_ascii=False))
    elif args.weekly:
        print(weekly_review())
    else:
        print(daily_briefing())


def handle_changed_command(args):
    """`ledger changed --since DATE [--type T]` — timeline digest of changes."""
    import json as _json
    from ledger.timeline import timeline_since

    try:
        since = _parse_as_of(getattr(args, "since", None))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)
    if since is None:
        print("error: --since DATE is required", file=sys.stderr)
        raise SystemExit(2)

    raw_types = getattr(args, "type", None)
    types = None
    if raw_types:
        types = [t.strip() for t in raw_types.split(",") if t.strip()]

    cfg = get_config()
    events = timeline_since(cfg.timeline_jsonl_path, since, types=types)

    if getattr(args, "json", False):
        print(_json.dumps(events, indent=2, ensure_ascii=False))
        return

    if not events:
        print(f"No changes since {getattr(args, 'since')}.")
        return

    grouped: dict[str, list[dict]] = {}
    for ev in events:
        grouped.setdefault(str(ev.get("action", "other")), []).append(ev)
    print(f"Changes since {getattr(args, 'since')} ({len(events)} events):")
    for action in sorted(grouped):
        rows = grouped[action]
        print(f"\n{action} ({len(rows)}):")
        for ev in sorted(rows, key=lambda e: str(e.get("ts", ""))):
            desc = str(ev.get("desc", "")).strip()
            suffix = f" — {desc}" if desc else ""
            print(f"  {ev.get('ts', '')} | {ev.get('path', '')}{suffix}")


def handle_inbox_command(args):
    import json
    from ledger.inbox import list_inbox, triage_suggestions

    sub = getattr(args, "inbox_command", None)

    if sub == "list":
        items = list_inbox()
        if not items:
            print("Inbox is empty.")
            return
        print(f"Inbox ({len(items)} items):")
        for item in items:
            print(f"  {item['filename']} - {item['title']}")

    elif sub == "triage":
        if not getattr(args, "plain", False):
            from ledger.inbox_triage import run_interactive_triage
            raise SystemExit(run_interactive_triage())
        suggestions = triage_suggestions()
        if not suggestions:
            print("Inbox is empty.")
            return
        print(f"Triage suggestions ({len(suggestions)} items):")
        for s in suggestions:
            print(f"  {s['filename']}")
            print(f"    -> {s['suggested_type']} ({s['reason']})")

    elif sub == "cleanup":
        from ledger.inbox import cleanup_inbox
        result = cleanup_inbox(stale_days=args.days, apply=args.apply)
        orphaned = result["orphaned_locks"]
        stale = result["stale_items"]
        unheld = result.get("unheld_locks") or []
        label = "Removed" if args.apply else "Would remove"
        if not orphaned and not stale and not unheld:
            print("Nothing to clean up.")
            return
        if orphaned:
            print(f"{label} {len(orphaned)} orphaned lock file(s):")
            for f in orphaned:
                print(f"  {f}")
        if unheld:
            print(f"{label} {len(unheld)} unheld lock file(s) across the notes tree:")
            for f in unheld:
                print(f"  {f}")
        if stale:
            print(f"{label} {len(stale)} stale auto-generated item(s):")
            for f in stale:
                print(f"  {f}")
        if not args.apply:
            print("\nRun with --apply to remove these files.")

    elif sub == "reject":
        from ledger.inbox import reject_inbox_item

        try:
            result = reject_inbox_item(
                args.path,
                reason=args.reason,
                remove=not getattr(args, "keep", False),
            )
        except (FileNotFoundError, ValueError) as exc:
            if getattr(args, "json", False):
                payload = {
                    "tool": "ledger",
                    "command": "inbox reject",
                    "ok": False,
                    "error": {"code": "invalid_input", "message": str(exc)},
                }
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(f"ledger inbox reject: {exc}", file=sys.stderr)
            raise SystemExit(1)

        if getattr(args, "json", False):
            payload = {
                "tool": "ledger",
                "command": "inbox reject",
                "ok": True,
                **result,
            }
            print(json.dumps(payload, ensure_ascii=False))
        else:
            verb = "rejected" if result["removed"] else "logged rejection for"
            print(f"{verb} {result['filename']} (reason: {result['reason']})")
            print(f"  logged to {result['rejected_to']}")

    elif sub == "rejected":
        import sys
        from datetime import datetime, timezone
        from ledger.inbox import list_rejections, clear_rejections

        # Parse --since
        since_days: int | None = None
        since_raw = getattr(args, "since", None)
        if since_raw is not None:
            val = since_raw.rstrip("d")
            try:
                since_days = int(val)
            except ValueError:
                print(
                    f"ledger inbox rejected: invalid --since: {since_raw}",
                    file=sys.stderr,
                )
                raise SystemExit(1)

        do_clear = getattr(args, "clear", False)
        before_raw = getattr(args, "before", None)
        do_json = getattr(args, "json", False)
        do_yes = getattr(args, "yes", False)
        do_verbose = getattr(args, "verbose", False)

        if before_raw is not None and not do_clear:
            print(
                "ledger inbox rejected: --before requires --clear",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # Parse --before
        before_dt = None
        if before_raw is not None:
            try:
                before_dt = datetime.strptime(before_raw, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                print(
                    f"ledger inbox rejected: invalid --before date: {before_raw}",
                    file=sys.stderr,
                )
                raise SystemExit(1)

        if do_clear:
            # Confirmation prompt unless --yes or --json.
            if not do_yes and not do_json:
                prompt = "Proceed? [y/N] "
                try:
                    answer = input(prompt).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer not in ("y", "yes"):
                    print("Aborted.")
                    return

            count = clear_rejections(before=before_dt)
            if do_json:
                print(json.dumps({
                    "tool": "ledger",
                    "command": "inbox rejected",
                    "ok": True,
                    "count": count,
                    "records": [],
                }, ensure_ascii=False))
            else:
                print(f"Removed {count} rejection record(s).")
        else:
            records = list_rejections(since_days=since_days)
            if do_json:
                print(json.dumps({
                    "tool": "ledger",
                    "command": "inbox rejected",
                    "ok": True,
                    "count": len(records),
                    "records": records,
                }, ensure_ascii=False))
            else:
                if not records:
                    print("No rejection records found.")
                    return
                print(f"Rejected candidates ({len(records)}):")
                for rec in records:
                    # Format timestamp as YYYY-MM-DD HH:MM
                    ts_raw = rec.get("rejected_at", "")
                    try:
                        ts_dt = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%SZ")
                        ts_display = ts_dt.strftime("%Y-%m-%d %H:%M")
                    except ValueError:
                        ts_display = ts_raw
                    print(f"  {ts_display} {rec.get('filename', '')}")
                    print(f"    id: {rec.get('yaams_candidate_id', '')}  entity: {rec.get('yaams_entity', '')}  reason: {rec.get('reason', '')}")
                    if do_verbose:
                        ids = rec.get("yaams_source_item_ids", [])
                        if ids:
                            print(f"    source_ids: {', '.join(str(i) for i in ids)}")

    elif sub == "conflicts":
        import json as _json
        from ledger.inbox import load_candidates_for_triage
        from ledger.parsing.frontmatter import parse_frontmatter_text

        do_json = getattr(args, "json", False)
        candidates = load_candidates_for_triage()
        conflicts = [c for c in candidates if c.conflict_classification == "contradict"]

        def _extract_statement_section(body: str) -> str | None:
            lines = body.splitlines()
            in_section = False
            section_lines: list[str] = []
            for line in lines:
                if line.strip() == "## Statement":
                    in_section = True
                    continue
                if in_section:
                    if line.startswith("## "):
                        break
                    section_lines.append(line)
            if not section_lines:
                return None
            return "\n".join(section_lines).strip() or None

        if do_json:
            records = []
            for c in conflicts:
                statement = _extract_statement_section(c.body) or c.body.strip()
                records.append({
                    "filename": c.filename,
                    "merge_with": c.merge_with,
                    "conflict_classification": c.conflict_classification,
                    "conflict_confidence": c.conflict_confidence,
                    "conflict_reason": c.conflict_reason,
                    "statement": statement,
                })
            print(_json.dumps({
                "tool": "ledger",
                "command": "inbox conflicts",
                "ok": True,
                "conflicts": records,
            }, ensure_ascii=False))
        else:
            if not conflicts:
                print("No conflict candidates.")
                return
            print(f"Conflict candidates ({len(conflicts)}):")
            for c in conflicts:
                statement = _extract_statement_section(c.body) or c.body.strip()
                print(f"\n  {c.filename}")
                print(f"    classification: {c.conflict_classification}")
                if c.conflict_confidence is not None:
                    print(f"    confidence:     {c.conflict_confidence:.2f}")
                if c.conflict_reason:
                    print(f"    reason:         {c.conflict_reason}")
                print(f"    conflicts with: {c.merge_with or '(unknown)'}")
                print(f"    statement:      {statement[:200]}")

    else:
        print("Usage: ledger inbox {list|triage|cleanup|reject|rejected|conflicts}")
        raise SystemExit(1)


def handle_loops_sync(args) -> None:
    """Run the Things3 ⇄ open-loops sync."""
    from ledger.config import get_config
    from ledger.integrations.things3_sync import LoopInfo, reconcile

    cfg = get_config()

    if not cfg.things3_sync_enabled:
        print(
            "Things3 sync disabled "
            "(set things3_sync_enabled: true in ~/.config/ledger/config.yaml)"
        )
        return

    dry_run = getattr(args, "dry_run", False)

    # Import adapter (will fail clearly if things not on PATH)
    from ledger.integrations import things3 as adapter

    if not adapter.things_available():
        print(
            "ledger loops sync: 'things' CLI not found on PATH.\n"
            "Install from: https://culturedcode.com/things/support/articles/2803573/",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Load loops
    from ledger.notes import get_notes

    notes_dir = cfg.ledger_notes_dir
    all_loops_raw = get_notes("loops", loop_status="all", notes_dir=notes_dir)

    # status/scope frontmatter are enums (LoopStatus/Scope); coerce to their
    # string value so comparisons and scope-routing lookups work.
    def _enum_val(v, default=""):
        return getattr(v, "value", v) if v is not None else default

    # Include only open + blocked loops in the desired set
    active_statuses = {"open", "blocked"}
    active_loops = [
        n for n in all_loops_raw
        if _enum_val(getattr(n.frontmatter, "status", None), "open") in active_statuses
    ]
    # Closed loops are not synced as tasks, but their slugs are needed so a
    # closed loop's task is completed rather than mistaken for a deleted one.
    closed_slugs = {
        n.path.stem for n in all_loops_raw
        if _enum_val(getattr(n.frontmatter, "status", None), "open") == "closed"
    }
    # Closing a loop archives it out of 05_open_loops, which get_notes() is the
    # only place that scans — so the set above misses exactly the loops that
    # were finished properly, and their tasks get flagged "[orphan]" instead of
    # completed. Anything still named loop__*.md in the archive is a retired
    # loop, which is all this needs to know.
    closed_slugs |= {
        p.stem for p in (Path(notes_dir) / "09_archive").glob("loop__*.md")
    }
    # Snoozed loops are neither active nor closed. Without their own set they
    # fall through to the orphan branch and get flagged as if deleted.
    snoozed_slugs = {
        n.path.stem for n in all_loops_raw
        if _enum_val(getattr(n.frontmatter, "status", None), "open") == "snoozed"
    }

    # The Frontmatter model only exposes known schema fields, so custom keys
    # (things_uuid, things_list_id) must be read from the raw YAML.
    from ledger.parsing.frontmatter import parse_frontmatter_text

    def _raw_fm(path) -> dict:
        try:
            meta, _ = parse_frontmatter_text(Path(path).read_text(encoding="utf-8"))
            return meta or {}
        except OSError:
            return {}

    loop_infos: list[LoopInfo] = []
    for ln in active_loops:
        fm = ln.frontmatter
        raw = _raw_fm(ln.path)
        loop_infos.append(LoopInfo(
            slug=ln.path.stem,
            path=str(ln.path),
            title=ln.title or ln.path.stem,
            status=_enum_val(getattr(fm, "status", None), "open"),
            scope=_enum_val(getattr(fm, "scope", None), ""),
            things_uuid=(raw.get("things_uuid") or None),
            updated=str(getattr(fm, "updated", "")),
            list_id=str(raw.get("things_list_id", "") or ""),
        ))

    # Read tasks from Things
    tasks = adapter.read_tasks(marker_prefix=cfg.things3_marker_prefix)

    # Reconcile
    actions = reconcile(
        loop_infos,
        tasks,
        marker_prefix=cfg.things3_marker_prefix,
        scope_routing=cfg.things3_scope_routing or {},
        default_project=cfg.things3_default_project,
        blocked_project=cfg.things3_blocked_project,
        completed_maps_to=cfg.things3_completed_maps_to,
        canceled_maps_to=cfg.things3_canceled_maps_to,
        orphan_action=cfg.things3_orphan_action,
        closed_slugs=closed_slugs,
        snoozed_slugs=snoozed_slugs,
    )

    # Apply actions
    stats = {"create": 0, "update": 0, "reverse": 0, "complete": 0,
             "cancel": 0, "orphan": 0, "noop": 0, "error": 0}

    for action in actions:
        try:
            if action.kind == "noop":
                stats["noop"] += 1
                continue

            elif action.kind == "create":
                new_uuid = adapter.create_task(
                    title=action.things_title or "",
                    notes=_build_task_notes(action.loop_path, action.things_notes or ""),
                    project=action.things_project or "",
                    dry_run=dry_run,
                    marker_prefix=cfg.things3_marker_prefix,
                )
                stats["create"] += 1
                if new_uuid and action.loop_path:
                    _write_things_uuid_to_loop(action.loop_path, new_uuid)
                elif dry_run:
                    print(f"  [dry-run] would write things_uuid to {action.loop_path}")

            elif action.kind == "update":
                adapter.update_task(
                    action.things_uuid,
                    title=action.things_title,
                    notes=action.things_notes,
                    dry_run=dry_run,
                )
                stats["update"] += 1
                if action.new_things_uuid and action.loop_path and not dry_run:
                    _write_things_uuid_to_loop(action.loop_path, action.new_things_uuid)

            elif action.kind in ("reverse_complete", "reverse_cancel"):
                stats["reverse"] += 1
                if action.loop_path and action.new_loop_status:
                    if dry_run:
                        print(
                            f"  [dry-run] would set status={action.new_loop_status} "
                            f"on {action.loop_path}"
                        )
                    else:
                        _write_loop_status(
                            action.loop_path,
                            action.new_loop_status,
                            action.new_things_uuid,
                        )

            elif action.kind == "forward_complete":
                stats["complete"] += 1
                adapter.complete_task(action.things_uuid, dry_run=dry_run)
                if dry_run:
                    print(
                        f"  [dry-run] would complete {action.things_uuid} "
                        f"({action.loop_slug} is closed)"
                    )

            elif action.kind == "forward_cancel":
                stats["cancel"] += 1
                adapter.cancel_task(action.things_uuid, dry_run=dry_run)
                if dry_run:
                    print(
                        f"  [dry-run] would cancel {action.things_uuid} "
                        f"({action.loop_slug} is snoozed)"
                    )

            elif action.kind == "orphan_cancel":
                stats["orphan"] += 1
                adapter.cancel_task(action.things_uuid, dry_run=dry_run)

            elif action.kind == "orphan_flag":
                stats["orphan"] += 1
                if dry_run:
                    print(f"  [dry-run] would flag orphan {action.things_uuid}")
                else:
                    task_data = adapter.get_task_by_uuid(action.things_uuid)
                    if task_data:
                        current_title = task_data.get("title", "")
                        if "[orphan]" not in current_title:
                            adapter.update_task(
                                action.things_uuid,
                                title=f"[orphan] {current_title}",
                            )

        except Exception as exc:
            stats["error"] += 1
            print(
                f"  ERROR ({action.kind} {action.things_uuid or action.loop_slug}): {exc}",
                file=sys.stderr,
            )

    dry_label = " (dry-run)" if dry_run else ""
    total = sum(stats.values())
    print(f"\nThings3 sync{dry_label}: {total} actions")
    for key, count in stats.items():
        if count:
            print(f"  {key:12s}: {count}")


def _build_task_notes(loop_path: str | None, marker: str) -> str:
    """Compose human-readable Things task notes from a loop note.

    The task notes become: the loop's markdown body (readable) + an absolute
    link back to the loop file + the ``ledger:<slug> status:<status>`` marker
    (kept as a machine reference / dedup key on its own trailing line so
    ``_parse_marker`` still finds it).  Falls back to just the marker if the
    file can't be read.
    """
    if not loop_path:
        return marker
    try:
        from ledger.parsing.frontmatter import parse_frontmatter_text
        text = Path(loop_path).read_text(encoding="utf-8")
        _, body = parse_frontmatter_text(text)
        body = (body or "").strip()
        link = f"file://{loop_path}"
        parts = [p for p in (body, f"🔗 {link}", marker) if p]
        return "\n\n".join(parts)
    except OSError:
        return marker


def _write_things_uuid_to_loop(loop_path: str, uuid: str) -> None:
    """Write things_uuid to a loop note's frontmatter."""
    from ledger.io.safe_write import safe_write_text
    from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter

    path = Path(loop_path)
    text = path.read_text(encoding="utf-8")
    fm_dict, body = parse_frontmatter_text(text)
    fm_dict["things_uuid"] = uuid
    new_text = serialize_frontmatter(fm_dict) + "\n" + body
    safe_write_text(path, new_text)


def _write_loop_status(loop_path: str, new_status: str, things_uuid: str | None) -> None:
    """Update status (and optionally things_uuid) in a loop note's frontmatter."""
    from ledger.io.safe_write import safe_write_text
    from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter

    path = Path(loop_path)
    text = path.read_text(encoding="utf-8")
    fm_dict, body = parse_frontmatter_text(text)
    fm_dict["status"] = new_status
    if things_uuid:
        fm_dict["things_uuid"] = things_uuid
    new_text = serialize_frontmatter(fm_dict) + "\n" + body
    safe_write_text(path, new_text)


def handle_notes_add_command(args):
    """Create a new note from the supplied body text."""
    import json
    from ledger.config import get_config
    from ledger.notes.add import AddNoteError, add_note

    cfg = get_config()
    try:
        result = add_note(
            body=args.body,
            note_type=args.type,
            inbox=getattr(args, "inbox", True),
            slug=args.slug,
            title=args.title,
            tags=args.tags,
            links=args.links,
            source=args.source,
            scope=args.scope,
            lang=args.lang,
            confidence=args.confidence,
            config=cfg,
        )
    except AddNoteError as exc:
        if getattr(args, "json", False):
            payload = {
                "tool": "ledger",
                "command": "notes add",
                "ok": False,
                "error": {"code": "invalid_input", "message": str(exc)},
            }
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"ledger notes add: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if getattr(args, "json", False):
        payload = {
            "tool": "ledger",
            "command": "notes add",
            "ok": True,
            **result.to_dict(ledger_notes_dir=cfg.ledger_notes_dir),
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        location = "00_inbox" if result.inbox else result.note_type
        print(f"created {result.path} ({location})")


def handle_import_claude_memory_command(args):
    """Import Claude Code memory files into the ledger (dry run by default)."""
    import json
    from ledger import claude_memory as cm
    from ledger.config import get_config

    cfg = get_config()
    memory_root = (
        Path(args.memory_root).expanduser() if args.memory_root else cm.DEFAULT_MEMORY_ROOT
    )
    mode = "direct" if getattr(args, "direct", False) else "inbox"
    dry_run = not getattr(args, "apply", False)

    result, plan = cm.run_import(
        memory_root=memory_root, mode=mode, dry_run=dry_run, cfg=cfg
    )

    if getattr(args, "json", False):
        payload = {
            "tool": "ledger",
            "command": "import-claude-memory",
            "ok": True,
            "dry_run": result.dry_run,
            "mode": result.mode,
            "files_seen": result.files_seen,
            "folders_scanned": result.folders_scanned,
            "written": result.written,
            "skipped": result.skipped,
            "skipped_already_promoted": len(plan.skipped_promoted),
            "paths": result.written_paths,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return

    if dry_run:
        print(cm.render_report(plan, mode=mode, preview=args.preview))
    else:
        promoted = len(plan.skipped_promoted)
        unchanged = result.skipped - promoted
        msg = f"imported {result.written} note(s) into {mode} (skipped {unchanged} unchanged"
        msg += f", {promoted} already promoted)" if promoted else ")"
        print(msg)
        for n in plan.skipped_promoted:
            print(f"  already promoted, not re-imported: {n.name} -> {n.skip_reason}")
        for rel in result.written_paths:
            print(f"  {rel}")


def handle_voice_dna_command(args):
    from ledger.voice import import_voice_dna, export_voice_dna

    sub = getattr(args, "voice_command", None)

    if sub == "import":
        path = import_voice_dna(args.json_path)
        print(f"Voice DNA imported to {path}")

    elif sub == "show":
        profile = export_voice_dna()
        if profile is None:
            print("No voice DNA profile found.")
            print("Import one with: ledger voice-dna import <json-path>")
            raise SystemExit(1)
        print(json.dumps(profile, indent=2, ensure_ascii=False))

    else:
        print("Usage: ledger voice-dna {import|show}")
        raise SystemExit(1)


def handle_migrate_command(args, migrate_parser):
    migrate_command = getattr(args, "migrate_command", None)

    if migrate_command == "bitemporal":
        from ledger.bitemporal import cmd_migrate_bitemporal
        apply = bool(getattr(args, "apply", False))
        raise SystemExit(cmd_migrate_bitemporal(apply=apply))

    migrate_parser.print_help()
    raise SystemExit(1)


def handle_sleep_command(args):
    from ledger import maintenance as maint
    subargs = getattr(args, "subargs", []) or []
    raise SystemExit(maint.main(subargs))


def handle_mcp_command(args):
    """Launch the Model Context Protocol server over stdio (plan 44)."""
    try:
        from ledger import mcp as mcp_pkg
    except (ImportError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    try:
        mcp_pkg.run(
            allow_write=getattr(args, "allow_write", False),
            with_yaams=getattr(args, "with_yaams", False),
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def handle_web_command(args):
    try:
        from ledger import web as web_pkg
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(2)
    try:
        web_pkg.run(
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(2)


def handle_ab_command(args, ab_parser):
    ab_command = getattr(args, "ab_command", None)

    if ab_command == "run":
        from ledger.ab import main_cli as ab_main
        subargs = getattr(args, "abargs", []) or []
        raise SystemExit(ab_main(subargs))

    if ab_command == "loop":
        from ledger.ab_loop import main_cli as loop_main
        loopargs = getattr(args, "loopargs", []) or []
        raise SystemExit(loop_main(loopargs))

    if ab_command == "charts":
        from ledger.ab_charts import main as charts_main
        charts_main()
        return

    ab_parser.print_help()


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # --doctor is a top-level flag per the CLI contract. Handle it
    # before argparse so it composes with --json without polluting the
    # subparser surface.
    # Only inspect flags before the first positional argument to avoid
    # matching `ledger query "--doctor"` which passes --doctor as a
    # literal query string.
    _pre_positional = []
    for _tok in raw:
        if _tok.startswith("-"):
            _pre_positional.append(_tok)
        else:
            break
    if "--doctor" in _pre_positional:
        from ledger.doctor import emit_doctor
        as_json = "--json" in _pre_positional[:2]
        fix = "--fix" in _pre_positional
        return emit_doctor(None, as_json, fix=fix)

    # argparse.REMAINDER misroutes when nested under subparsers (bpo-9334),
    # so dispatch these passthrough commands before argparse sees them.
    if raw[:2] == ["ab", "run"]:
        from ledger.ab import main_cli as ab_main
        return ab_main(raw[2:])
    if raw[:2] == ["ab", "loop"]:
        from ledger.ab_loop import main_cli as loop_main
        return loop_main(raw[2:])
    if raw[:1] == ["sleep"]:
        from ledger import maintenance as maint
        return maint.main(raw[1:])

    # Config may be in the unsafe state guarded by config._guard_notes_dir
    # (ledger_notes_dir unresolvable → would fall back into the code tree). If
    # so, prime the singleton with an unguarded fallback so parser construction
    # and meta commands (--version, --help) still work — parser building only
    # reads scalar choices, never ledger_notes_dir. `_config_error` is then
    # surfaced as a clean message *before* any subcommand handler runs, so no
    # note is ever written with the poisoned config (see after parse_args).
    _config_error: RuntimeError | None = None
    try:
        cfg = get_config()
    except RuntimeError as exc:
        _config_error = exc
        from ledger.config import set_config
        set_config(LedgerConfig())
        cfg = get_config()
    from ledger import __version__
    parser = argparse.ArgumentParser(description="Cognitive Ledger retrieval helpers")
    parser.add_argument("-v", "--version", action="version", version=f"ledger {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    loops_parser = subparsers.add_parser("loops", help="List loops (default: open)")
    loops_parser.add_argument("loops_command", nargs="?", choices=["sync"], default=None,
                              help="Sub-command: sync (Things3 sync)")
    loops_parser.add_argument("--dry-run", action="store_true",
                              help="Preview sync actions without writing anything (sync only)")
    loops_parser.add_argument("--limit", type=int, default=100)
    loops_parser.add_argument("--width", type=int, default=120)
    loops_parser.add_argument("--paths", action="store_true")
    loops_parser.add_argument("--status", choices=list(cfg.loop_statuses) + ["all"], default="open")
    loops_parser.add_argument("--verbose", action="store_true")
    loops_parser.add_argument("--json", action="store_true", dest="json")

    notes_parser = subparsers.add_parser(
        "notes",
        help="List notes by type, or `notes add ...` to create one",
    )
    notes_parser.add_argument(
        "--type",
        choices=sorted(list(cfg.note_types.keys()) + ["all"]),
        help="Note type to list (required when not using a subcommand)",
    )
    notes_parser.add_argument("--limit", type=int, default=100)
    notes_parser.add_argument("--width", type=int, default=120)
    notes_parser.add_argument("--paths", action="store_true")
    notes_parser.add_argument("--verbose", action="store_true")
    notes_parser.add_argument("--json", action="store_true", dest="json")

    notes_subparsers = notes_parser.add_subparsers(dest="notes_subcommand")
    notes_add_parser = notes_subparsers.add_parser(
        "add",
        help="Create a new note (defaults to 00_inbox; --no-inbox writes to typed folder)",
    )
    # Accept singular + plural; canonical mapping happens inside add_note().
    _add_type_choices = sorted({
        *cfg.note_types.keys(),
        "fact", "pref", "preference", "prefs",
        "goal", "loop", "concept", "id",
    })
    notes_add_parser.add_argument(
        "--type",
        required=True,
        choices=_add_type_choices,
        help="Note type (accepts singular or plural form)",
    )
    notes_add_parser.add_argument(
        "--no-inbox",
        dest="inbox",
        action="store_false",
        default=True,
        help="Write straight to the typed folder, skipping 00_inbox/",
    )
    notes_add_parser.add_argument("--slug", default=None, help="Filename slug override")
    notes_add_parser.add_argument("--title", default=None, help="H1 heading override")
    notes_add_parser.add_argument(
        "--tag", dest="tags", action="append", default=[], help="Frontmatter tag (repeatable)"
    )
    notes_add_parser.add_argument(
        "--link",
        dest="links",
        action="append",
        default=[],
        help="Wikilink target or external link (repeatable)",
    )
    notes_add_parser.add_argument("--source", default="assistant")
    notes_add_parser.add_argument("--scope", default="work")
    notes_add_parser.add_argument("--lang", default="en")
    notes_add_parser.add_argument("--confidence", type=float, default=0.7)
    notes_add_parser.add_argument(
        "--yes",
        action="store_true",
        help="Accepted for API compatibility; writes are non-destructive so confirmation is implicit",
    )
    notes_add_parser.add_argument("--json", action="store_true", dest="json")
    notes_add_parser.add_argument("body", help="The note body text (use quotes for multi-word)")

    query_parser = subparsers.add_parser("query", help="Rank notes for a query")
    query_parser.add_argument("text", help="query text")
    query_parser.add_argument(
        "--profile",
        dest="profile",
        default=None,
        help=(
            "Named query profile from config (e.g. 'work', 'personal', 'dev'). "
            "Overridden by explicit --scope/--limit/--retrieval-mode flags."
        ),
    )
    query_parser.add_argument("--scope", choices=cfg.query_scopes, default=None)
    query_parser.add_argument("--limit", type=int, default=None)
    query_parser.add_argument(
        "--retrieval-mode",
        choices=cfg.retrieval_modes,
        default=None,
        help="Retrieval strategy (default from config; override with LEDGER_RETRIEVAL_MODE)",
    )
    query_parser.add_argument(
        "--embed-backend",
        choices=cfg.embed_backends,
        default=resolve_embed_backend(None),
        help="Embedding backend for semantic_hybrid mode (default: local)",
    )
    query_parser.add_argument(
        "--embed-model",
        default=cfg.embed_model,
        help="Optional embedding model override for semantic_hybrid mode",
    )
    query_parser.add_argument(
        "--view",
        choices=("index", "context", "detail"),
        default="context",
        help="Result detail level: index (compact), context (default), detail (full bodies)",
    )
    query_parser.add_argument("--json", action="store_true", dest="json")
    query_parser.add_argument("--bundle", action="store_true")
    query_parser.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        metavar="DATE",
        help=(
            "Retrieve notes valid at DATE (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ). "
            "Widens the corpus to include 09_archive notes. "
            "Default: current-validity only (expired notes hidden)."
        ),
    )
    query_parser.add_argument(
        "--changed-since",
        dest="changed_since",
        default=None,
        metavar="DATE",
        help=(
            "Only return notes created or updated on/after DATE "
            "(YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ). Composes with --as-of."
        ),
    )
    query_parser.add_argument(
        "--pick",
        action="store_true",
        help="After results, ask which one helped and log a retrieval_hit signal",
    )
    query_parser.add_argument(
        "--prf",
        action="store_true",
        default=False,
        dest="prf",
        help=(
            "Enable Pseudo-Relevance Feedback for this query (semantic_hybrid / "
            "semantic_rerank modes only). Expands the query vector via Rocchio "
            "using the top pseudo-positive and bottom pseudo-negative results. "
            "Default off; enable persistently via prf_enabled: true in config.yaml."
        ),
    )
    query_parser.add_argument(
        "--include-tier1",
        action="store_true",
        default=False,
        dest="include_tier1",
        help=(
            "Fuse YAAMS tier-1 results (iMessage, mail, calendar, …) into the "
            "ranked list via Reciprocal Rank Fusion.  Requires yaams on PATH. "
            "Degrades to tier-2-only with a stderr warning when YAAMS is unavailable."
        ),
    )
    query_parser.add_argument(
        "--tier1-limit",
        type=int,
        default=10,
        dest="tier1_limit",
        metavar="N",
        help="Number of tier-1 (YAAMS) results to fetch (default: 10).",
    )
    query_parser.add_argument(
        "--tier1-boost",
        type=float,
        default=0.0,
        dest="tier1_boost",
        metavar="BOOST",
        help=(
            "Extra RRF score added to every tier-2 result after fusion to "
            "favour tier-2 over tier-1 entries (default: 0.0)."
        ),
    )
    query_parser.add_argument(
        "--tier1-min-score",
        type=float,
        default=None,
        dest="tier1_min_score",
        metavar="SCORE",
        help="Discard tier-1 results whose score is below this threshold.",
    )

    answer_parser = subparsers.add_parser(
        "answer", help="Synthesize a grounded, cited answer to a question"
    )
    answer_parser.add_argument("text", help="question text")
    answer_parser.add_argument("--scope", default=None, help="Scope filter (default: all)")
    answer_parser.add_argument("--limit", type=int, default=5, help="Sources to retrieve (default: 5)")
    answer_parser.add_argument("--as-of", dest="as_of", default=None, metavar="DATE",
                               help="Answer as of DATE (YYYY-MM-DD or full ISO)")
    answer_parser.add_argument("--backend", default=None,
                               help="Override synth backend (dummy|claude|ollama|subprocess)")
    answer_parser.add_argument("--no-voice", action="store_true", dest="no_voice",
                               help="Do not inject the Voice DNA profile into the prompt")
    answer_parser.add_argument("--json", action="store_true", dest="json")

    discover_parser = subparsers.add_parser(
        "discover-source", help="Semantic discovery on source notes (source_only output)"
    )
    discover_parser.add_argument("text", help="query text")
    discover_parser.add_argument(
        "--source-notes-dir",
        dest="source_notes_dir",
        default=str(cfg.source_notes_dir),
        help="Source notes directory for discovery",
    )
    discover_parser.add_argument("--limit", type=int, default=20)
    discover_parser.add_argument(
        "--embed-backend",
        choices=cfg.embed_backends,
        default=resolve_embed_backend(None),
        help="Embedding backend for source discovery (default: local)",
    )
    discover_parser.add_argument(
        "--embed-model",
        default=None,
        help="Optional embedding model override",
    )
    discover_parser.add_argument(
        "--allow-api-on-source",
        action="store_true",
        help="Required with --embed-backend openai to allow source-note API externalization",
    )
    discover_parser.add_argument("--json", action="store_true", dest="json")

    embed_parser = subparsers.add_parser("embed", help="Build, inspect, or clean semantic indices")
    embed_subparsers = embed_parser.add_subparsers(dest="embed_command")

    embed_build_parser = embed_subparsers.add_parser("build", help="Build semantic indices")
    embed_build_parser.add_argument("--target", choices=("ledger", "source", "both"), required=True)
    embed_build_parser.add_argument("--backend", choices=cfg.embed_backends, required=True)
    embed_build_parser.add_argument("--model", default=None)
    embed_build_parser.add_argument(
        "--source-notes-dir",
        dest="source_notes_dir",
        default=str(cfg.source_notes_dir),
        help="Source notes directory (used for target=source|both)",
    )
    embed_build_parser.add_argument(
        "--text-template",
        dest="text_template",
        choices=cfg.embed_text_templates,
        default=None,
        help="Passage/query text template applied at index time. Use 'e5_prefix' for intfloat/e5-* models.",
    )
    embed_build_parser.add_argument(
        "--device",
        choices=cfg.embed_devices,
        default=None,
        help="Embedder device: auto|cpu|mps|cuda (default from config embed_device). "
        "'cpu' dodges the spurious MPS allocator OOM on Apple Silicon.",
    )
    embed_build_parser.add_argument(
        "--batch-size",
        dest="batch_size",
        type=int,
        default=None,
        help="Encode batch size (default from config embed_batch_size). "
        "Lower (e.g. 8) to bound peak GPU memory for large models like bge-m3.",
    )
    embed_build_parser.add_argument("--json", action="store_true", dest="json")

    embed_status_parser = embed_subparsers.add_parser("status", help="Show semantic index status")
    embed_status_parser.add_argument("--target", choices=("ledger", "source", "both"), required=True)
    embed_status_parser.add_argument("--json", action="store_true", dest="json")

    embed_clean_parser = embed_subparsers.add_parser("clean", help="Clean semantic index artifacts")
    embed_clean_parser.add_argument("--target", choices=("ledger", "source", "both"), required=True)
    embed_clean_parser.add_argument("--json", action="store_true", dest="json")
    embed_clean_parser.add_argument(
        "--yes",
        action="store_true",
        dest="yes",
        help="Skip confirmation. Required when stdin is not a TTY (destructive).",
    )

    embed_search_parser = embed_subparsers.add_parser("search", help="Semantic search over a built index")
    embed_search_parser.add_argument("--target", choices=("ledger", "source"), default="ledger")
    embed_search_parser.add_argument("--query", required=True)
    embed_search_parser.add_argument("--limit", type=int, default=5)
    embed_search_parser.add_argument("--embed-backend", dest="embed_backend", choices=cfg.embed_backends, default=None)
    embed_search_parser.add_argument("--embed-model", dest="embed_model", default=None)
    embed_search_parser.add_argument("--allow-api-on-source", action="store_true", dest="allow_api_on_source")
    embed_search_parser.add_argument("--json", action="store_true", dest="json")

    eval_parser = subparsers.add_parser("eval", help="Evaluate retrieval quality against benchmark cases")
    eval_parser.add_argument("--cases", required=True, help="Path to retrieval_eval_cases.yaml")
    eval_parser.add_argument("--k", type=int, default=3)
    eval_parser.add_argument(
        "--retrieval-mode",
        choices=cfg.retrieval_modes,
        default=resolve_retrieval_mode(None),
        help="Retrieval strategy used during eval (default from config; override with LEDGER_RETRIEVAL_MODE)",
    )
    eval_parser.add_argument(
        "--embed-backend",
        choices=cfg.embed_backends,
        default=resolve_embed_backend(None),
        help="Embedding backend for semantic_hybrid mode (default: local)",
    )
    eval_parser.add_argument(
        "--embed-model",
        default=cfg.embed_model,
        help="Optional embedding model override for semantic_hybrid mode",
    )
    eval_parser.add_argument("--strict-cases", action="store_true", help="Fail when eval cases violate strict schema/path rules")
    eval_parser.add_argument("--baseline", help="Optional baseline metrics JSON path for regression check")
    eval_parser.add_argument("--write-baseline", dest="write_baseline", help="Write current metrics snapshot to JSON")
    eval_parser.add_argument("--json", action="store_true", dest="json")
    eval_parser.add_argument(
        "--emit-ranks",
        dest="emit_ranks",
        action="store_true",
        help="Emit per-case rank diagnostics as JSONL on stdout (one line per case).",
    )

    # Context subcommand
    context_parser = subparsers.add_parser("context", help="Output boot context or build context files")
    context_parser.add_argument(
        "--format",
        choices=("boot", "identity", "json"),
        default="boot",
        help="Output format: boot (full payload), identity (identity notes only), json (scored items)",
    )
    context_parser.add_argument(
        "--profile",
        dest="profile",
        default=None,
        help=(
            "Named context profile from config (e.g. 'work', 'personal', 'dev'). "
            "Overrides scope filtering for the context payload."
        ),
    )
    context_subparsers = context_parser.add_subparsers(dest="context_command")

    context_build_parser = context_subparsers.add_parser("build", help="Write context.md index file")
    context_build_parser.add_argument("--ledger-notes-dir", dest="ledger_notes_dir", required=True)
    context_build_parser.add_argument("--output", required=True, help="Path to output markdown file")
    context_build_parser.add_argument("--json", action="store_true", dest="json")

    context_profiles_parser = context_subparsers.add_parser("profiles", help="Write scoped context profile files")
    context_profiles_parser.add_argument("--ledger-notes-dir", dest="ledger_notes_dir", required=True)
    context_profiles_parser.add_argument("--output-dir", dest="output_dir", required=True)
    context_profiles_parser.add_argument("--json", action="store_true", dest="json")

    paths_parser = subparsers.add_parser("paths", help="Show resolved ledger/source paths")
    paths_parser.add_argument(
        "--field",
        choices=("ledger_root", "ledger_notes_dir", "source_notes_dir", "timeline_path"),
        help="Print a single field with no label",
    )
    paths_parser.add_argument("--json", action="store_true", dest="json")

    init_parser = subparsers.add_parser("init", help="Initialize a cognitive ledger")
    init_parser.add_argument("--root", default=None, help="Ledger root directory")
    init_parser.add_argument("--voice-dna", default=None, help="Path to voice-dna JSON file")
    init_parser.add_argument("--source-notes-dir", dest="source_notes_dir", default=None)
    init_parser.add_argument("--ledger-notes-dir", dest="ledger_notes_dir", default=None)
    init_parser.add_argument("--demo", action="store_true", dest="demo", default=False,
                             help="Write 5 sample notes to illustrate ledger structure.")
    init_parser.add_argument("--json", action="store_true", dest="json", help="Emit action envelope on stdout.")

    ingest_parser = subparsers.add_parser("ingest", help="Source ingest pipeline")
    ingest_subparsers = ingest_parser.add_subparsers(dest="ingest_command")
    ingest_scan_parser = ingest_subparsers.add_parser("scan", help="Show new/changed sources")
    ingest_scan_parser.add_argument("--source-notes-dir", dest="source_notes_dir", default=None)
    ingest_scan_parser.add_argument("--json", action="store_true", dest="json")
    ingest_diff_parser = ingest_subparsers.add_parser("diff", help="Detailed diff against manifest")
    ingest_diff_parser.add_argument("--source-notes-dir", dest="source_notes_dir", default=None)
    ingest_diff_parser.add_argument("--json", action="store_true", dest="json")
    ingest_record_parser = ingest_subparsers.add_parser("record", help="Record ingest provenance")
    ingest_record_parser.add_argument("source", help="Source file relative path")
    ingest_record_parser.add_argument("notes", nargs="+", help="Derived note paths")
    ingest_record_parser.add_argument("--source-notes-dir", dest="source_notes_dir", default=None)
    ingest_record_parser.add_argument("--json", action="store_true", dest="json")

    from ledger.importers.cli import build_import_subparser, handle_import_command as _handle_import_command
    import_parser = build_import_subparser(subparsers)

    links_parser = subparsers.add_parser("links", help="Show link graph")
    links_parser.add_argument("note_path", nargs="?", help="Show links for a specific note")

    briefing_parser = subparsers.add_parser("briefing", help="Daily or weekly briefing")
    briefing_parser.add_argument("--weekly", action="store_true")
    briefing_parser.add_argument("--json", action="store_true", dest="json", help="Output as JSON")

    changed_parser = subparsers.add_parser(
        "changed", help="List notes changed since a date (from the timeline log)"
    )
    changed_parser.add_argument("--since", required=True, metavar="DATE",
                                help="YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ")
    changed_parser.add_argument("--type", default=None, metavar="TYPES",
                                help="Comma-separated note types to include (e.g. loops,facts)")
    changed_parser.add_argument("--json", action="store_true", dest="json", help="Output as JSON")

    inbox_parser = subparsers.add_parser("inbox", help="Manage inbox captures")
    inbox_subparsers = inbox_parser.add_subparsers(dest="inbox_command")
    inbox_subparsers.add_parser("list", help="List inbox items")
    inbox_triage_parser = inbox_subparsers.add_parser(
        "triage", help="Review the inbox in a subtraction-model TUI (accept-all by default)"
    )
    inbox_triage_parser.add_argument(
        "--plain",
        action="store_true",
        help="Print type suggestions to stdout instead of launching the TUI",
    )
    cleanup_parser = inbox_subparsers.add_parser("cleanup", help="Remove orphaned locks and stale auto-generated items")
    cleanup_parser.add_argument("--days", type=int, default=14, help="Age threshold for stale items (default: 14)")
    cleanup_parser.add_argument("--apply", action="store_true", help="Actually delete (default is dry-run)")
    reject_parser = inbox_subparsers.add_parser("reject", help="Log a rejection signature and remove an inbox item")
    reject_parser.add_argument("path", help="Inbox filename, logical notes/... path, or absolute path")
    reject_parser.add_argument(
        "--reason",
        choices=("discarded", "duplicate", "merged", "not_durable"),
        default="discarded",
        help="Rejection reason (default: discarded)",
    )
    reject_parser.add_argument(
        "--keep",
        action="store_true",
        help="Log the rejection but do not delete the inbox file",
    )
    reject_parser.add_argument("--json", action="store_true", dest="json", help="Emit action envelope on stdout.")
    rejected_parser = inbox_subparsers.add_parser("rejected", help="List or clear rejection log")
    rejected_parser.add_argument(
        "--since",
        default=None,
        metavar="DAYS",
        help='Filter to last N days, e.g. "30" or "30d"',
    )
    rejected_parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove records from the rejection log",
    )
    rejected_parser.add_argument(
        "--before",
        default=None,
        metavar="YYYY-MM-DD",
        help="With --clear: remove records before this date",
    )
    rejected_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt for --clear",
    )
    rejected_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include full yaams_source_item_ids in output",
    )
    rejected_parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit action envelope on stdout.",
    )

    conflicts_parser = inbox_subparsers.add_parser(
        "conflicts",
        help="List inbox candidates classified as contradictions",
    )
    conflicts_parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit JSON envelope: {tool, command, ok, conflicts:[...]}",
    )

    import_cm_parser = subparsers.add_parser(
        "import-claude-memory",
        help="Import Claude Code memory files into the ledger (dry run by default)",
    )
    import_cm_parser.add_argument(
        "--memory-root",
        dest="memory_root",
        default=None,
        help="Root of Claude memory folders (default: ~/.claude/projects)",
    )
    import_cm_parser.add_argument(
        "--direct",
        action="store_true",
        help="Write straight to typed folders instead of 00_inbox/",
    )
    import_cm_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write notes (default: dry run that writes nothing)",
    )
    import_cm_parser.add_argument(
        "--preview",
        type=int,
        default=4,
        help="Number of full note previews to render in the dry-run report",
    )
    import_cm_parser.add_argument("--json", action="store_true", dest="json")

    voice_parser = subparsers.add_parser("voice-dna", help="Import or show voice DNA profile")
    voice_subparsers = voice_parser.add_subparsers(dest="voice_command")
    voice_import_parser = voice_subparsers.add_parser("import", help="Import voice DNA from JSON")
    voice_import_parser.add_argument("json_path", help="Path to voice-dna JSON file")
    voice_subparsers.add_parser("show", help="Show current voice DNA profile")

    signal_parser = subparsers.add_parser("signal", help="Capture and analyze feedback signals")
    signal_subparsers = signal_parser.add_subparsers(dest="signal_command")

    signal_add_parser = signal_subparsers.add_parser("add", help="Append a signal entry")
    signal_add_parser.add_argument(
        "--type", required=True,
        choices=("retrieval_hit", "retrieval_miss", "correction", "affirmation",
                 "stale_flag", "preference_applied", "rating"),
    )
    signal_add_parser.add_argument("--query", default=None)
    signal_add_parser.add_argument("--note", default=None)
    signal_add_parser.add_argument("--detail", default=None)
    signal_add_parser.add_argument("--rating", type=int, default=None)
    signal_add_parser.add_argument("--session", default=None)

    signal_subparsers.add_parser("summarize", help="Rebuild signal_summary.json")
    signal_subparsers.add_parser("stats", help="Print signal statistics")

    signal_seed_parser = signal_subparsers.add_parser(
        "seed",
        help="Seed synthetic signals from query history via an LLM judge",
    )
    signal_seed_src = signal_seed_parser.add_mutually_exclusive_group()
    signal_seed_src.add_argument(
        "--from-history",
        action="store_true",
        dest="from_history",
        default=False,
        help="Read queries from the query_log.jsonl telemetry file",
    )
    signal_seed_src.add_argument(
        "--queries-file",
        dest="queries_file",
        default=None,
        metavar="PATH",
        help="Plain-text file with one query per line (# lines are comments)",
    )
    signal_seed_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of queries processed (default: all)",
    )
    signal_seed_parser.add_argument(
        "--backend",
        default=None,
        choices=("dummy", "subprocess"),
        help="Judge backend: 'dummy' (lexical heuristic) or 'subprocess' "
             "(configurable LLM command). Defaults to config judge_backend.",
    )
    signal_seed_parser.add_argument(
        "--judge-command",
        dest="judge_command",
        default=None,
        metavar="CMD",
        help="Shell command for subprocess backend (e.g. 'claude -p'). "
             "Overrides config judge_subprocess_command.",
    )
    signal_seed_parser.add_argument(
        "--top-k",
        type=int,
        dest="top_k",
        default=None,
        metavar="K",
        help="Number of notes to retrieve per query (default: config judge_seed_top_k)",
    )

    signal_purge_parser = signal_subparsers.add_parser(
        "purge",
        help="Remove seeded signal events from signals.jsonl",
    )
    signal_purge_parser.add_argument(
        "--synthetic",
        action="store_true",
        default=False,
        help="Remove all synthetic (LLM-seeded) events — full rollback",
    )

    review_parser = subparsers.add_parser(
        "review",
        help="Scan-and-judge notes to emit feedback signals (TUI)",
    )
    review_parser.add_argument(
        "--type",
        dest="review_type",
        default=None,
        help="Restrict to one note type (e.g. facts, preferences); default all",
    )
    review_parser.add_argument(
        "--scope", default=None, help="Restrict to notes matching this scope"
    )
    review_parser.add_argument(
        "--stale-days",
        type=int,
        default=180,
        help="Flag notes older than this many days as stale candidates (default 180)",
    )
    review_parser.add_argument(
        "--limit", type=int, default=None, help="Cap the review queue length"
    )
    review_parser.add_argument(
        "--unjudged-only",
        action="store_true",
        help="Only queue notes that have never received a signal",
    )
    review_parser.add_argument(
        "--stats",
        action="store_true",
        help="Print the signal dashboard and exit (no TUI)",
    )
    review_parser.add_argument(
        "--queue",
        action="store_true",
        help="Print the prioritized queue as text and exit (no TUI)",
    )

    # migrate subcommand
    migrate_parser = subparsers.add_parser(
        "migrate", help="Run one-time idempotent migrations (e.g. bitemporal back-fill)"
    )
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_command")
    migrate_bitemporal_parser = migrate_subparsers.add_parser(
        "bitemporal",
        help="Back-fill valid_from (and valid_to on archive notes) on eligible notes",
    )
    migrate_bitemporal_mode = migrate_bitemporal_parser.add_mutually_exclusive_group()
    migrate_bitemporal_mode.add_argument(
        "--check",
        action="store_true",
        help="Report what would be written without changing any files (default)",
    )
    migrate_bitemporal_mode.add_argument(
        "--apply",
        action="store_true",
        help="Write the back-filled fields and append a timeline entry",
    )

    # sleep subcommand - delegates to ledger.maintenance
    sleep_parser = subparsers.add_parser("sleep", help="Electric Sheep maintenance (sleep, lint, index, status, sync)")
    sleep_parser.add_argument("subargs", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    # ab subcommand - A/B testing and performance charts
    ab_parser = subparsers.add_parser("ab", help="A/B testing and performance charts")
    ab_subparsers = ab_parser.add_subparsers(dest="ab_command")

    ab_run_parser = ab_subparsers.add_parser("run", help="Run A/B retrieval quality harness")
    ab_run_parser.add_argument("abargs", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    ab_loop_parser = ab_subparsers.add_parser("loop", help="Autonomous A/B optimization loop")
    ab_loop_parser.add_argument("loopargs", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    ab_subparsers.add_parser("charts", help="Render A/B performance charts from performance_series.json")

    web_parser = subparsers.add_parser("web", help="Launch the local read-only web UI")
    web_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    web_parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    web_parser.add_argument("--reload", action="store_true", help="Auto-reload on code change (dev)")
    web_parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn log level (default: info)",
    )

    mcp_parser = subparsers.add_parser("mcp", help="Launch the Model Context Protocol server (stdio)")
    mcp_parser.add_argument("--allow-write", action="store_true", dest="allow_write",
                            help="Enable the ledger_remember tool (captures to inbox; default off)")
    mcp_parser.add_argument("--with-yaams", action="store_true", dest="with_yaams",
                            help="Expose a yaams_query tool (requires yaams on PATH)")

    args = parser.parse_args(argv)

    # If config was unsafe, --version/--help already ran during parse_args on
    # the fallback. Any actual subcommand must not proceed on the poisoned
    # config — refuse loudly here, before a single handler (and thus a single
    # note write) can execute.
    if _config_error is not None:
        if args.command is None:
            parser.print_help()
            return 0
        print(f"error: {_config_error}")
        return 2

    def handle_listing_command(command_args):
        if command_args.command == "loops":
            if getattr(command_args, "loops_command", None) == "sync":
                handle_loops_sync(command_args)
                return
            if command_args.verbose:
                verbose_items(command_args, "loops", loop_status=command_args.status)
            else:
                list_items(command_args, "loops", loop_status=command_args.status)
            return

        if command_args.command == "notes":
            if getattr(command_args, "notes_subcommand", None) == "add":
                return handle_notes_add_command(command_args)
            if command_args.type is None:
                notes_parser.error("the following arguments are required: --type")
            if command_args.verbose:
                verbose_items(command_args, command_args.type)
            else:
                list_items(command_args, command_args.type)
            return

    command_handlers = {
        "query": handle_query_command,
        "answer": handle_answer_command,
        "discover-source": handle_discover_source_command,
        "eval": handle_eval_command,
        "context": handle_context_command,
        "paths": handle_paths_command,
    }
    embed_handlers = {
        "build": handle_embed_build_command,
        "status": handle_embed_status_command,
        "clean": handle_embed_clean_command,
        "search": handle_embed_search_command,
    }

    try:
        if args.command in {"loops", "notes"}:
            handle_listing_command(args)
            return 0

        if args.command == "embed":
            embed_handler = embed_handlers.get(args.embed_command)
            if embed_handler is not None:
                embed_handler(args)
                return 0
            embed_parser.print_help()
            return 0

        if args.command == "signal":
            handle_signal_command(args)
            return 0

        if args.command == "review":
            handle_review_command(args)
            return 0

        if args.command == "init":
            handle_init_command(args)
            return 0

        if args.command == "ingest":
            handle_ingest_command(args)
            return 0

        if args.command == "import":
            return _handle_import_command(args, import_parser)

        if args.command == "links":
            handle_links_command(args)
            return 0

        if args.command == "briefing":
            handle_briefing_command(args)
            return 0

        if args.command == "changed":
            handle_changed_command(args)
            return 0

        if args.command == "inbox":
            handle_inbox_command(args)
            return 0

        if args.command == "import-claude-memory":
            handle_import_claude_memory_command(args)
            return 0

        if args.command == "voice-dna":
            handle_voice_dna_command(args)
            return 0

        if args.command == "migrate":
            handle_migrate_command(args, migrate_parser)
            return 0

        if args.command == "sleep":
            handle_sleep_command(args)
            return 0

        if args.command == "ab":
            handle_ab_command(args, ab_parser)
            return 0

        if args.command == "mcp":
            handle_mcp_command(args)
            return 0

        if args.command == "web":
            handle_web_command(args)
            return 0

        handler = command_handlers.get(args.command)
        if handler is not None:
            handler(args)
            return 0

    except RuntimeError as exc:
        print(f"error: {exc}")
        return 2

    parser.print_help()
    return 0
