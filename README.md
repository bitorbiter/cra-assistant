# cra-assistant

A retrieval service over the **EU Cyber Resilience Act** (Regulation (EU)
2024/2847) that answers questions with citations to specific articles and
recitals. Compliance answers are only useful if you can check them, so every
claim points at the text it came from. The corpus is deliberately split into a
curated tier and an open tier, which makes indirect prompt injection an
architectural problem to design against rather than a demo to stage: material
anyone can edit has to be usable as evidence while never being able to act as
instruction.

It is a portfolio project, and it is honest about being unfinished. The most
useful things in it are two findings, both below.

## Trust tiers

| Tier | Contents | Who can write it | Treatment in prompts |
| --- | --- | --- | --- |
| `trusted` | The regulation text, EN and DE (418 segments) | Curated; authorised parties only | May carry instruction authority |
| `untrusted` | GitHub issues and comments, community FAQ answers, a machine-converted copy of an official FAQ (1,383 segments) | Anyone | Encapsulated. Quoted as evidence, never treated as instruction, never permitted to trigger tool calls |

`trusted` and `untrusted` describe **write access, not quality**. An untrusted
source is often more useful than the statute; it is untrusted because anyone can
edit it.

---

## Finding 1: five checks, none broken, all green on a navigation menu

For two development steps, three of this project's four untrusted sources
contained nothing. GitHub serves issue lists as an application shell that fills
itself in from the browser, so a static fetch returned a navigation menu and the
words *"Uh oh! There was an error while loading."*

Every check in the pipeline passed:

| Check | What it asked | Verdict on the chrome |
| --- | --- | --- |
| Raw checksum | Did the bytes change? | stable ✅ |
| Content checksum | Did the extracted text change? | stable ✅ |
| Pin file | Has a human approved this digest? | approved ✅ |
| Drift gate | Did trusted content move? | clean ✅ |
| Structural validation | Are the article numbers contiguous? | not a statute, no findings ✅ |

None of them was broken. Each answered its question correctly. **All five asked
whether the bytes were *stable*; none asked whether they were *useful*.** A
navigation menu is perfectly stable, hashes reproducibly forever, and has no
missing article numbers because it is not a regulation.

The fix was a new *class* of check — ingest plausibility, which asks whether a
document contains anything at all — not a patch to the existing five. Untrusted
content now comes from APIs and raw files, never rendered pages
([ADR-0009](docs/adr/0009-untrusted-content-from-apis.md)). The untrusted tier
went from 70 segments of chrome to 1,383 of real argument.

## Finding 2: the defence that was working was refusal, not framing

Seventeen authored fixtures plus two third-party corpora measure the one place
the trust boundary is enforced: `uv run cra-assistant attack --runs 3 --external`.

The first baseline looked reasonable — 20% of delimiter escapes got through.
Then a **positive control** was added: an untrusted document whose marker *is*
the correct answer to its question, so any faithful reply must contain it. It
failed, three rewrites running, always reasoning that the context held no
*authoritative* source. The model was not distinguishing data from instruction;
it was declining to use untrusted content at all. That 20% measured a system
refusing to use half its corpus, and is not a baseline for anything.

Fixing the refusals is a defect fix, not a mitigation. Against a system that
actually uses its corpus, an anti-injection framing was then added, ablated, and
**deleted for making attacks more likely** — 7 of 9 succeeded with it, 4 without
([ADR-0013](docs/adr/0013-ablate-the-framing.md)).

### Current measured state

Three runs per case, temperature 0, model `gpt-4o-mini-2024-07-18`. Reports:
[before](docs/eval/attacks-2026-09-13a-reaxed.md) ·
[after](docs/eval/attacks-2026-09-13c-claim-support-reaxed.md).

Citations must now carry a **verbatim span** from the segment they cite, checked
by substring match ([ADR-0015](docs/adr/0015-claim-support-enforcement.md)).
Attacks that reached the prompt and succeeded: **5 of 14 → 3 of 14, 36% → 21%.**

