# Agentisk YAAMS -> ledger enrichment og todelt evaluering

_Created 2026-08-27_

## Goal

Bygg en reproduserbar, auditerbar og gradvis automatiserbar kjede som:

1. Leser nye, immutable YAAMS-items fra et eksplisitt database-snapshot.
2. Bruker en LLM til å foreslå atomiske ledger-notater med presis kildeproveniens.
3. Kjører mekaniske kontrakt-, dedup-, konflikt- og retrieval-tester.
4. Kjører en separat, adversarial LLM-evaluering av både notatkandidater og svarene ledgeren produserer med kandidatene.
5. Skriver godkjente kandidater til `00_inbox/` i første utrulling, uten å endre YAAMS raw tier eller automatisk mutere eksisterende ledger-notater.
6. Samler menneskelige utfall som nye eval-labels uten at syntetiske LLM-dommer forveksles med ekte brukersignaler.

**Primærmålet er å øke kvantiteten og dekningsgraden i ledgeren kraftig uten å senke kvalitetsgulvet.** Optimaliseringsproblemet er derfor: maksimer antall nye, nyttige og gjenfinnbare fakta som blir representert i ledgeren, gitt harde krav til korrekthet, proveniens, atomicity, dedup, temporal riktighet og privacy.

Sluttmålet er ikke bare «en LLM som skriver flest mulig noter», men heller ikke en så restriktiv admission-gate at nesten ingenting slipper gjennom. Discovery skal ha høy recall; promotion skal ha høy precision. Den lukkede kvalitetsløkken må kunne øke volumet målbart og samtidig bevise at den nye hukommelsen er kildebundet, varig, nyttig, ikke-duplisert og minst like pålitelig som baseline.

### Quantity is a first-class metric

Hver run-rapport skal vise både kvalitet og volum:

- antall nye raw items undersøkt;
- antall distinkte kunnskapsrelasjoner oppdaget;
- kandidat-yield og `NOOP`-rate;
- antall nye fakta som når shadow, inbox og typed ledger;
- coverage mot et kjent gold set;
- antall nye spørsmål ledgeren kan besvare korrekt;
- marginal LLM-kostnad og behandlingstid per akseptert faktum.

En endring som holder precision perfekt ved å avvise alt er ikke en suksess. En endring som dobler antall noter ved å slippe inn tvilsomme eller dupliserte påstander er heller ikke en suksess.

## Terminology: what "agentic" means here

Skill mellom tre nivåer; ellers blir statusbildet misvisende:

- **Mekanisk automatisering:** fast kodeflyt, regler, embeddings og modeller uten verktøyvelgende LLM-løkke.
- **LLM i en pipeline:** LLM-en parser, skriver eller dømmer innenfor et fast steg og et fast skjema.
- **Agentisk lukket løkke:** en tilstandsmaskin velger neste steg, bruker verktøy, evaluerer resultatet, stopper ved gate, kan gjenopptas idempotent og produserer et auditerbart run-resultat.

Dagens system har mye av nivå 1 og 2. YAAMS autoresearch er nær nivå 3 for retrieval-tuning. Den komplette YAAMS -> kandidat -> ledger -> svar -> evaluering -> signal-løkken finnes ikke ennå.

## Current-state audit (2026-08-27)

### What already exists

| Capability | Current implementation | Assessment |
|---|---|---|
| Raw import | YAAMS source adapters, deterministic item IDs, `INSERT OR IGNORE`, embeddings/NER, `yaams refresh` | Automated and correct raw-tier foundation; not LLM-agentic |
| Ledger import | `ledger/ingest.py` scans/diffs sources and prepares an LLM prompt, but does not invoke an LLM or write distilled notes itself | Scaffolding, not an autonomous importer |
| Candidate writing | `yaams promote generate` clusters entity evidence and invokes the configured LLM with `GENERATE_PROMPT`; `from-facts` creates candidates without LLM | Real LLM-authored note candidates already exist |
| Admission | Novelty, identity/open-loop utility, corroboration and provenance trust; semantic dedup; optional LLM conflict classification | Useful fixed pipeline; advisory and not yet evaluated end-to-end |
| Promotion | Human `promote review`; non-interactive `promote commit --candidate/--min-score/--all` writes to ledger inbox | Automation exists, but no integrated quality gate before commit |
| Ranking | BM25/vector fusion, optional cross-encoder rerank, priors and optional signal score; YAAMS has an LLM query parser | Ranking itself is mechanical/ML, not an LLM agent; this is desirable for latency and reproducibility |
| Answering | `yaams query --answer` and `ledger answer` synthesize grounded answers with numbered citations | LLM answering exists as a fixed, source-bound pipeline |
| Signal capture | Query logging, manual hit/miss/correction review, explicit picks/clicks, auto-miss options, ledger synthetic LLM signal seeding | Partial automation; no automatic grading of every live answer |
| Evaluation | `ledger eval`, `ledger ab`, mandatory retrieval gate, YAAMS frozen autoresearch fixture; separate `llm_judge_unjudged.py` with adversarial best-of-N verification | Strong pieces, but mechanical and LLM evaluation are not one shared candidate/answer gate |
| Consolidation | YAAMS session consolidation is deterministic grouping/rendering; Electric Sheep is mainly a maintenance checklist plus deterministic/NLI steps | Not general LLM consolidation today |

