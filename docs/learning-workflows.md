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
