# Question Playbook

Use this playbook to ask only high-value questions, then write one clear note and synchronize durable memory to the ledger.

## Core Intake (Ask Missing Items Only)

1. What is the note for (meeting, decision, plan, idea, journal, or reference)?
2. Where should it live in `$LEDGER_SOURCE_NOTES_DIR`?
3. What must be captured exactly (facts, dates, names, links)?
4. What outcome should this note support?

## Intent-Specific Questions

### Meeting Note

1. Who attended?
2. What decisions were made?
3. What are next actions, owners, and due dates?

### Project Update

1. What changed since last update?
2. What is blocked and by whom?
3. What is the next milestone and date?

### Decision Log

1. What decision was made?
2. Why was it made?
3. What alternatives were rejected?

### Idea Capture

1. What problem does this idea solve?
2. What is the smallest test?
3. What would make this worth revisiting?

### Journal Entry

1. What happened?
2. What mattered?
3. What follow-up is needed, if any?

## Notes Folder Routing (Default)

Customize these categories to match your notes directory structure:

- Personal/home topics -> `$LEDGER_SOURCE_NOTES_DIR/01-home`
- Work topics -> `$LEDGER_SOURCE_NOTES_DIR/02-work`
- Project-specific topics -> `$LEDGER_SOURCE_NOTES_DIR/03-projects`
- Development topics -> `$LEDGER_SOURCE_NOTES_DIR/04-dev`
- Daily journal -> `$LEDGER_SOURCE_NOTES_DIR/90-journal`
- Temporary scratch -> `$LEDGER_SOURCE_NOTES_DIR/00-tmp`

If destination is ambiguous, propose one folder and ask for confirmation once.

## Note-to-Ledger Mapping

Convert note fragments into atomic ledger writes:

- Stable preference -> `notes/03_preferences/pref__*.md`
- Fact or commitment -> `notes/02_facts/fact__*.md`
- Goal or objective -> `notes/04_goals/goal__*.md`
- New concept/definition -> `notes/06_concepts/concept__*.md`
- Unresolved item / revisit -> `notes/05_open_loops/loop__*.md`

Do not mirror full notes into the ledger. Store only durable atomic memory.
