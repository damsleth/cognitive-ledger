# From lessons to improvements

The `cj-notes` skill in the separate SKILLS repository routes lessons into
structural checks, skill corrections, durable notes, tasks, or skips. The
ledger tracks recurring issues without adding administrative notes to search.

## Improvement queue

```sh
ledger improvements list --json
ledger improvements show <id>
ledger improvements sync
ledger improvements resolve <id> --artifact <commit-or-file>
ledger improvements resolve <id> --reason "Explanation when no artifact exists"
ledger improvements dismiss <id> --reason "Why this is not actionable"
ledger improvements defer <id> --until 2027-01-01
ledger improvements reopen <id>
```

List/show derive failures from existing signals and never write. Sync saves
observations; transitions also save the current item. Lifecycle events append
to `08_indices/improvements.jsonl`, outside the semantic corpus. Keep this log
to preserve dispositions. Positive patterns remain in `ledger signal patterns`.

Stable IDs use pattern kind and subject. A multiset of matching signal hashes
is the evidence checkpoint: unchanged evidence stays resolved/dismissed; new
matching evidence reopens an item, including identical same-timestamp events.
Unrelated signals do not reopen it. Deleted signals do not erase observations.
Deferrals expire on the given local calendar date. Briefings show up to three
open items and the total count. No tasks or semantic notes are created.

## Instruction audit

Create an explicit YAML manifest (`sources: [{path: ..., scope: dev}]`).
Relative paths resolve beside the manifest; `notes/...` paths resolve against
the configured physical corpus. Directories select Markdown files recursively.
Note frontmatter supplies its scope; other documents use the manifest scope.

```sh
ledger audit instructions --manifest sources.yaml --json
ledger audit instructions --manifest sources.yaml --command 'reviewer --json' --json
ledger audit instructions --manifest sources.yaml --findings reviewed.json --enqueue
```

Without a reviewer the audit reports missing sources and emits a bounded,
redacted evidence bundle, not a semantic verdict. Limits default to 100 files
and 60,000 excerpt characters including excerpt metadata (path metadata is
additional); truncation and omitted files are explicit. Private sections are
removed before line mapping; changed lines carry their original line range.

The optional reviewer receives the prompt on stdin and must emit the JSON
contract in `ledger/instruction_audit.py:audit_prompt`. It is an explicitly
selected executable, not a shell command. Configure tool access in that
executable; ledger does not sandbox it. Use a tool-free reviewer for audits.
No external model is called by default. Reviews must match the current bundle
hash and cite exposed quotes and original line numbers. Conflicts, divergence,
and missing guidance require two source documents. These checks establish
traceability, not semantic correctness: scope interpretation remains reviewable.
`--enqueue` explicitly appends findings to the improvement log; it edits no
instructions or notes. Saved findings are rejected after the bundle changes.
