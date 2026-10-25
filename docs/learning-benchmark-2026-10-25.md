# Learning workflow implementation and keep decisions

Measured on 2026-10-25 against the owner's configured corpus. The package
launcher resolves to this checkout. Model runs used `claude-sonnet-5-5`,
confirmed by a CLI JSON model-usage probe. No retrieval/scoring/config code
changed, so the retrieval A/B gate was not applicable.

## Decision

| Feature | Evidence | Decision |
|---|---|---|
| Lesson routing | All four destination cases already passed without guidance; the first workflow-only test invented a new-note path. An explicit `new:<type>` target contract fixed that in a focused follow-up (candidate 2/2, baseline 0/2). | Keep the small skill workflow and output contract; add no classifier or routing service. No demonstrated gain in destination classification. |
| Improvement lifecycle | Live signals produced two correction-prone issues. Scratch-copy sync observed two, repeated sync changed zero, and resolution survived rebuilding. | Keep. It makes existing evidence actionable without another note/task store. |
| Instruction audit | Initial 60k budget truncated evidence and produced two non-actionable findings. Tightened criteria and larger budgets consistently identified the direct-cron maintenance example versus the owner's scheduler preference. After fixing the example, a complete ten-source rerun found zero issues. | Keep opt-in with explicit manifests. Reject direct model-to-queue automation; only reviewed saved findings can be enqueued. |
| Behaviour evaluation | Eight scenarios repeated twice: baseline 14/16, candidate 16/16. Both improvements came from the owner's scheduling preference. Actual command-fixture checks also passed. | Keep as a descriptive diagnostic; do not make this small agent sample a merge gate. |

## Measurements

Thirty in-process repetitions of the final live-corpus run:

| Operation | Median | p95 |
|---|---:|---:|
| Derive improvement queue | 0.54 ms | 0.74 ms |
| Build instruction evidence bundle | 6.70 ms | 7.59 ms |

The complete bundle selected ten documents, omitted/truncated none, and
produced a 140,001-character audit prompt. The final tool-free model review
took 16.6 seconds. A separate ten-process CLI measurement of
`ledger improvements list --json` had median 69.5 ms and maximum 77.6 ms
(the empirical p95 in a ten-sample set). In-process timings are warm reads,
not cold-storage or end-to-end model latency.

The paired model suite covered four actual preferences (task store,
scheduler, locally developed CLI exposure, and generated-file handling) and
four lesson destinations (guard, skill fix, transient incident, preference).
Only the candidate received explicit note references and routing guidance.
Each case alternated baseline/candidate order across its two repetitions.
The scheduler baseline selected launchd twice; candidate selected takt twice.
The routing-only destination checks were 8/8 in both arms.

The new-note target follow-up used the full capture skill plus the revised
reference, rather than the reference alone. Candidate selected `new:pref`
twice. This demonstrates compliance with a supplied output convention, not
a broad improvement in reasoning. All agent assertions checked JSON fields;
no model judge graded these decisions.

## Validation and corpus boundary

- Full Python suite: 1,700 passed, 3 subtests passed, 5 warnings.
- SKILLS lint passed for the routing workflow.
- Fixture tests cover lifecycle idempotence, same-timestamp recurrence,
  unrelated signals, deferral expiry, private stripping, corrupt logs,
  evidence validation, stale reviews, timeouts, missing context, invalid
  agent JSON, missing-vs-null fields, and disposable command effects.
- Each of four benchmark runs hashed all 1,913 corpus files before/after;
  every run reported identical content. Lifecycle writes used scratch copies.
- No live signals, notes, indexes, improvement dispositions, task systems,
  or scheduler jobs were changed. The maintenance correction edits repository
  instructions only. No package install or configuration activation was needed.

## Reproduce

Use `scripts/benchmark_learning.py --help` with an explicit corpus, source
manifest, new output directory outside that corpus, and optional case suite,
workflow instructions, and model command. For the complete ten-source audit
this run used `--max-chars 250000`. The CLI's bounded default remains 60000;
it reports truncation rather than silently implying complete coverage.

The model command disabled tools, slash commands, MCP discovery, settings
sources, and session persistence, used a JSON-only system prompt, and ran
from disposable working directories. Explicit references bypass retrieval;
these results cannot establish retrieval improvement, autonomous tool-use
compliance, or statistical generalisation from eight selected scenarios.

Raw evidence, manifests, responses, and summaries remain private and ignored
under `.tmp/learning-eval/` in the local checkout. The report commits only
aggregate measurements and findings, not personal source text.
