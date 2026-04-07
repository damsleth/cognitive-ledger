# Ingest Prompt Template

Use this template when distilling a source document into atomic ledger notes.

## Instructions

Read the source below and distill it into 3-8 atomic notes:

1. **Search first**: Check if similar notes already exist in the ledger
2. **One idea per note**: Each note captures a single durable fact, preference, goal, concept, or open loop
3. **Tag with `ingested`**: All notes from an ingest should carry this tag
4. **Link to source**: Include the source path in the Links section
5. **Set provenance**: Use `source: tool` and appropriate confidence

## Source

```
{source_content}
```

## Related Existing Notes

{related_notes}

## Output Format

For each note, create a properly formatted atomic note with:
- Correct type prefix and folder
- Full frontmatter (created, updated, tags, confidence, source, scope, lang)
- Statement, Context, Implications, Links sections
- Run `ledger ingest record <source> <note1> [note2...]` after creating notes
