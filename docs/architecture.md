# Architecture

What happens where, and when. The README's diagram is the ninety-second version;
this is the one to read before changing anything.

References here are to modules and functions (`fetch.fetch_sources`) rather than
line numbers, because line numbers rot and this document is expected to outlive
several refactors. Every module named below lives in `src/cra_assistant/`, and a
test asserts that each one still exists.

## The organising idea: almost nothing persists

Three kinds of state, and knowing which is which explains most of the design.

**Committed to git** — the things a human decides:

| Artefact | Decides |
| --- | --- |
| `registry/sources.toml` | what to fetch, and at which trust tier |
| `registry/pins.toml` | which checksums a human has approved |
| `eval/golden.toml` | what "correct" means |
| `docs/eval/*.md` | what was measured, and when. Append-only |

**Derived and gitignored** — everything under `data/`:

| Path | Contents |
| --- | --- |
| `data/raw/<source>/<sha256[:12]>.<ext>` | fetched bytes, content-addressed, never overwritten |
| `data/manifest.jsonl` | append-only record of every fetch |
| `data/calls.jsonl` | append-only record of every model call |
| `data/segments/<source>.jsonl` | written by `parse`; see [Open questions](#open-questions) |

**Nothing else.** No database, no cached index, no intermediate representation
that anything reads. Every command reconstructs the corpus from bytes on disk
each time it runs, and segments do not exist between invocations. That is a
deliberate consequence of [ADR-0006](adr/0006-walking-skeleton.md): the index is
disposable, so nothing is allowed to depend on it surviving.

## Startup, identical for every command

`cli.main`:

1. `config.apply_dotenv()` runs **before** argument parsing, so every code path
   sees the same environment. An exported variable always wins over `.env`.
2. `cli.build_parser()` parses arguments and dispatches to `args.handler(args)`.

Six subcommands, one handler each. `cli` is the only module that imports
everything; every other module has a narrow dependency set, and there are no
cycles. `models` is the leaf that almost everything depends on.

## Flow A — acquisition

`fetch` is the only command that talks to the network.
`fetch.fetch_sources` processes sources sequentially:

```
for each source:
  ├─ sleep(delay)                     between sources, not before the first
  ├─ fetch.fetcher_for(source.parser) plain GET │ GitHub API (paginated) │ tree + raw files
  ├─ segment.segment_document(...)    yes: the document is parsed during fetch
  ├─ plausibility.check_document(...) is there anything here?
  │     └─ fails → collect error, CONTINUE — nothing stored, nothing recorded
  ├─ fetch.store_bytes(...)           content-addressed; identical bytes are a no-op
  └─ manifest.append_observation(...) one JSON line
```

**The ordering is the design.** Plausibility runs before anything is stored or
recorded, so a document that cannot be segmented into something usable never
enters the corpus — it is treated exactly like a 404. This only works because it
sits *before* `store_bytes`; running it later would let an empty document be
stored, checksummed and recorded as a clean observation with only a subsequent
command dissenting. That was very nearly the situation
[ADR-0009](adr/0009-untrusted-content-from-apis.md) was written to fix.

Failures are collected rather than raised, so one dead URL does not hide four
working ones. The CLI prints them all and exits non-zero.

Fetchers differ by parser but all return one `FetchedDocument`, so a source
assembled from forty paginated API responses is still one document with one
checksum, and the store, manifest and drift machinery need no special case.

## Flow B — everything else, rebuilt from disk

Five commands share one prelude, `cli.segments_for`:

```
registry.load_registry(path)       → validated Sources (pydantic, strict)
manifest.load_manifest()           → every observation ever recorded
  └─ manifest.latest_by_source()   → newest per source, by retrieved_at
read data/raw/<stored_path>        → the exact bytes that observation recorded
segment.segment_document(src, raw) → Segments
```

`segment._make_segment` is where **the trust tier is stamped onto every
segment**, along with its citation, source digest and content digest. Nothing
downstream looks a tier up; if a segment exists, it states its own provenance
([ADR-0001](adr/0001-two-tier-trust-model.md)).

Then the commands diverge:

| Command | After the prelude | Exit code |
| --- | --- | --- |
| `parse` | writes `data/segments/<id>.jsonl`, prints counts and content checksum | 0, or 1 if nothing is fetched |
| `validate` | `plausibility.check_document` + `validate.validate_segments` | **1 on any error**, 0 on warnings |
| `verify` | re-derives content checksums, compares against pins | **1 on trusted content drift** |
| `eval` | builds a BM25 index, scores the golden set, renders a report | always 0 — report only |
| `ask` | builds a BM25 index, retrieves, prompts, generates | 1 on provider failure, 2 without a key |

`verify` deliberately does **not** use the prelude.
`verify.observed_content_checksums` re-derives everything from stored bytes on
every run, because a cached intermediate could go stale against the segmenter,
and a stale content checksum is precisely the failure the gate exists to catch.

### Two checksums, two jobs

| Checksum | Over | On drift |
| --- | --- | --- |
| raw | the bytes as served | reported at every tier, **never** blocking |
| content | the extracted segment text | **blocks** for trusted sources |

An EUR-Lex response embeds a per-request analytics id, so two fetches seconds
apart differ in raw bytes while producing an identical content checksum. That is
why one of these can be a gate and the other cannot
([ADR-0003](adr/0003-drift-policy.md), [ADR-0004](adr/0004-structure-based-segmentation.md)).

## `ask`, step by step

`generate.ask`:

```
retriever.retrieve(question, k)         BM25, tier-blind by design (ADR-0008)
  └─ empty? → abstain, NO model call, still write a telemetry record
prompt.assemble_prompt(...)             the trust boundary becomes text; records
                                        exactly what of each segment was delivered
budget.spend()                          hard ceiling, before any spend
with telemetry.timed():
    client.complete(...)                the only outbound call
generate._parse_reply(...)              malformed JSON → abstention, not a crash
generate.enforce_citations(...)         the rule, not the request
telemetry.log_call(...)                 every call, success or failure
```

Two things happen in code rather than in the prompt, and that is the point:

- `prompt.render_segment` wraps untrusted segments in delimiters they cannot
  close — `prompt.neutralise_delimiters` strips the closing tag from their text
  first — and labels them as data. Outside the wrapper an untrusted item carries
  only its pattern-validated id, tier and language; its human-readable citation,
  built from attacker-chosen headings and file names, is rendered inside
  ([ADR-0017](adr/0017-metadata-is-untrusted-content.md)). **This is the only
  place the trust boundary is enforced.**
- `generate.enforce_citations` drops any cited id that was not delivered and any
  citation whose span is not verbatim in the **delivered** text — the segment as
  the prompt clipped it, not as it is stored — and converts an answer left with
  no citation into an abstention. The prompt asks for citations; this decides
  whether the answer has them.

## Where each guarantee lives

Each of these has exactly one enforcement point. If you are changing one of
these files, you are changing a guarantee.

| Guarantee | Enforced in |
| --- | --- |
| A source's tier is unambiguous | `registry/sources.toml`, validated by `registry.SourceRegistry` |
| A segment knows its own tier | `segment._make_segment`, at ingest |
| Untrusted text cannot act as instruction | `prompt.render_segment` — **only here** |
| An answer cannot cite what the model was not shown | `generate.enforce_citations`, against `prompt.DeliveredSegment` |
| Empty documents cannot enter the corpus | `plausibility.check_document`, before `store_bytes` |
| Trusted text cannot change unnoticed | `verify.verify` against committed pins |
| No key reaches a log | `telemetry.CallRecord`'s fixed schema — no free-form field exists |
| Documented commands exist | `tests/test_docs.py` |

## Module dependencies

No cycles. `models` is the leaf; `cli` is the only module that knows everything.

```
models ← manifest, prompt, retrieve, registry, parse⁺, segment, verify, validate,
         plausibility, evaluate, generate
parse  ← segment, plausibility
segment ← fetch, validate, verify
problems ← plausibility, validate          shared Problem/Severity, so structural
                                           and plausibility checks report alike
paths  ← config, registry, golden, verify
cli    ← everything
```

`problems` exists solely so `validate` and `plausibility` can report in the same
shape without importing each other.

## Open questions

Recorded rather than hidden. None is speculative; each is visible in the code.

- **`parse` writes into a void.** `data/segments/*.jsonl` has no consumer — the
  only reference to the directory is the write. `ask`, `eval` and `verify` all
  re-derive segments from raw bytes. It is an inspection artefact that currently
  looks like a pipeline stage. It should either feed something or be renamed to
  say what it is.
- **Segmentation is the dominant startup cost, paid per invocation.** Every
  `ask` re-parses ~1,800 segments across six documents before answering.
  Imperceptible at this size, and explicitly not expected to survive a corpus
  fifty times larger.
- **The trust boundary is one function deep.** Ranking is tier-blind on purpose
  ([ADR-0008](adr/0008-tier-blind-ranking.md)), so if `prompt.render_segment` is
  wrong there is no second line of defence. The concentration is deliberate —
  one place to test and audit — but nothing has yet attacked it, because the
  poison fixtures do not exist.
