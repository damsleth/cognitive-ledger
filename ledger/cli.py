"""Cognitive Ledger CLI - installed entry point for the `ledger` command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ledger.config import get_config
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

# Re-exports for test compatibility
from ledger.retrieval import (  # noqa: E402,F811
    build_attention_tokens, candidate_from_note, build_candidates,
    clear_candidate_cache, build_candidate_index,
    retrieve_candidates_from_index, coarse_candidate_score,
    shortlist_candidates,
    prefilter_candidates_by_scope_and_type, score_candidate,
    compute_recency_component, expand_query_tokens,
)
from ledger.eval import (  # noqa: E402,F811
    EvalCaseValidationError, parse_eval_cases, extract_notes_relative_path,
    path_candidates_from_expected, normalize_expected_path,
    validate_eval_cases, print_eval_result, baseline_metrics,
    build_baseline_snapshot, write_baseline_snapshot,
    compare_with_baseline, format_baseline_comparison, eval_result_to_json,
)
from ledger.query import (  # noqa: E402,F811
    bundle_results, format_query_results_human, query_result_to_json,
)


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
        items_out = []
        for item in items[: args.limit]:
            items_out.append({
                "type": getattr(item, "type", None) or item.get("type") if isinstance(item, dict) else None,
                "title": getattr(item, "title", None) or (item.get("title") if isinstance(item, dict) else None),
                "path": getattr(item, "path", None) or (item.get("path") if isinstance(item, dict) else None),
                "status": getattr(item, "status", None) or (item.get("status") if isinstance(item, dict) else None),
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


def handle_query_command(args):
    try:
        validated_query = validate_query(args.text)
        validated_scope = validate_scope(args.scope)
        validated_limit = validate_limit(args.limit, min_val=1, max_val=1000)
    except QueryValidationError as e:
        print(f"error: {e.message}", file=sys.stderr)
        raise SystemExit(2)
    except ScopeValidationError as e:
        print(f"error: {e.message}", file=sys.stderr)
        raise SystemExit(2)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)

    payload = rank_query(
        query=validated_query,
        scope=validated_scope,
        limit=validated_limit,
        aliases_path=get_config().aliases_path,
        retrieval_mode=args.retrieval_mode,
        embed_backend=args.embed_backend,
        embed_model=args.embed_model,
    )

    view = getattr(args, "view", "context")

    if args.json:
        print(
            json.dumps(
                query_lib.query_result_to_json(payload, include_bundle=args.bundle, view=view),
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    print(query_lib.format_query_results_human(payload, include_bundle=args.bundle, view=view))


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
            load_embeddings_module_fn=lambda: load_embeddings_module(),
            resolve_embed_model_fn=semantic_lib.resolve_embed_model,
        )
    except Exception as exc:
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
                pass
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
        print(f"Total signals: {stats['total']}")
        print(f"By type: {json.dumps(stats['by_type'], indent=2)}")
        if stats["top_notes"]:
            print("\nTop notes by hit count:")
            for note_path, hits in stats["top_notes"][:5]:
                print(f"  {hits:3d} hits  {note_path}")
        if stats["corrections_pending"]:
            print(f"\nNotes with corrections pending review: {stats['corrections_pending']}")
        misses = stats.get("retrieval_misses", {})
        if misses:
            print("\nTop retrieval miss queries:")
            for query, count in list(misses.items())[:5]:
                print(f"  {count:3d}x  {query}")

    else:
        print("Usage: ledger signal {add|summarize|stats}")
        raise SystemExit(1)


def handle_init_command(args):
    import time as _time
    from ledger.init import init_ledger

    as_json = bool(getattr(args, "json", False))
    t0 = _time.monotonic()

    try:
        report = init_ledger(
            root=args.root,
            voice_dna_path=args.voice_dna,
            source_notes_dir=args.source_notes_dir,
            ledger_notes_dir=args.ledger_notes_dir,
        )
    except Exception as exc:
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

    print("\nNext steps:")
    print("  1. Run: ledger paths")
    print("  2. Edit config.yaml if needed")
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
    from ledger.briefing import daily_briefing, weekly_review

    if args.weekly:
        print(weekly_review())
    else:
        print(daily_briefing())


def handle_inbox_command(args):
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
        label = "Removed" if args.apply else "Would remove"
        if not orphaned and not stale:
            print("Nothing to clean up.")
            return
        if orphaned:
            print(f"{label} {len(orphaned)} orphaned lock file(s):")
            for f in orphaned:
                print(f"  {f}")
        if stale:
            print(f"{label} {len(stale)} stale auto-generated item(s):")
            for f in stale:
                print(f"  {f}")
        if not args.apply:
            print("\nRun with --apply to remove these files.")

    else:
        print("Usage: ledger inbox {list|triage|cleanup}")
        raise SystemExit(1)


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


def handle_sleep_command(args):
    from ledger import maintenance as maint
    subargs = getattr(args, "subargs", []) or []
    raise SystemExit(maint.main(subargs))


def handle_ab_command(args, ab_parser):
    ab_command = getattr(args, "ab_command", None)

    if ab_command == "run":
        from ledger.ab import main_cli as ab_main
        subargs = getattr(args, "abargs", []) or []
        raise SystemExit(ab_main(subargs))

    if ab_command == "charts":
        from ledger.ab_charts import main as charts_main
        charts_main()
        return

    ab_parser.print_help()


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # --doctor is a top-level flag per mnem CONVENTIONS.md. Handle it
    # before argparse so it composes with --json without polluting the
    # subparser surface.
    if "--doctor" in raw:
        from ledger.doctor import emit_doctor
        as_json = "--json" in raw
        return emit_doctor(None, as_json)

    # argparse.REMAINDER misroutes when nested under subparsers (bpo-9334),
    # so dispatch these passthrough commands before argparse sees them.
    if raw[:2] == ["ab", "run"]:
        from ledger.ab import main_cli as ab_main
        return ab_main(raw[2:])
    if raw[:1] == ["sleep"]:
        from ledger import maintenance as maint
        return maint.main(raw[1:])

    cfg = get_config()
    parser = argparse.ArgumentParser(description="Cognitive Ledger retrieval helpers")
    subparsers = parser.add_subparsers(dest="command")

    loops_parser = subparsers.add_parser("loops", help="List loops (default: open)")
    loops_parser.add_argument("--limit", type=int, default=100)
    loops_parser.add_argument("--width", type=int, default=120)
    loops_parser.add_argument("--paths", action="store_true")
    loops_parser.add_argument("--status", choices=list(cfg.loop_statuses) + ["all"], default="open")
    loops_parser.add_argument("--verbose", action="store_true")
    loops_parser.add_argument("--json", action="store_true", dest="json")

    notes_parser = subparsers.add_parser("notes", help="List notes by type")
    notes_parser.add_argument(
        "--type",
        required=True,
        choices=sorted(list(cfg.note_types.keys()) + ["all"]),
        help="Note type to list",
    )
    notes_parser.add_argument("--limit", type=int, default=100)
    notes_parser.add_argument("--width", type=int, default=120)
    notes_parser.add_argument("--paths", action="store_true")
    notes_parser.add_argument("--verbose", action="store_true")
    notes_parser.add_argument("--json", action="store_true", dest="json")

    query_parser = subparsers.add_parser("query", help="Rank notes for a query")
    query_parser.add_argument("text", help="query text")
    query_parser.add_argument("--scope", choices=cfg.query_scopes, default="all")
    query_parser.add_argument("--limit", type=int, default=8)
    query_parser.add_argument(
        "--retrieval-mode",
        choices=cfg.retrieval_modes,
        default=resolve_retrieval_mode(None),
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

    links_parser = subparsers.add_parser("links", help="Show link graph")
    links_parser.add_argument("note_path", nargs="?", help="Show links for a specific note")

    briefing_parser = subparsers.add_parser("briefing", help="Daily or weekly briefing")
    briefing_parser.add_argument("--weekly", action="store_true")

    inbox_parser = subparsers.add_parser("inbox", help="Manage inbox captures")
    inbox_subparsers = inbox_parser.add_subparsers(dest="inbox_command")
    inbox_subparsers.add_parser("list", help="List inbox items")
    inbox_subparsers.add_parser("triage", help="Suggest target types for inbox items")
    cleanup_parser = inbox_subparsers.add_parser("cleanup", help="Remove orphaned locks and stale auto-generated items")
    cleanup_parser.add_argument("--days", type=int, default=14, help="Age threshold for stale items (default: 14)")
    cleanup_parser.add_argument("--apply", action="store_true", help="Actually delete (default is dry-run)")

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

    # sleep subcommand - delegates to ledger.maintenance
    sleep_parser = subparsers.add_parser("sleep", help="Electric Sheep maintenance (sleep, lint, index, status, sync)")
    sleep_parser.add_argument("subargs", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    # ab subcommand - A/B testing and performance charts
    ab_parser = subparsers.add_parser("ab", help="A/B testing and performance charts")
    ab_subparsers = ab_parser.add_subparsers(dest="ab_command")

    ab_run_parser = ab_subparsers.add_parser("run", help="Run A/B retrieval quality harness")
    ab_run_parser.add_argument("abargs", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    ab_subparsers.add_parser("charts", help="Render A/B performance charts from performance_series.json")

    args = parser.parse_args(argv)

    def handle_listing_command(command_args):
        if command_args.command == "loops":
            if command_args.verbose:
                verbose_items(command_args, "loops", loop_status=command_args.status)
            else:
                list_items(command_args, "loops", loop_status=command_args.status)
            return

        if command_args.command == "notes":
            if command_args.verbose:
                verbose_items(command_args, command_args.type)
            else:
                list_items(command_args, command_args.type)
            return

    command_handlers = {
        "query": handle_query_command,
        "discover-source": handle_discover_source_command,
        "eval": handle_eval_command,
        "context": handle_context_command,
        "paths": handle_paths_command,
    }
    embed_handlers = {
        "build": handle_embed_build_command,
        "status": handle_embed_status_command,
        "clean": handle_embed_clean_command,
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

        if args.command == "init":
            handle_init_command(args)
            return 0

        if args.command == "ingest":
            handle_ingest_command(args)
            return 0

        if args.command == "links":
            handle_links_command(args)
            return 0

        if args.command == "briefing":
            handle_briefing_command(args)
            return 0

        if args.command == "inbox":
            handle_inbox_command(args)
            return 0

        if args.command == "voice-dna":
            handle_voice_dna_command(args)
            return 0

        if args.command == "sleep":
            handle_sleep_command(args)
            return 0

        if args.command == "ab":
            handle_ab_command(args, ab_parser)
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
