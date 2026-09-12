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
- Dev tooling lives in the PEP 735 `dev` dependency group, so plain `uv sync`
  is enough to get a working environment.
- `uv.lock` is committed. CI runs `uv sync --locked`, so a lockfile that drifts
  from `pyproject.toml` fails the build.
- Runtime `dependencies` stay empty until a step genuinely needs one.
- `data/` is gitignored: source registries are committed, downloaded bytes are
  not.
- Secrets come from `.env` (gitignored). `.env.example` documents the shape.
  Key material is never logged — log that a key was used, never the key.

## Commands

```sh
uv sync                     # create the environment
uv run pytest               # tests
uv run ruff check .         # lint
uv run ruff format .        # format (CI runs --check)
```

## Roadmap

1. Bootstrapping — done
2. Corpus: source registry with trust tiers, download with checksums,
   structure-based segmentation into articles/recitals/annexes, validation
3. Poison fixtures: authored attack documents in the untrusted tier
4. Index: Postgres + pgvector, hybrid retrieval
5. Generation via OpenAI API with mandatory citations and abstention
6. Evaluation as a CI gate, retrieval and generation measured separately
7. Telemetry: OpenTelemetry, token and cost attribution
8. MCP server as the primary interface, then deployment
