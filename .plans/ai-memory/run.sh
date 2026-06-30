#!/usr/bin/env bash
# Orchestration runner for the AI-Memory research plans (see 00-INDEX.md).
# Dispatches plan files to headless Sonnet agents, one git branch per plan,
# respecting the stage DAG. DRY-RUN by default — pass --apply to actually spawn
# agents (expensive, mutates the repo). One plan = one branch = one PR.
set -euo pipefail

# Real Claude Code binary, NOT the headroom `claude` shell alias (bypasses the
# token-compression wrapper, which subagents don't need).
CLAUDE="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
MODEL="${MODEL:-sonnet}"
# acceptEdits lets agents write files but still gates bash (git/ledger/pytest).
# For a fully unattended full-stage run, export PERM=bypassPermissions.
PERM="${PERM:-acceptEdits}"

PLANS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$PLANS_DIR/../.." && pwd)"   # cognitive-ledger repo root

# DAG: stage -> plan numbers. Stage 0 blocks everything below it.
# Within a stage, plans are independent unless their file's "Needs:" says otherwise.
declare -A STAGE=( [0]="01 02 03" [1]="04 05 06 07" [2]="08 09 10 11 12" [3]="13 14 15 16" )

plan_file () { ls "$PLANS_DIR/$1-"*.md 2>/dev/null | head -1; }

dispatch () {  # $1=plan number  $2=apply(0|1)
  local num="$1" apply="$2" f slug branch prompt
  f="$(plan_file "$num")" || true
  [[ -n "${f:-}" && -f "${f:-}" ]] || { echo "  !! no plan file for '$num'"; return 1; }
  slug="$(basename "$f" .md)"; branch="ai-mem/$slug"

  if [[ "$apply" != "1" ]]; then
    echo "  [dry-run] $slug  ->  branch $branch  (model=$MODEL perm=$PERM)"
    return 0
  fi

  prompt="You are a Sonnet agent executing ONE implementation plan inside the cognitive-ledger repo at $REPO.
Follow the plan below EXACTLY. Steps:
1. Create and switch to a git branch named '$branch' off main.
2. Implement the change per the plan's Steps, grounded in the file:line anchors it cites (re-locate by symbol name if a line moved).
3. Run the plan's Acceptance gate verbatim. Use .venv/bin/python for any python/pytest.
4. If 'ledger ab run' returns exit code 2 (regression): STOP, do NOT commit the change, report the regressing deltas. Exit 0/3 is OK to commit.
5. NEVER touch ~/brain live notes — use fixture corpora only. Strip private content from any output.
6. Commit on the branch only if the gate passed. Then report: branch name, gate exit code, before/after metric table, one-paragraph summary.
Shared conventions (eval gate, golden invariants) are in $PLANS_DIR/00-INDEX.md.

===== PLAN: $slug =====
$(cat "$f")
===== END PLAN ====="

  echo "  [dispatch] $slug -> $branch"
  ( cd "$REPO"
    git switch -c "$branch" main 2>/dev/null || git switch "$branch"
    "$CLAUDE" --model "$MODEL" --permission-mode "$PERM" --add-dir "$REPO" \
              --output-format text -p "$prompt"
    git switch main 2>/dev/null || true )
}

cmd="${1:-help}"; arg="${2:-}"; apply=0
[[ "${2:-}" == "--apply" || "${3:-}" == "--apply" ]] && apply=1

case "$cmd" in
  dag)
    for s in 0 1 2 3; do echo "Stage $s: ${STAGE[$s]}"; done ;;
  check)  # self-check: every plan number resolves to exactly one file
    bad=0
    for s in 0 1 2 3; do for n in ${STAGE[$s]}; do
      cnt="$(ls "$PLANS_DIR/$n-"*.md 2>/dev/null | wc -l | tr -d ' ')"
      [[ "$cnt" == "1" ]] || { echo "FAIL: plan $n resolved $cnt files"; bad=1; }
    done; done
    [[ "$bad" == "0" ]] && echo "OK: all 16 plan numbers resolve to one file each" || exit 1 ;;
  plan)
    [[ -n "$arg" && "$arg" != "--apply" ]] || { echo "usage: run.sh plan <NN> [--apply]"; exit 2; }
    dispatch "$arg" "$apply" ;;
  stage)
    [[ -n "$arg" && "$arg" != "--apply" && -n "${STAGE[$arg]:-}" ]] || { echo "usage: run.sh stage <0-3> [--apply]"; exit 2; }
    [[ "$apply" == "1" ]] || echo "(dry-run — pass --apply to actually dispatch)"
    echo "Stage $arg: ${STAGE[$arg]}  [sequential, one branch per plan]"
    for n in ${STAGE[$arg]}; do dispatch "$n" "$apply"; done ;;
  status)
    cd "$REPO"; echo "ai-mem branches (✓ = merged to main):"
    for b in $(git for-each-ref --format='%(refname:short)' refs/heads/ai-mem 2>/dev/null); do
      if git merge-base --is-ancestor "$b" main 2>/dev/null; then echo "  ✓ $b"; else echo "  · $b"; fi
    done ;;
  *)
    cat <<EOF
AI-Memory plan runner. Dispatches plans to headless Sonnet agents (one branch each).
  run.sh dag                 show the stage -> plans map
  run.sh check               verify all plan files resolve (self-check)
  run.sh stage <0-3> [--apply]   dispatch a whole stage (sequential)
  run.sh plan  <NN>  [--apply]   dispatch one plan (e.g. 04)
  run.sh status              list ai-mem/* branches and merge state
Without --apply, prints what it would do. Stage 0 blocks the rest — run it first.
Env: CLAUDE_BIN, MODEL (default sonnet), PERM (acceptEdits|bypassPermissions).
EOF
    ;;
esac