### Live YAAMS baseline to preserve

Read-only audit of `/Users/damsleth/brain/feed/data.db` on 2026-08-27:

- 87,445 raw items, 25,464 entities, 124,888 entity links, 590.5 MB.
- Source time range: 2023-08-08 through 2026-08-27.
- 39,390 items are session-consolidated, but raw items remain present.
- 733 raw items are marked as contributing to promoted notes.
- Promotion candidates: 119 accepted, 41 rejected, 674 pending.
- All 674 pending candidates are `chats_facts` candidates created 2026-07-08.
- All current candidates have `admission_score IS NULL`; none has conflict classification. They predate the newer admission/conflict path and must be treated as a legacy backlog, not mass-committed.
- 307 logged queries, 180 judged, 76 parser fallbacks, 12 synthesized answers.
- Ledger signals: 132 real signals, but ranking weight is still `0.0`; activation is `ready`, not active.

### Existing decisions this plan inherits

- YAAMS raw items remain immutable and append-only. Never summarize *instead of* retaining raw data.
- Promotion admission remains advisory during the first rollout. Human acceptance into the durable typed ledger is retained.
- Contradiction means review/supersession candidate, never silent overwrite or archive.
- Norwegian contradiction handling remains advisory until validated on Norwegian examples.
- Cross-repo coupling is through CLI/artifacts, never package imports.
- Retrieval-affecting changes must pass the existing `scripts/ab_gate.sh`/`ledger ab` hard gate.

## Proposed architecture

```text
YAAMS immutable items
        |
        v
frozen selection window + run manifest
        |
        v
LLM proposer -> evidence-bound candidate bundle
        |
        +--> mechanical validator/admission/dedup/conflict shortlist
        |
        v
independent adversarial LLM judge
        |
        v
shadow ledger clone -> index -> retrieval A/B -> answer A/B
        |
        v
gate report: reject | review | inbox-approved
        |
        v
ledger 00_inbox -> human triage -> typed note / merge / reject
        |
        v
human outcome becomes eval label; raw YAAMS data is unchanged
```

Ownership:

- **YAAMS owns:** raw ingestion, the snapshot window, evidence gathering, candidate generation, candidate/run persistence and candidate export.
- **cognitive-ledger owns:** note schema/policy validation, temporary candidate application, retrieval/answer A/B, final inbox/merge/supersession semantics, timeline and indices.
- **Shared contract owns:** versioned candidate bundle and gate report. Both tools continue to work alone and communicate via CLI/JSON/files.

## First vertical slice - exhaustive abbreviation facts

Forkortelser er første tracer bullet for hele arkitekturen. De er mange nok til å øke ledger-volumet merkbart, avgrensede nok til å enumerere, og enkle å evaluere med spørsmål som «Hva står X for?» og «Hva betyr X i kontekst Y?».

### Current abbreviation surface

Read-only måling 2026-08-27:

- YAAMS har 25,471 entities; 1,049 av dem har minst ett alias og til sammen 1,233 aliasverdier.
- Konfigurasjonsordboken har 1,044 entries og 1,134 aliasverdier.
- 130 aliasverdier er korte single-token-kandidater på 2-12 tegn.
- 921 av entity-radene med alias er personer og 77 er organisasjoner. Mange aliaser er derfor kallenavn, e-postadresser, telefonnumre eller andre identifikatorer—not forkortelser.
- Ingen entities er eksplisitt tagget som `acronym`/`abbreviation`, og ingen tilsvarende metadatafelter finnes ennå.
- Bare fire eksisterende ledger-fakta traff en enkel søkestreng for forkortelse/akronym. Det finnes altså mye potensiell coverage mellom YAAMS' aliasgraf og ledgerens eksplisitte fakta.
- Live entity-antallet flyttet seg mens denne planen ble revidert. Det understreker at discovery/eval må bruke et frosset snapshot, ikke løpende tellinger.