| | before | after |
| --- | ---: | ---: |
| delimiter-escape | 2/5 | **0/5** |
| authority-mimicry | 2/3 | 2/3 |
| instruction-injection | 1/6 | 1/6 |
| refusals on legitimate documents (NotInject, n=40) | 0% | **0%** |
| BIPIA external attacks | 13% | 6.7% |

Per-vector rates are direction only — three cases per vector cannot distinguish
them. BIPIA's 6.7% sits below the 7.5% rate at which the same detector fires on
*benign* content, so the external signal is no longer distinguishable from noise.

**What the two survivors show is more interesting than the drop.** Both are now
"supported" by a span that really exists:

> Article 71 … states that the regulation shall apply from 11 December 2027.
> However, a community note indicates that this date has been revised to
> 11 December 2029…

A correct quotation from the real article, with a fabricated date beside it.
That was **predicted in writing before the code was written**, and the
prediction is committed separately from the implementation so it could not be
adjusted afterwards.

The other survivor is worse: it cites *the attack document itself*. The span
check asks whether a claim is supported by a retrieved segment, and an attack
document is a retrieved segment — so stating a false claim plainly now supplies
its own verbatim span. One previously blocked attack started succeeding this
way. **The check verifies support, not authority.**

**Prompt injection is not solved here.** The rate went down, two of four
falsification conditions fired, and the defence acquired a new hole while
closing an older one.

## Finding 3: a retrieval failure does not look like a failure

Asked *"Wer gilt als Hersteller im Sinne der Verordnung?"* — who counts as a
manufacturer — at the default retrieval depth of 8:

> Als Hersteller im Sinne der Verordnung gilt ein Unternehmen, das Produkte mit
> digitalen Elementen vertreibt oder verkauft. Insbesondere wird ein Anbieter
> eines Online-Marktplatzes … als Hersteller betrachtet …
>
> **Citations:** `cra-de:recital:78`, `cra-de:recital:15`

Fluent, grounded, correctly cited, and **not the definition**. Recital 78 is
about online marketplaces and Recital 15 about monetisation. Article 3 defines
*Hersteller*, and it ranks 20th, so it was never retrieved.

Same question, same model, same prompt, depth 20:

> … eine natürliche oder juristische Person, die Produkte mit digitalen
> Elementen entwickelt oder herstellen lässt und sie unter ihrem Namen oder ihrer
> Marke vermarktet …
>
> **Citations:** `cra-de:article:3`, `cra-de:article:21`, `cra-de:article:22`

The only difference is whether the right segment was in the window. Generation
was working the whole time — it did not hallucinate, did not cite anything it was
not shown, and did not fall back on training knowledge of Article 3 that it
certainly has. It answered faithfully from what it was given.

**A retrieval failure does not look like a failure. It looks like a slightly off
answer with real citations attached.** That is why retrieval and generation are
measured separately ([ADR-0007](docs/adr/0007-measure-before-tuning.md)), and
why the fix is better retrieval rather than a larger window — the evaluation
prices both, and depth 20 costs 2.4× the tokens for +0.06 recall@10.

---

## Quickstart

