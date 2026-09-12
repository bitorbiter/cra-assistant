# cra-assistant

## What this is

A retrieval service over the EU Cyber Resilience Act (Regulation (EU) 2024/2847,
CELEX `32024R2847`) answering questions with verifiable citations to specific
articles and recitals. The corpus is two-tiered, and that boundary is the point:

- **trusted** — the regulation text and official guidance. Curated, writable
  only by authorised parties. May carry instruction authority in prompts.
- **untrusted** — GitHub issues and comments, community FAQ answers, a
  machine-converted copy of an official FAQ. Writable by anyone. Encapsulated,
  never instructions, never permitted to trigger tool calls.

The split makes indirect prompt injection a real architectural problem rather
than a staged demo; everything else serves it. Public portfolio project: code
quality, documented reasoning and honest limitations beat feature count.

## Working agreement

- One step at a time. At the end of each step: stop, summarise, propose the next
  step, do not start it. Never implement anything from a later roadmap step
  because it seems convenient now, and no placeholder modules for future steps.
- Ask before adding a dependency, and say why the stdlib is not enough.
- Every non-obvious decision gets an ADR in `docs/adr/` (MADR-style: context,
  decision, rationale, consequences, rejected alternatives). No ADR for trivia.
- After each session append a dated entry to `docs/journal.md`: what was built,
  what surprised us, what broke — record the dead ends, a blog post comes from it.
- If a decision of the user's looks wrong, say so before implementing it.
- Assert that every scripted string replacement matched. Four silent no-matches
  have shipped wrong docs and one crash.

## Stack (decided, not open for re-litigation)

Python 3.12 · uv · `src/` layout · pydantic v2 · pytest · ruff · GitHub Actions ·
OpenAI API. Later: Postgres + pgvector, OpenTelemetry, MCP.

## Conventions

- Version lives only in `src/cra_assistant/__init__.py`; hatchling reads it. Dev
  tooling is a PEP 735 `dev` group. `uv.lock` is committed, CI runs
  `uv sync --locked`. New runtime dependencies need a journal note saying why the
  stdlib was not enough. `data/` is gitignored; `registry/` is committed.
- `registry/sources.toml` and `eval/golden.toml` are committed data validated
  through pydantic (ADR-0002); never move either into Python. A `Source` is
  purely declarative — facts about a fetch belong in the manifest.
- Trust tier is a property of the source, stamped onto every segment at ingest.
  Nothing may look it up at query time (ADR-0001).
- Fetch records, verify judges (ADR-0003). Fetching fails on an implausible
  document, never a changed one. Raw-byte checksums are report-only; content
  checksums block for trusted sources. Anything needing the corpus lives in the
  weekly `corpus` CI job — the push/PR job never touches EUR-Lex or the API.
- Untrusted content comes from APIs and raw files, NEVER rendered pages, and
  every document passes an ingest plausibility check (ADR-0009) — distinct from
  stability: checksums ask "did it change", plausibility "is anything here".
  Failure is an error at both tiers.
- Segment on the document's own structure via text markers, never EUR-Lex HTML
  classes (ADR-0004); only block-level elements break a line, or footnote markers
  become recital numbers. A segment id is a permanent name, never encodes a
  version, and is non-positional wherever the source offers a stable identifier
  (ADR-0005, ADR-0009). Corrigenda are unhandled: the corpus is knowingly stale.
- Validation: a gap means a marker stopped matching — fix the marker, never the
  check. Genuinely short articles are a named allowlist.
- Measure before tuning (ADR-0007). Gold labels stay `verified = false` until
  checked; baselines in `docs/eval/` are append-only. Never weaken a gold label
  to improve a metric.
- Ranking is tier-blind (ADR-0008). Never add tier weighting to `retrieve.py`: a
  ranking penalty would make the prompt-level injection defence untestable.
- Retrieval is throwaway in-memory BM25 (ADR-0006): depend on the `Retriever`
  protocol, never `Bm25Retriever`. Citations are enforced in code, not requested
  in the prompt — an answer citing nothing retrieved becomes an abstention.
  Untrusted segments render inside delimiters they cannot close.
- Every model call is logged to `data/calls.jsonl`. Secrets come from `.env`
  (read at startup; an exported variable wins) and never appear in a log, a repr
  or an exception — exception class names and the provider's structured error
  code only, never its message.
- The model is pinned to a dated snapshot, never a floating alias, and its id
  goes in every telemetry record and report header (ADR-0010).

## Commands

```sh
uv sync && uv run pytest         # environment, then tests (live deselected)
uv run ruff check . && uv run ruff format .
uv run cra-assistant fetch       # download; rejects implausible documents
uv run cra-assistant parse       # segment them
uv run cra-assistant validate    # structure + plausibility; non-zero on errors
uv run cra-assistant verify      # drift; blocks on trusted content drift
uv run cra-assistant eval --include-unverified   # score retrieval, offline
uv run cra-assistant ask "..."   # cited answer; --show-prompt needs no key
```

## Roadmap and standing gaps

Status and remaining steps are in README.md, the flow through the application in
docs/architecture.md; keep them there, not duplicated here. Three gaps that
shape day-to-day decisions:

- The golden set is drafted; all 41 items are `verified = false`.
- Corrigenda R(01)/R(04) are NOT incorporated — the corpus is the OJ text of
  20.11.2024, so citations to amended articles quote superseded wording.
- Generation has two output shapes and needs a third: "practitioners assume X,
  but it is not confirmed" (ADR-0006 consequences).