YAAMS bruker allerede aliasene til dictionary tagging og query-time synonym expansion. Det gjør søk bedre, men det er ikke det samme som et eksplisitt, kontekstbærende faktum. Et ledger-faktum kan for eksempel bevare at `SP` i NOCOS vanligvis betyr `serviceprovider`, mens `SP` i en annen kontekst kan bety `SharePoint`.

### Discovery contract

- [ ] Enumerer alle aliasrelasjoner fra YAAMS entities/config, men behold den flate source-taxonomien. Forkortelse er en avledet kunnskapsrelasjon, ikke en ny source-type.
- [ ] Mine eksplisitte mønstre i immutable raw items og øvrige allerede benyttede kilder:
  - `Lang form (KORT)` / `KORT (lang form)`;
  - `KORT = lang form`;
  - «KORT står for ...», «forkortes ...», «kalles ...»;
  - repo-glossarer, README-er, konfigurasjonsordbøker og etablerte domeneordlister som allerede inngår i kildegrunnlaget.
- [ ] Normaliser til en kandidatrelasjon:
  - `short_form`, `long_form`, `context/domain`;
  - `relation_type`: `acronym|initialism|abbreviation|shorthand|code|nickname|identifier|unknown`;
  - kilde-item-ID-er, eksplisitte evidensspans og observerte varianter;
  - språk, first/last seen og om betydningen er kontekstavhengig.
- [ ] Discovery skal være bevisst high-recall. Telefonnumre, e-postadresser, profilalias, personkallenavn og didcodes får være kandidater, men klassifiseres før promotion i stedet for å bli forkastet i discovery.

### Promotion policy

- [ ] Skriv bare `fact` når relasjonen er bevist. Et YAAMS-alias beviser ekvivalens mellom surface forms, men beviser ikke alene at relasjonen semantisk er en forkortelse.
- [ ] Godta som sterk evidens:
  - en eksplisitt definisjon i en pålitelig kilde;
  - en kuratert aliasrelasjon kombinert med entydig kortform og støttende bruk;
  - minst to uavhengige, konsistente kontekster som kobler kort og lang form.
- [ ] Én global forkortelse kan bare opprettes når betydningen faktisk er global. Ved kollisjon skal det skrives kontekstspesifikke fakta eller ett eksplisitt ambiguity-faktum—not en lossy global aliasregel.
- [ ] Kandidater av typen `nickname|identifier|unknown` går til `NOOP` for abbreviation-lanen. De kan senere behandles av en annen faktatype uten at discovery-arbeidet går tapt.
- [ ] Codes/didcodes må ikke automatisk slås sammen med prosjektet eller organisasjonen de peker mot. De kan bli egne fakta dersom mappingen er kildebelagt og nyttig.
- [ ] Foreslått filform er `02_facts/fact__abbreviation_<short>__<context>.md`, med tags som `abbreviation`, `glossary` og domene. Bruk ordinær frontmatter inntil eventuelle nye felter er godkjent i `schema.yaml`.
- [ ] Statement skal være søkbar og eksplisitt, for eksempel: «I NOCOS betyr `SP` normalt `serviceprovider`, ikke `SharePoint`.» Body skal bevare kontekst, alternative betydninger og YAAMS-kildeproveniens.
- [ ] Sensitive personforkortelser kan være gyldige fakta, men følger normal scope/privacy-policy og går gjennom menneskelig review i første rollout.

### Abbreviation gold set and A/B

- [ ] Manuelt klassifiser de 130 korte YAAMS-aliasene som første komplette gold set. Dette er lite nok til å gjøre exhaustively og stort nok til å måle recall/precision.
- [ ] Suppler gold-settet med eksplisitte forkortelser funnet i raw-pattern-mining, inkludert negative eksempler som telefonnummer, kallenavn, tilfeldige uppercase tokens og ambiguous short forms.
- [ ] Mål:
  - discovery recall over alle validerte forkortelser;
  - promotion precision og false-fact rate;
  - korrekt `relation_type` og kontekst;
  - ambiguity recall—systemet må oppdage kollisjoner, ikke skjule dem;
  - duplicate rate mot eksisterende ledger;
  - antall nye validerte abbreviation-fakta og nye korrekt besvarte glossary-spørsmål;
  - kostnad per promotert faktum.
