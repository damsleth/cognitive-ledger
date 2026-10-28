#!/usr/bin/env bash
# Refresh every active project hub (07_projects/project__*.md) from yaams, repos,
# ADO, `now` and new ledger notes, one headless agent run per project.
#
# Writes go through `ledger projects set|log --agent`, so every change is marked
# unreviewed (reviewed: false / "(agent)" log lines) and lands in the timeline;
# review with `ledger unreviewed`. Scheduled by takt (job ledger-projects).
#
# Usage: projects_refresh.sh [slug ...]      (default: all active projects)
# Env:   LEDGER_REFRESH_AGENT=codex|claude   (default codex)
#        LEDGER_REFRESH_MODEL=medium         (agent-bridge tier)
#        LEDGER_BIN=ledger                   (ledger CLI to call)
set -euo pipefail

agent="${LEDGER_REFRESH_AGENT:-codex}"
model="${LEDGER_REFRESH_MODEL:-medium}"
ledger="${LEDGER_BIN:-ledger}"
notes_dir="$("$ledger" paths --field ledger_notes_dir)"

if [ "$#" -gt 0 ]; then
  slugs=("$@")
else
  # ponytail: jq-free JSON read; python is already a dependency of ledger.
  # (No mapfile: macOS /bin/bash is 3.2.)
  slugs=()
  while IFS= read -r slug; do slugs+=("$slug"); done < <("$ledger" projects list --json | python3 -c '
import json, sys
for row in json.load(sys.stdin):
    if row["status"] in ("", "active"):
        print(row["slug"])')
fi

[ "${#slugs[@]}" -gt 0 ] || { echo "no active projects"; exit 0; }

failed=0
for slug in "${slugs[@]}"; do
  echo "== $slug"
  # cd into the corpus so the agent's working directory is the ledger, not a code repo.
  if ! (cd "$notes_dir" && agent-bridge -m "$model" "$agent" - <<EOF
You maintain one project hub in a personal knowledge ledger. Project slug: $slug.
Read it first: \`$ledger projects show $slug\` (frontmatter has status, profile, repos, ado, people, last_activity).

Gather what changed since last_activity (or the last 30 days if empty). Read-only sources, use what applies:
- yaams recall, two passes: \`yaams query "<project name or alias>" --since <last_activity>\`, then without --since only if the first is empty.
- repos listed in frontmatter: \`git -C ~/code/<repo> log --since=<last_activity> --oneline\`.
- ADO ids in frontmatter: \`owa-ado wi <id>\` (read-only).
- open work: \`now -g "<project name>"\`.
- ledger notes pointing at the project: listed under "Connected" in the show output; check their updated dates.
Never send, create, update or delete anything outside the ledger hub.

Then write ONLY through these commands, always with --agent:
- If the state changed: \`$ledger projects set $slug --section State --agent "<2-5 lines: where it stands now>"\`.
- For each notable event (decision, release, blocker, handover), one line:
  \`$ledger projects log $slug --agent --date YYYY-MM-DD "<event> (source: <where you saw it>)"\`.
- Nothing new: write nothing.
Do not change status. If the project looks done or dormant (no activity for 6+ weeks), say so in your reply instead.

Reply with one line per change you made, plus any status suggestion.
EOF
  ); then
    echo "!! $slug failed" >&2
    failed=1
  fi
done
exit "$failed"
