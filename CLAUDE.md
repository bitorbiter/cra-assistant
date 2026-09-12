# cra-assistant

## What this is

A retrieval service over the EU Cyber Resilience Act (Regulation (EU) 2024/2847,
CELEX `32024R2847`) that answers questions with verifiable citations to specific
articles and recitals.

The corpus is two-tiered, and that boundary is the point of the project:

- **trusted** — the regulation text and official guidance. Curated, only
  writable by authorised parties. May carry instruction authority in prompts.
- **untrusted** — vendor blogs, forum posts, GitHub issues interpreting the CRA.
  Writable by anyone. Must be encapsulated, must never trigger tool calls, must
  never be treated as instructions.

The split makes indirect prompt injection a real architectural problem rather
than a staged demo. Everything else in the system serves it.

This is a public portfolio project. Code quality, documented reasoning and
honest limitations matter more than feature count.

## Working agreement

- One step at a time. At the end of each step: stop, summarise what was done,
  propose the next step. Do not start it.
- Never implement anything from a later roadmap step because it seems
  convenient now. No placeholder modules for future steps.
- Ask before adding a dependency, and say why the stdlib is not enough.
- Every non-obvious decision gets an ADR in `docs/adr/` (MADR-style: context,
  decision, rationale, consequences, rejected alternatives). No ADR for trivia.
- After each session, append a dated entry to `docs/journal.md`: what was
  built, what was surprising, what broke. A blog post will be written from
  this, so record the dead ends, not just the outcome.
- If a decision of the user's looks wrong, say so before implementing it.

## Stack (decided, not open for re-litigation)

Python 3.12 · uv for dependency management · `src/` layout · pydantic v2 for
models · pytest · ruff for lint and format · GitHub Actions for CI.

Later steps add: Postgres + pgvector, OpenAI API, OpenTelemetry, MCP.

## Conventions

- Version lives only in `src/cra_assistant/__init__.py`; hatchling reads it.
  Dev tooling is a PEP 735 `dev` group, so plain `uv sync` suffices. `uv.lock`
  is committed and CI runs `uv sync --locked`, so lockfile drift fails the build.
- Runtime `dependencies` are added by the step that first needs one, with a
  journal note on why the stdlib was not enough.
- `registry/sources.toml` is committed data, validated through pydantic
  (ADR-0002). Never move source declarations into Python. A `Source` is purely
  declarative: facts about a fetch belong in the manifest, and `extra="forbid"`
  enforces that.
- Trust tier is a property of the source, stamped onto every document and
  segment at ingest. Nothing may look it up at query time (ADR-0001).
- `data/` is gitignored: `registry/` is committed, fetched bytes are not.
- Fetch records, verify judges (ADR-0003). Fetching never fails on changed
  content. Two checksums per source: raw bytes report-only forever, content
  (over extracted text) blocks for trusted sources. The drift job runs on a
  weekly CI schedule, never on push — it needs the network.
- Segment on the document's own structure via text markers, never EUR-Lex HTML
  classes (ADR-0004). Only block-level elements break a line, or footnote
  markers become recital numbers.
- A segment id is a permanent name and never encodes a version. Corrigenda are
  separate sources, patched at composition, invisible in citations (ADR-0005) —
  not implemented, so the corpus is knowingly stale.
- Validation reports structural problems; a gap means a marker stopped
  matching. Fix the marker, never loosen the check. Genuinely short articles are
  a named allowlist, so anything else short is an error.
- Retrieval is throwaway in-memory BM25 (ADR-0006): depend on the `Retriever`
  protocol, never on `Bm25Retriever`. Citations are enforced in code, not
  requested in the prompt — an answer citing nothing retrieved becomes an
  abstention. Untrusted segments render inside delimiters they cannot close.
- Every model call is logged to `data/calls.jsonl`. Secrets come from `.env`
  (gitignored) and the environment only. Never put a key, a prompt or a
  provider message in a log, a repr or an exception — class names only.

## Commands

```sh
uv sync                     # create the environment
uv run pytest               # tests
uv run ruff check .         # lint
uv run ruff format .        # format (CI runs --check)

uv run cra-assistant fetch  # download declared sources
uv run cra-assistant verify # drift report; report-only, always exits 0
```

## Roadmap

Done: 1 bootstrapping, 2 corpus, plus a disposable walking skeleton (ADR-0006).
Remaining: 3 poison fixtures, 4 pgvector hybrid index, 5 generation proper,
6 evaluation as a CI gate, 7 OpenTelemetry, 8 MCP server and deployment.

Corrigenda R(01)/R(04) are NOT incorporated: the corpus is the OJ text of
20.11.2024. ADR-0005 decides the model; the work is not done.