- [ ] Generer eval-spørsmål per godkjent mapping: «Hva står X for?», «Hva betyr X i Y?» og negative/ambiguous varianter. Kjør baseline-ledger mot shadow-enriched ledger med både mekanisk retrieval-A/B og blind answer-A/B.
- [ ] Før auto-to-inbox kreves minst 0.98 precision på abbreviation-fakta, 100% source-ID-validitet, null ukontekstualiserte ambiguity-feil og ingen regressjon i canonical retrieval. Lavere confidence kan fortsatt gi høyere kvantitet gjennom batch-review, men ikke gjennom automatisk promotion.

### Generalization value

Når forkortelseslanen fungerer, generaliseres den samme discover -> classify -> evidence -> promote -> dual-eval-malen til andre høyvolumskategorier: personer/roller, systemnavn, prosjektterminologi, steder, organisasjonsrelasjoner, datoer/frister og stabile arbeidskonvensjoner. Hver kategori får egen relation schema, gold set og quality floor; de deler run state, provenance og eval-infrastruktur.

## Steps

### Phase 0 - Reconcile and freeze the baseline

- [x] Reconcile stale plan evidence before coding. In particular, `.plans/ai-memory/06-bitemporal-event-time.md` still says YAAMS event-time wiring is missing, while `enrich_candidate_event_time()` and its tests now implement the source-derived `valid_from` path. Mark implemented substeps as done; retain still-open temporal query/ranking work. _(2026-08-30: plan 06 + index reconciled — (a)/(c) shipped, (b) auto-as-of open.)_
- [x] Freeze a consistent read-only YAAMS scenario with SQLite backup semantics, not a raw file copy while WAL writes may be active. Keep the private DB outside Git. _(2026-08-30: `yaams scripts/promotion_freeze.py` → `~/brain/promotion_fixture.db`.)_
- [x] Write a non-sensitive scenario manifest containing DB schema version, file hash, item count, max `ingested_at`, max raw row/item boundary, source counts, candidate counts, query-feedback hash, YAAMS commit, ledger commit and config hashes with secrets removed. _(2026-08-30: `yaams scripts/promotion_scenario.json`.)_
- [ ] Define train/dev/holdout splits before evaluating a new design:
  - time split by `ingested_at`, not source `timestamp`, so late-arriving historic data is included exactly once;
  - entity/thread grouping to prevent near-identical messages leaking across splits;
  - a frozen holdout that no proposer prompt or tuning agent sees.
- [x] Snapshot the current 834 promotion candidates as immutable baseline evidence. Do not change their statuses during harness development. _(2026-08-30: in the fixture; `candidate_hash` in the manifest detects status drift.)_
- [x] Snapshot YAAMS entity/config aliases and materialize the 130 short-form candidates as the first abbreviation gold-set worksheet. Do not write abbreviation notes yet. _(2026-08-30: `promotion_freeze.py --worksheet` → `~/brain/promotion_abbrev_worksheet.csv`, 130 rows, unlabeled.)_
- [x] Add a one-command verification that the scenario manifest still matches the private fixture, analogous to YAAMS `autoresearch_freeze.py --check`. _(2026-08-30: `promotion_freeze.py --check`.)_

**Exit condition:** Two consecutive baseline runs on the same snapshot produce identical candidate selection and identical mechanical metrics, apart from explicitly excluded wall-clock values.

### Phase 1 - Add run-level provenance and a candidate state machine

- [ ] Add a YAAMS migration for `promotion_runs`:
  - `run_id`, `created_at`, `snapshot_id`, `selection_start`, `selection_end`;
  - proposer backend/model, prompt version/hash, temperature/seed where supported;
  - config hash, source/item-set hash, status, duration, token/cost counters;
  - parent/baseline run ID for A/B comparisons.
- [ ] Add additive candidate fields rather than breaking contract v1:
  - `run_id`, `candidate_schema_version`, `proposed_action` (`ADD|MERGE|SUPERSEDE|NOOP|REVIEW`);
  - `target_path`, `evidence_map`, `generator_confidence`;
  - mechanical validation/admission result;
  - LLM judge verdict/version/model;
  - gate status and final human disposition.
- [ ] Store only source item IDs, content hashes and bounded evidence offsets/excerpts needed for audit. Do not duplicate the full YAAMS raw payload into candidate/eval logs.
- [ ] Define a resumable state machine:
  - `drafted -> mechanically_validated -> llm_validated -> shadow_passed -> inbox_written -> human_accepted|human_rejected|merged`;
  - any stage may become `needs_review|failed` with a structured reason;
  - rerunning a completed stage with the same inputs is a no-op.
