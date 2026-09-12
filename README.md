# cra-assistant

A retrieval service over the **EU Cyber Resilience Act** (Regulation (EU)
2024/2847, CELEX `32024R2847`) that answers questions with verifiable citations
to specific articles and recitals. Compliance answers are only useful if you can
check them, so every claim the system makes has to point at the text it came
from. The corpus is deliberately split into a curated tier and an open tier,
which makes indirect prompt injection an architectural problem to be designed
against rather than a demo to be staged: untrusted material has to be usable as
evidence while never being able to act as instruction.

## Trust tiers

| Tier | Contents | Who can write it | Treatment in prompts |
| --- | --- | --- | --- |
| `trusted` | The regulation text and official guidance | Curated; authorised parties only | May carry instruction authority |
| `untrusted` | Vendor blogs, forum posts, GitHub issues interpreting the CRA | Anyone | Encapsulated. Never treated as instructions, never permitted to trigger tool calls |

**`trusted` and `untrusted` describe write access, not quality.** An untrusted
source may be more accurate and better argued than the regulation's own wording
is clear; it is untrusted because anyone can edit it, so its text must never be
able to steer the system.

The boundary is one-way: trusted content may direct the system's behaviour,
untrusted content may only be quoted, cited and reasoned about. A source's tier
is declared in [`registry/sources.toml`](registry/sources.toml) and is stamped
onto every document and segment at ingest, so nothing downstream has to look it
up — see [ADR-0001](docs/adr/0001-two-tier-trust-model.md).

## Roadmap

- [x] 1. Bootstrapping
- [ ] 2. Corpus: source registry with trust tiers, download with checksums, structure-based segmentation into articles/recitals/annexes, validation
- [ ] 3. Poison fixtures: authored attack documents in the untrusted tier
- [ ] 4. Index: Postgres + pgvector, hybrid retrieval
- [ ] 5. Generation via OpenAI API with mandatory citations and abstention
- [ ] 6. Evaluation as a CI gate, retrieval and generation measured separately
- [ ] 7. Telemetry: OpenTelemetry, token and cost attribution
- [ ] 8. MCP server as the primary interface, then deployment

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv installs the pinned Python 3.12
itself, so nothing else needs to be on your machine.

```sh
uv sync
uv run pytest
```

Fetch the declared sources and report drift against the committed pins:

```sh
uv run cra-assistant fetch
uv run cra-assistant verify
```

Ask a question (needs `OPENAI_API_KEY`):

```sh
uv run cra-assistant ask "Wer gilt als Hersteller im Sinne der Verordnung?"
```

Every answer cites segment ids, and an answer that cites nothing retrieved is
converted into an abstention rather than shown. To see how the prompt is
assembled — including how untrusted content is delimited — without calling the
model or needing a key:

```sh
uv run cra-assistant ask --show-prompt "your question"
```

Score retrieval against the golden set (offline, no API key):

```sh
uv run cra-assistant eval --include-unverified
```

The golden set in [`eval/golden.toml`](eval/golden.toml) is committed data,
reviewed like any other change. Every item is currently `verified = false` —
the labels were drafted and not checked by hand — so `eval` refuses to score
without `--include-unverified` and stamps the report as provisional. Baselines
live in [`docs/eval/`](docs/eval/) and are append-only.

Segment the fetched documents and check them structurally:

```sh
uv run cra-assistant parse
uv run cra-assistant validate
```

`fetch` never fails because content changed — it stores every version under its
own digest and appends to a manifest. `verify` is a separate command comparing two
checksums per source:

| Checksum | Covers | On drift |
| --- | --- | --- |
| raw | the bytes as served | reported at every tier, **never** blocking |
| content | the extracted segment text | **blocks** for trusted sources |

The split exists because an EUR-Lex response embeds a per-request analytics id,
so two fetches seconds apart differ in raw bytes while producing an identical
content checksum over all 209 segments. Drift detection runs in CI on a weekly
schedule rather than on every push, so an upstream edit can never block an
unrelated pull request. See [ADR-0003](docs/adr/0003-drift-policy.md) and
[ADR-0004](docs/adr/0004-structure-based-segmentation.md).

Lint and format the way CI does:

```sh
uv run ruff check .
uv run ruff format --check .
```

Configuration lives in `.env`; copy `.env.example` and fill it in. No real key
is ever committed, and no key material is ever logged.

## Known limitations

Honest gaps, not a roadmap. Each is a thing the system currently gets wrong or
does not do, stated so that nobody has to discover it by being misled.

- **The corpus is the Official Journal text of 20.11.2024.** Corrigenda
  `32024R2847R(01)` and `32024R2847R(04)` amend the article text and are **not
  incorporated** — they are not registered, not fetched and not applied. An
  answer citing an article touched by a corrigendum quotes superseded wording,
  and the citation looks correct while doing so. The model for handling them is
  decided in [ADR-0005](docs/adr/0005-corrigenda-as-separate-sources.md); the
  work is not done.
- **Retrieval is a throwaway in-memory BM25 index**, rebuilt on every
  invocation. No embeddings, no database, no semantic matching: a question
  phrased without the regulation's own vocabulary will retrieve badly. See
  [ADR-0006](docs/adr/0006-walking-skeleton.md).
- **Long segments are truncated, not sub-split.** Annex VIII is 22,000
  characters and reaches the model clipped, so an answer drawn from its later
  parts is not possible today.
- **Prompt-level injection defence is a first pass.** Untrusted content is
  delimited and labelled, and the system prompt forbids treating it as
  instruction. That is not the same as being tested against real attacks — the
  poison fixtures that would test it do not exist yet.
- **Retrieval is measured, and it is not good.** On a drafted golden set,
  MRR@10 is 0.455 for questions phrased in the regulation's own words and
  **0.185** for questions phrased the way a practitioner asks. Article 13 ranks
  47th for a question that is verbatim its own title, because BM25 penalises it
  for being long. See [the baseline](docs/eval/baseline-2026-09-12.md).
- **The golden set is drafted, not verified.** Nobody has checked the gold
  labels by hand, so the numbers above describe the shape of the problem rather
  than being a baseline anybody should defend.
- **Generation is not evaluated at all.** Retrieval and generation are measured
  separately; only retrieval has been measured.
- **The untrusted tier contains almost no usable content.** The registered
  community FAQ is a link index, and both GitHub sources render in the browser,
  so a static fetch captured navigation chrome rather than discussion. Every
  check the project has asks whether bytes are *stable*, none asks whether they
  are *useful*.

## Project documentation

- `docs/adr/` — architecture decision records, including the options rejected
  and what each choice costs.
- `docs/journal.md` — dated build log: what was built, what was surprising,
  what broke.

## Status

Early. Sources are declared, fetched, checksummed and segmented into 130
recitals, 71 articles and 8 annexes per language. Nothing is indexed or
retrieved yet, and no prompt is assembled anywhere, so the trust boundary is
still a modelled property rather than an enforced one.

**Known correctness gap.** The registry declares the Official Journal text of
20.11.2024. Corrigenda 32024R2847R(01) and R(04) amend the article text and are
**not yet applied**, so an answer citing an affected article would quote
superseded wording. The model for handling them is decided in
[ADR-0005](docs/adr/0005-corrigenda-as-separate-sources.md); the patching is
not built.

The trust boundary is currently a modelled property and a documented rule. No
prompt is assembled anywhere in this repository, so nothing yet *enforces* that
untrusted text cannot act as an instruction — that enforcement is the point of
the project and it is not built.

## License

MIT. See [LICENSE](LICENSE).