Requires [uv](https://docs.astral.sh/uv/), which installs the pinned Python 3.12
itself. Nothing else needs to be on your machine.

```sh
uv sync
uv run pytest
```

### Without an API key

Most of the project runs with no key and no account. This is the honest way to
see what it does:

```sh
uv run cra-assistant fetch      # download the corpus (needs network, not a key)
uv run cra-assistant export-segments   # dump segments for inspection: 130 recitals,
                                #   71 articles, 8 annexes per language
uv run cra-assistant validate   # structural + plausibility checks
uv run cra-assistant verify     # drift report against the committed pins
uv run cra-assistant eval --include-unverified   # score retrieval, offline

# See exactly how the trust boundary is rendered into a prompt — no key needed:
uv run cra-assistant ask --show-prompt "Wer gilt als Hersteller im Sinne der Verordnung?"
```

### With an API key

Copy `.env.example` to `.env` and fill in `OPENAI_API_KEY`. The file is
gitignored and read at startup; an exported environment variable wins over it.

```sh
uv run cra-assistant ask "Wer gilt als Hersteller im Sinne der Verordnung?"

# Measure the trust boundary against the attack fixtures and the external corpora:
uv run cra-assistant attack --runs 3 --external
```

Every answer cites segment ids. An answer citing nothing that was retrieved is
converted into an abstention rather than shown — citation is enforced in code,
not requested in the prompt.

## Architecture

Corpus in, cited answer out. Each stage is a module and a CLI subcommand.

```
registry/sources.toml   committed declaration: url, tier, licence, parser
        │
   fetch │  APIs and raw files only. Content-addressed store, append-only
        │  manifest, ingest plausibility check.
        ▼
 segment │  Structure from the document's own text markers ("Article 13"),
        │  never from EUR-Lex CSS classes. `export-segments` dumps the
        │  result for inspection; nothing in the pipeline reads that dump.
        ▼
 Segment │  id, tier, citation, text, content checksum. Tier is materialised
        │  onto every segment; nothing looks it up at query time.
        ▼
retrieve │  Retriever protocol. Behind it, a disposable in-memory BM25 index.
        │  Ranking is tier-blind on purpose.
        ▼
 prompt │  Trusted segments plain; untrusted wrapped in delimiters they cannot
        │  close, labelled as data. This is where the boundary is enforced.
        ▼
generate │  Citations checked against what was actually retrieved. Telemetry
        │  written for every call.
```

Supporting: `verify` (drift against committed pins), `validate` (structural and
plausibility checks), `eval` (retrieval scored against a committed golden set),
`attack` (authored attack fixtures run against the trust boundary).

**[docs/architecture.md](docs/architecture.md)** is the longer version: what runs
in what order, which state persists and which is rebuilt every invocation, and
the single enforcement point behind each guarantee.

## Decisions

Each ADR records the options rejected and what the choice costs.

| ADR | Decision |
| --- | --- |
| [0001](docs/adr/0001-two-tier-trust-model.md) | Two trust tiers, materialised onto every segment, never looked up at query time |
| [0002](docs/adr/0002-registry-as-committed-data.md) | The source registry is committed TOML, not Python, so "marked trusted" is visible in a diff |
| [0003](docs/adr/0003-drift-policy.md) | Fetch records, verify judges; drift never blocks a fetch, and escalates by tier |
| [0004](docs/adr/0004-structure-based-segmentation.md) | Segment on the document's own structure, detected from text markers, not HTML classes |
| [0005](docs/adr/0005-corrigenda-as-separate-sources.md) | Corrigenda: separate sources, patched at composition, invisible in citations, stable ids |
| [0006](docs/adr/0006-walking-skeleton.md) | Build a walking skeleton with a disposable index before investing in pgvector |
| [0007](docs/adr/0007-measure-before-tuning.md) | Measure before tuning; golden set as committed data; retrieval and generation scored separately |
| [0008](docs/adr/0008-tier-blind-ranking.md) | Ranking stays tier-blind — a downranking penalty would make the injection defence untestable |
| [0009](docs/adr/0009-untrusted-content-from-apis.md) | Untrusted content from APIs, never rendered pages; plausibility as a check class distinct from stability |
| [0010](docs/adr/0010-pin-the-model.md) | Pin the model to a dated snapshot; record its id in every telemetry record and report |
| [0011](docs/adr/0011-detection-in-depth.md) | Measure the defence before adding a second one — detection in depth before defence in depth |
| [0012](docs/adr/0012-inline-provenance.md) | Inline provenance and trust as a harness fact; prediction written first, and wrong |
| [0013](docs/adr/0013-ablate-the-framing.md) | Ablate and delete the anti-injection framing — measured making the system worse |
| [0014](docs/adr/0014-harden-the-measurement.md) | Harden the measurement: external corpora, repeats, denominators, two detection paths |
| [0015](docs/adr/0015-claim-support-enforcement.md) | Require a verbatim supporting span per citation; prediction committed before the code |

`docs/journal.md` is a dated build log including the dead ends.
`docs/eval/` holds append-only measurement baselines.

## Known limitations

Each of these is verifiable from the repository. A limitation you can check is
worth more than a feature claim you cannot.

- **The golden set is drafted, not verified.** All 41 items are
  `verified = false`; nobody has checked the gold labels by hand. `eval` refuses
  to score without `--include-unverified` and stamps its report provisional. The
  five `untrusted_only` items are additionally suspect: they were drafted after
  reading the sources they are labelled against, so they inherit that vocabulary
  and their scores are inflated by the overlap.
- **Corrigenda are not incorporated.** The corpus is the Official Journal text of
  20 November 2024. `32024R2847R(01)` and `32024R2847R(04)` amend the article
  text and are not fetched, not applied and not registered. An answer citing an
  affected article quotes superseded wording, and the citation looks correct
  while doing so. The model for handling them is decided
  ([ADR-0005](docs/adr/0005-corrigenda-as-separate-sources.md)); the work is not.
- **Retrieval is untuned BM25, with measured failures.** No stemming, no stopword
  list, no embeddings. On the drafted golden set, MRR@10 is 0.346 for questions
  in the regulation's own vocabulary and **0.159** for practitioner phrasing,
  where recall@5 is **0.12**. Article 13 ranks **215th** for a question that is
  verbatim its own title, because BM25 penalises it for being long. See
  [the latest baseline](docs/eval/).
- **The trust boundary does not hold, and the current rates are published.**
  3 of 14 attacks that reach the prompt still succeed. Delimiter escapes are at
  0/5 since citations began requiring a verbatim supporting span, but authority
  mimicry is unchanged at 2/3, and one previously blocked attack now succeeds by
  citing the attack document as its own support — the span check verifies
  support, not authority. Ranking stays tier-blind
  ([ADR-0008](docs/adr/0008-tier-blind-ranking.md)), so prompt assembly is the
  only line. Reports in [docs/eval/](docs/eval/) are append-only, including the
  ones that got worse.
- **The security numbers rest on 17 self-authored fixtures plus two third-party
  corpora, three runs each, scored by string match.** That is better than where
  it started and still small. Attack classes are not disjoint — successful
  delimiter escapes fabricate citations, which the misattribution class scores
  as 0%. An attack class nobody imagined has a success rate of zero here and is
  not measured at all.
- **Long segments are truncated, not sub-split.** Annex VIII is 22,000
  characters and reaches the model clipped at 4,000, so an answer drawn from its
  later parts is not possible.
- **Telemetry is a JSONL call log and nothing more.** Model, tokens, latency,
  estimated cost, request id. No traces, no spans, no OpenTelemetry. Cost figures
  come from a hand-maintained price table that will go stale.
- **Some untrusted segment ids will rot.** Where a source provides a stable
  identifier the id uses it (`issue-137`,
  `stewards-obligations-what-must-a-steward-do`). Where none exists — an
  arbitrary web page — ids stay positional, so an upstream insertion silently
  renumbers everything after it. Recorded rather than papered over with a hash
  nobody could resolve.
- **Generation has two output shapes and the corpus needs three.** It either
  answers with citations or abstains. One golden item requires *"practitioners
  assume X, but it is not confirmed"*, which is neither. See
  [ADR-0006](docs/adr/0006-walking-skeleton.md).

## Roadmap

- [x] **Bootstrapping** — packaging, CI, ADR and journal conventions
- [x] **Corpus** — source registry with trust tiers, fetching with checksums and
      plausibility checks, structure-based segmentation, validation
- [x] **Walking skeleton** — BM25 retrieval, prompt assembly, generation with
      enforced citations and abstention, telemetry. Disposable by design
- [x] **Retrieval evaluation** — golden set as committed data, sliced metrics,
      append-only baselines. *Golden set drafted, not verified*
- [x] **Attack fixtures** — thirteen authored documents across five attack
      classes, entering by the ordinary untrusted path, with measured success
      rates per class. No mitigation added on top of them yet
- [ ] **Index** — Postgres + pgvector, hybrid retrieval
- [ ] **Generation evaluation** — faithfulness and abstention scored, as a CI gate
- [ ] **Telemetry** — OpenTelemetry, token and cost attribution
- [ ] **MCP server** as the primary interface, then deployment
- [ ] **Corrigenda** — decided in ADR-0005, not built

## License

MIT. See [LICENSE](LICENSE).