- [ ] Use an ingestion cursor based on `(ingested_at, id)` or an explicit snapshot item-set, never only the source event timestamp. Backfills can insert old events today.
- [ ] Add `yaams promote export --run-id ... --jsonl` and a JSON Schema for the bundle. Keep the existing Markdown inbox contract as the final handoff, not the internal eval format.

**Exit condition:** A run can be interrupted after each stage, resumed without duplicate candidates, and reconstructed from hashes and IDs without consulting chat history.

### Phase 2 - Turn current drafting into evidence-bound LLM ingestion

- [ ] Refactor the existing `GENERATE_PROMPT` path rather than creating a second generator.
- [ ] Replace one-note-per-entity assumptions with an output list of zero or more atomic proposals per evidence window. A busy entity cluster may contain unrelated claims; a weak cluster may deserve `NOOP`.
- [ ] Require structured output for every proposed note:
  - type, title, one-sentence statement, body, tags, language and scope suggestion;
  - proposed action and optional existing-note target;
  - source item IDs for the complete note and a claim-to-evidence map for each factual sentence;
  - event-time proposal plus whether it came from an explicit source timestamp or inference;
  - a short durability rationale: why this will likely matter beyond today's conversation.
- [ ] Validate that every cited item ID exists in the frozen selection and that evidence offsets/hashes match. Unknown or out-of-window sources are a hard rejection.
- [ ] Make the proposer abstain explicitly. `NOOP` is a successful outcome and should be measured; it prevents the firehose from becoming ledger noise.
- [ ] Keep source snippets and nearest existing ledger notes separate in the prompt so the LLM cannot confuse old memory with new evidence.
- [ ] Derive final `confidence` mechanically from evidence/provenance/corroboration and verifier results. Do not copy an LLM's self-reported confidence directly into frontmatter.
- [ ] Preserve the current event-time behavior: trusted source timestamps may populate `valid_from`; inferred timestamps only emit low-confidence provenance.
- [ ] Add prompt-injection tests where source messages tell the model to ignore rules, invent a note, reveal private data or write outside the bundle schema.
- [ ] Add privacy policy per backend. External LLM use must be explicit in config, strip `<private>` fences, minimize source excerpts and record which backend saw which item IDs. A local backend remains a supported option.

**Exit condition:** On a labeled fixture, every emitted factual sentence has valid evidence, invalid structured output fails closed, and reruns never mutate raw items.

### Phase 3 - Mechanical admission and legacy backlog handling

- [ ] Reuse the current admission factors—novelty, identity/open-loop utility, corroboration and provenance trust—but version the formula and store the complete factor breakdown.
- [ ] Add hard mechanical checks before any LLM judge call:
  - schema/frontmatter and filename validity;
  - allowed type/scope/language/status values;
  - source IDs exist and match hashes;
  - note body contains exactly one primary claim;
  - private-fence stripping and forbidden-field checks;
  - duplicate/merge nearest-neighbor search against the exact baseline ledger index;
  - target paths exist and target statement hashes have not drifted;
  - `ledger sleep lint` passes when the candidate is rendered in a temporary corpus.
- [ ] Keep admission score advisory. A low score routes to `needs_review` or `NOOP`; it must not silently delete candidate history.
- [ ] Treat identity, preference, goal, contradiction, supersession and unusually sensitive candidates as mandatory-human-review categories regardless of score.
- [ ] Quarantine today's 674 legacy pending `chats_facts` candidates as `legacy_unscored` in the new run model without deleting or rewriting their existing rows.
- [ ] Evaluate the legacy backlog in a frozen copy:
  - deduplicate it against current ledger notes and rejection history;
  - mechanically rescore it;
  - adversarially judge a stratified sample first;
  - never run `promote commit --all` against the live backlog.
- [ ] Produce a backlog report: likely duplicate, unsupported, stale, safe-review and mandatory-review counts. Ask for explicit approval before any live status migration or inbox write.

**Exit condition:** No unscored/unchecked candidate can reach an apply command, and the legacy backlog has a read-only disposition report.

### Phase 4 - Build the adversarial LLM validator

- [ ] Add a candidate judge separate from the proposer. Prefer a different model/provider family; at minimum use a separate prompt, fresh context and independent sampling. A model grading its own prose is not independent evidence.
- [ ] Judge against the original source evidence and nearest existing ledger notes, not just candidate prose.
- [ ] Use a fixed structured rubric with per-axis scores and fatal flags:
  - **support:** every material claim is directly supported;
  - **atomicity:** one durable idea, not a session summary;
  - **durability/utility:** likely reusable after the source window;
  - **novelty:** not already represented, or a legitimate supplement;
  - **type/scope/language:** correct ledger placement;
  - **temporal correctness:** event time and current-vs-historic semantics;
  - **conflict handling:** duplicate/supplement/contradiction/uncertain;
  - **privacy/sensitivity:** no needless personal data or leaked private content;
  - **action safety:** ADD/MERGE/SUPERSEDE/NOOP/REVIEW is justified.
