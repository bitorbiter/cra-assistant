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

`fetch` never fails because content changed — it stores every version under its
own digest and appends to a manifest. `verify` is a separate, **report-only**
command: it detects drift and escalates it only for trusted sources, but always
exits 0 and does not run in CI. Raw-byte checksums over an EUR-Lex page drift on
nearly every fetch because of an analytics tag, not the legal text, so the gate
arms once checksums cover parser-extracted text. See
[ADR-0003](docs/adr/0003-drift-policy.md).

Lint and format the way CI does:

```sh
uv run ruff check .
uv run ruff format --check .
```

Configuration lives in `.env`; copy `.env.example` and fill it in. No real key
is ever committed, and no key material is ever logged.

## Project documentation

- `docs/adr/` — architecture decision records, including the options rejected
  and what each choice costs.
- `docs/journal.md` — dated build log: what was built, what was surprising,
  what broke.

## Status

Early. Step 2 of 8 complete: sources are declared, fetched and checksummed.
Nothing is parsed, segmented, indexed or retrieved yet.

The registry declares the Official Journal text of 20.11.2024. Corrigenda
32024R2847R(01) and R(04) amend the article text and are **not yet handled**.

The trust boundary is currently a modelled property and a documented rule. No
prompt is assembled anywhere in this repository, so nothing yet *enforces* that
untrusted text cannot act as an instruction — that enforcement is the point of
the project and it is not built.

## License

MIT. See [LICENSE](LICENSE).