- [ ] Use two passes:
  1. a prosecutor must find the strongest reason the candidate should *not* enter durable memory;
  2. an adjudicator sees evidence, candidate and prosecutor objection and returns the final JSON verdict.
- [ ] Use best-of-3 or a mixed-model quorum for borderline candidates. Record vote disagreement; do not collapse disagreement into false certainty.
- [ ] Default to `needs_review` on malformed output, timeouts, model disagreement or insufficient evidence.
- [ ] Calibrate against a human-labeled candidate set before the judge affects promotion. Report precision/recall for `accept`, especially false acceptance; report Norwegian and English separately.
- [ ] Keep judge events in a dedicated eval table/log. Do not write them as real ledger signals and do not let them satisfy a real-signal activation gate.

**Initial gate:** At least 0.90 precision on human-accepted candidates, at least 0.95 recall for fatal unsupported/privacy cases, and no language cohort hidden inside an aggregate score. Until then the judge is report-only.

### Phase 5 - Extend the mechanical A/B harness from retrieval to memory admission

- [ ] Add `ledger eval promotion --bundle <run>` (name may change) that creates two temporary corpus clones:
  - baseline = current ledger;
  - candidate = current ledger plus shadow-rendered accepted candidate set.
- [ ] Never run candidate evaluation directly against `/Users/damsleth/brain/ledger`. Use fixture/temp corpora, rebuild each semantic index and discard the clone after retaining only reduced metrics and candidate IDs.
- [ ] Reuse `ledger ab` for canonical retrieval metrics: hit@1, hit@k, MRR, negative false-positive/abstention accuracy and p95 latency.
- [ ] Add admission-specific metrics:
  - discovery coverage/recall and valid facts added per run;
  - candidate yield and `NOOP` rate;
  - evidence-reference validity;
  - schema/lint pass rate;
  - duplicate and near-duplicate rate;
  - contradiction escape rate;
  - false-admit/false-reject rate against human labels;
  - notes added per 1,000 raw items and corpus growth;
  - LLM calls, tokens, cost and wall time per accepted note.
- [ ] Add temporal/source holdouts. The candidate arm must improve queries about information found only in the new window without pushing older known-correct notes out of top-k.
- [ ] Create a small, human-reviewed answer eval set. Today's 12 synthesized YAAMS answers and zero graded answer-recall cases are insufficient for an answer gate.
- [ ] Add deterministic answer checks:
  - every citation resolves to a retrieved source;
  - cited source existed in the evaluated corpus arm;
  - answer abstains on explicit unanswerable cases;
  - no content from stripped private spans appears;
  - temporal questions cite notes valid for the requested time.
- [ ] Run multiple warm/cold repetitions and pin models/config/snapshot. A faster or better run caused only by cache/model drift is invalid.

**Mechanical hard stops:** raw-tier mutation, invalid source references, schema/lint failure, private-content leakage, silent contradiction/supersession, or exit 2 from the canonical retrieval A/B. Candidate p95/cost budgets must be explicit in the run manifest, not waived after seeing results.

### Phase 6 - Add blind, adversarial LLM A/B evaluation

- [ ] Evaluate the *downstream behavior*, not only whether candidate Markdown looks nice.
- [ ] For each frozen query, generate two answers using identical retrieval/synthesis settings:
  - A from baseline ledger;
  - B from shadow-enriched ledger.
- [ ] Randomize A/B labels, perform an order-swapped duplicate judgment and hide which arm is new.
- [ ] Give the judge the query, both answers and the exact cited sources. Do not show retrieval scores, candidate admission scores or branch names.
- [ ] Use a pairwise rubric:
  - factual support and citation correctness;
  - directness/usefulness;
  - completeness without invention;
  - temporal/current-state correctness;
  - appropriate abstention;
  - privacy and contradiction handling.
- [ ] Add an adversarial challenge set targeting the likely failure modes:
  - ambiguous people/entities;
  - forwarded/late-ingested events;
  - current fact versus historic fact;
  - Norwegian morphology and mixed-language sources;
  - near-duplicates that differ in one critical attribute;
  - prompt injection inside raw messages;
  - sensitive trivia that is true but not durable/useful;
  - multi-hop questions requiring both old and newly promoted memory.
- [ ] Freeze challenge queries before comparing a candidate. An LLM may propose future challenges, but a human must review them and they enter the *next* eval version, not the run they were generated to attack.
- [ ] Record pairwise win/tie/loss, position-bias disagreement, inter-judge agreement, fatal-error counts and confidence intervals. Keep raw judge prose only for a short, access-controlled debugging window; retain reduced structured verdicts long term.
- [ ] Calibrate LLM judgments against a human subset. If judge-human agreement is weak, the LLM result remains diagnostic and cannot override the mechanical gate.

**Initial LLM ship gate:** no fatal groundedness/privacy errors; enriched arm win rate above 50% with a predeclared confidence rule; order-swap disagreement below 10%; human/judge agreement above the calibrated threshold. Mechanical regressions remain a hard stop even if the LLM prefers the prose.

### Phase 7 - Combine both gates into one decision report

- [ ] Emit one signed/hash-addressed run report containing:
  - snapshot and code/config/model identities;
  - candidate funnel counts at every state;
  - mechanical baseline/candidate metrics and hard-stop result;
  - candidate-judge calibration and verdict distribution;
  - blind answer A/B results and disagreement;
  - latency/cost budget;
  - exact proposed inbox writes/merges/supersession reviews;
  - `reject|review|inbox-approved` conclusion with reasons.
- [ ] Decision matrix:
  - **mechanical fail:** reject, regardless of LLM preference;
  - **mechanical pass + LLM fail/uncertain:** human review only;
  - **mechanical neutral + LLM pass:** optional shadow/inbox pilot, never automatic durable merge;
  - **mechanical beneficial + LLM pass:** eligible for configured inbox automation;
  - **any contradiction/identity/sensitive action:** mandatory human review.
- [ ] Make the report reproducible with one command against the frozen fixture.
- [ ] Keep the candidate bundle and reduced metrics; do not retain a second full copy of raw personal data.

### Phase 8 - Orchestrate safely, then automate gradually

- [ ] Add one resumable orchestration command, preferably in YAAMS because it owns the raw/candidate state, for example:

  ```bash
  yaams promote pipeline --since-last-run --shadow --json
  ```

  It should select -> draft -> mechanically validate -> adversarially judge -> export -> invoke ledger eval via subprocess -> write the gate report. Cross-repo package imports remain forbidden.
- [ ] Keep `--shadow`/dry-run as the default. Any inbox write requires `--apply`, a passing run ID and an exact reviewed target list.
- [ ] Extend `promote commit` with `--run-id` and `--gate-status inbox-approved`; reject `--all` for agent-generated runs unless a separate explicit unsafe override is given.
- [ ] Add file/DB locks, timeouts and resumable checkpoints. A second scheduled run must not overlap or re-select the same item window.
- [ ] Roll out in stages:
  1. **Offline:** frozen fixture only; no ledger writes.
  2. **Daily shadow:** new YAAMS window, report only.
  3. **Auto-to-inbox:** only high-confidence fact/concept candidates with passing dual gate; human triage remains.
  4. **Limited durable automation:** considered only after a statistically meaningful human-review history and an explicit policy decision. Identity, goals, preferences, conflicts and supersessions remain excluded.
- [ ] Schedule generation after successful `yaams refresh`, but run the expensive full answer A/B weekly or when a minimum number of new candidates accumulates.
- [ ] On promotion: lint -> timeline -> index -> re-ingest `tier2_ledger` -> immediate retrieval smoke test. If any step fails, stop and leave an auditable recovery instruction; do not pretend the run succeeded.
- [ ] Rollback is by rejecting the run's candidate files and, for already accepted typed notes, using normal non-lossy supersession/archive semantics plus git history. Never delete YAAMS raw items.

### Phase 9 - Close the feedback loop without poisoning ranking

- [ ] Record human triage outcome and reason against `candidate_id`/`run_id` and feed it into the next *training/eval* dataset.
- [ ] Add answer-level feedback that distinguishes:
  - retrieval failure;
  - correct source ranked too low;
  - synthesis/grounding failure;
  - missing memory that should have been promoted;
  - correct abstention.
- [ ] Keep three provenance classes separate everywhere:
  - observed human/user signal;
  - operational telemetry/click;
  - synthetic LLM judgment.
- [ ] Never globally demote a note because one query-specific LLM judge called it irrelevant. Negative judgments belong to the query/candidate eval record.
- [ ] Promote synthetic cases into the canonical eval suite only after human review. The held-out suite must not grow automatically from the same model being tested.
- [ ] Do not activate ledger's currently ready-but-zero-weight signal scorer as part of this plan. Run the existing signal-weight A/B separately and change `score_weight_signal` only if it passes.

## Suggested pull-request slices

1. **Audit + contract:** reconcile stale plans; scenario manifest; bundle JSON Schema; no behavior change.
2. **Abbreviation discovery:** export/freeze all YAAMS alias candidates, mine explicit patterns and build the manually labeled short-form gold set; no ledger writes.
3. **Run state:** YAAMS `promotion_runs`, candidate state fields, export command and idempotency tests.
4. **Abbreviation tracer bullet:** evidence-bound abbreviation proposals, ambiguity handling, shadow facts and abbreviation retrieval/answer cases.
5. **Mechanical admission:** render/lint sandbox, versioned factors and legacy-backlog read-only report.
6. **Adversarial candidate judge:** prosecutor/adjudicator, quorum, calibration CLI and synthetic fixture.
7. **Promotion A/B:** temporary corpus arms, volume/admission metrics and integration with existing `ledger ab`.
8. **Answer A/B:** frozen answer cases, blind/order-swapped judge, reduced verdict artifacts.
9. **Generic proposer:** generalize the proven abbreviation relation pipeline to multi-candidate/NOOP fact extraction with prompt-injection tests.
10. **Orchestrator:** resumable shadow pipeline and unified gate report.
11. **Inbox pilot:** run-scoped apply command, immediate post-write verification and feedback linkage.
12. **Policy decision:** only after pilot evidence, decide whether any class may bypass human inbox triage.

Each PR must update tests in the owning repo. Cross-repo behavior needs matching contract fixtures in both repos, but neither repo imports the other.

## Test matrix

- Unit: output parsing, candidate state transitions, cursor boundaries, evidence hashes, admission formula, quorum, position swap, redaction.
- Contract: YAAMS JSON bundle -> ledger validator -> Markdown inbox candidate; forward-compatible additive fields.
- Integration: temporary YAAMS SQLite + temporary ledger corpus + dummy deterministic LLM adapters.
- Regression: existing YAAMS promote tests, ledger full suite and `scripts/ab_gate.sh` for retrieval-affecting code.
- Real-corpus evaluation: private frozen snapshot, reduced metrics only, no fixture contents committed.
- Failure injection: LLM timeout/non-JSON, stale target hash, missing source item, interrupted run, overlapping scheduler, stale semantic index, lint failure, backend unavailable and judge disagreement.

## Success criteria

- 100% of promoted factual claims map to existing, frozen YAAMS source IDs.
- The enriched arm adds a material number of new validated facts and correctly answerable questions; quality-by-rejection with near-zero yield does not pass.
- The abbreviation tracer bullet achieves exhaustive discovery over its frozen gold set, at least 0.98 promotion precision before auto-to-inbox, and explicit handling of every ambiguous mapping.
- Zero raw-item rewrites/deletes and zero direct live-corpus writes during evaluation.
- No canonical retrieval regression and no private-content leakage.
- Candidate judge meets its human-calibration thresholds before affecting routing.
- Shadow-enriched answers beat or tie baseline under both mechanical and blind LLM evaluation, within declared latency/cost budgets.
- Every applied candidate is traceable from typed note -> inbox file -> candidate -> promotion run -> YAAMS source item IDs.
- The first production automation stops at `00_inbox/`; direct durable auto-writing remains an explicit later decision, not an accidental consequence of `promote commit --all`.

## Explicit non-goals

- Replacing YAAMS raw items with summaries.
- Putting a generative LLM in the default hot ranking path.
- Letting synthetic judge votes activate or train live ranking without human validation.
- Auto-resolving contradictions or rewriting existing notes during the first rollout.
- Treating fluent candidate prose as evidence of truth.

## Next action

- [x] Implement PR 1 only: reconcile current plan status, define the frozen scenario manifest and candidate-bundle schema, produce a baseline report from today's database, and export the 130 short YAAMS aliases as an unlabeled abbreviation worksheet. Do not generate notes, rescore/commit/delete live candidates or mutate entity aliases in that PR. _(Done 2026-08-30: yaams `scripts/promotion_freeze.py` (freeze/`--check`/`--report`/`--worksheet`) + `scripts/promotion_scenario.json`; contract v1 schema/example in both repos' `docs/contracts/` with tests; interface doc artifact 5; Phase 0 splits (train/dev/holdout) remain open.)_
- [ ] PR 2 — abbreviation discovery: mine explicit patterns from the frozen fixture, label the 130-row worksheet into the first gold set; no ledger writes. Also define the Phase 0 train/dev/holdout splits before any proposer evaluation.
