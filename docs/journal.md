# Build journal

Dated entries, newest last. Records what was built, what surprised us and what
broke — including the dead ends, because a blog post gets written from this.

## 2026-09-12 — Bootstrapping

**Built.** An empty but complete skeleton: `pyproject.toml` (uv, hatchling,
ruff, pytest), `src/cra_assistant` with nothing in it but a version string, a
smoke test that asserts the package imports, `.gitignore`, `.env.example`, a
GitHub Actions workflow running `ruff check` / `ruff format --check` / `pytest`,
the MADR ADR template, README with the trust-tier table and roadmap, MIT
licence, and `CLAUDE.md` so the next session starts with the brief instead of a
reconstruction of it.

**Decisions worth naming, none of which earned an ADR.**

- The version lives in `src/cra_assistant/__init__.py` and hatchling reads it
  from there. One source of truth beats keeping `pyproject.toml` and the module
  in sync by hand.
- `dependencies = []`. pydantic v2 is in the agreed stack, but nothing models
  anything yet, and an unused dependency in a portfolio repo is a small lie
  about what the code needs. It arrives with the first model, in step 2.
- Dev tooling sits in a PEP 735 `dev` group rather than an optional extra, so
  plain `uv sync` produces a working environment with no flags to remember.
- CI runs `uv sync --locked`, not `uv sync`. A lockfile that has drifted from
  `pyproject.toml` should fail the build loudly rather than resolve something
  else quietly. The cost: every dependency change needs the lockfile committed
  alongside it, and a forgotten `uv lock` shows up as a red CI run rather than
  as a helpful local warning.
- ruff rule set is `E, F, I, UP, B, SIM, RUF`. `I` replaces isort outright,
  which is the whole reason ruff is in the stack.

**Surprised us.** `uv` was not installed on the machine at all, which is an odd
thing to discover in the session where uv is the already-decided answer. Two
Pythons were in play: the system had 3.14.7, the project pins 3.12. Installed
uv with `pip3 install --user uv` (consistent with the other tools already in
`~/.local/bin`) rather than the curl-pipe-sh installer; uv then downloaded
CPython 3.12.14 for itself, so the system interpreter never entered the
project's environment. Committing a `.python-version` file means CI and a
stranger's laptop resolve the same interpreter without anyone installing
Python 3.12 by hand.

**Broke.** Nothing. Worth writing down that `ruff format --check .` reports
"5 files already formatted" while `ruff check . --show-files` lists three
paths — the format counter is not a file list, and `.venv` is genuinely
excluded. Ten minutes went into confirming CI would not be formatting the
virtualenv.

**Deliberately absent.** No downloading, no parsing, no retrieval, no database,
no OpenAI calls, no MCP, no Docker, and no placeholder modules for any of them.
The temptation to stub out `corpus/` "so the layout is visible" was real and was
refused; an empty directory documents nothing that the roadmap does not.

**Next.** Step 2: the corpus. Source registry with trust tiers, download with
checksums, structure-based segmentation into articles, recitals and annexes,
and validation.

## 2026-09-12 — Step 2a: source registry and trust-tier model

**Built.** `src/cra_assistant/models.py` (`Source`, `TrustTier`, `Parser`),
`src/cra_assistant/registry.py` (`SourceRegistry`, `load_registry`),
`registry/sources.toml` with the CRA from EUR-Lex in DE and EN as trusted plus
three community sources as untrusted, twelve tests, and ADR-0001 (two-tier trust
model) and ADR-0002 (registry as committed data). pydantic v2 joins
`dependencies`; TOML parsing is stdlib `tomllib`, so no second dependency.

**The design constraints came from review, not from us**, and two of them
changed the shape of the code:

- *Source is purely declarative.* Checksum, retrieval timestamp and byte count
  are facts about a fetch, not properties of a source. The nice part is that
  this did not have to stay a convention: `extra="forbid"` on the model turns
  "someone adds `checksum` to a registry entry" into a validation error naming
  the entry. There is a test asserting exactly that, which is really a test that
  the next session cannot casually undo the decision.
- *Tier is materialised onto every segment, never looked up at query time.* The
  reasoning is in ADR-0001: a lookup is a second source of truth that can be
  skipped or can default permissive, and the failure mode of a missing tier has
  to be a crash rather than a guess. Nothing enforces this yet — there is no
  ingest to enforce it in. It is written down so the ingest step inherits it.

**Surprised us.** Putting `min_length=1` on `sources` produced a *misleading*
error. A registry with one bad entry reported three problems: the two real ones
plus "Tuple should have at least 1 item after validation, not 0", because the
bad entry had been dropped. So a file with a typo claimed to be an empty
registry. Moved the emptiness check into the `mode="after"` validator, which
only runs once the entries parse, and the phantom error went away. General
lesson: field-level cardinality constraints on a collection of validated models
report cascades, not causes.

Also: we did not wrap pydantic's `ValidationError` in a project-specific
exception. The instinct was to add one for a tidy API. Looking at the actual
output — `sources.0.tier: Input should be 'trusted' or 'untrusted'` — a wrapper
would have made it worse, not better. Left it alone.

**Judgement calls worth naming.**

- `Parser` is an enum, not a free string, so a typo cannot silently select a
  default. It names parsers that do not exist yet. That sits close to the "no
  placeholder modules" rule, and the line we drew is: a declaration of what
  segmentation must handle is fine, an empty `parsers/` package would not be.
- `licence = "UNKNOWN"` on the untrusted sources. Guessing a licence would be
  worse than admitting we have not checked.
- Untrusted sources are community forums and working groups (ORC WG CRA Hub,
  OpenSSF policy WG discussions) rather than named commercial vendors, per
  review. Cheap to change later and avoids implying anything about a company.

**Broke / unverified.** Nothing broke. But this step had no network access by
design, so **none of the six URLs in the registry has been fetched**. They are
written from memory and the untrusted three are the least certain. The fetch
step is what confirms them, and correcting a 404 there is expected, not a
failure. The registry header says so in the file itself.

**Still not enforced anywhere.** The trust boundary is currently a field on a
model and an argument in an ADR. No prompt is assembled, so nothing yet
*prevents* untrusted text from being treated as instructions. That is the whole
point of the project and it is entirely unbuilt. Worth being honest about that
in the README before anyone reads the ADR and assumes otherwise.

**Next.** Step 2b: fetching. Download each source, record checksum, retrieval
timestamp and byte count in a manifest — the facts deliberately kept off
`Source` — and make re-fetching detect change rather than silently overwrite.

## 2026-09-12 — Step 2b: fetching and the manifest

**Built.** `fetch.py` (httpx, timeout, retry with exponential backoff,
identifiable User-Agent, one request per source, polite delay between sources),
`manifest.py` (append-only JSONL of observations), `verify.py` (pin file, drift
detection, tier-aware escalation), `cli.py` (argparse: `fetch`, `verify`),
`registry/pins.toml`, ADR-0003, 29 new tests. httpx joins `dependencies`;
argparse is stdlib and two subcommands do not justify a CLI framework.

**Which URLs were wrong.** One of five, not one of six — the registry has five
sources (2 trusted + 3 untrusted), and the brief said six. Corrected:

| source | verdict |
| --- | --- |
| `cra-eurlex-en`, `cra-eurlex-de` | 200, both fine |
| `orcwg-cra-hub-faq` (raw.githubusercontent) | 200, real 8,893-byte community FAQ |
| `orcwg-cra-hub-discussions` | **404** — Discussions is disabled on that repo |
| `ossf-cyber-policy-discussions` | 200, Discussions genuinely enabled |

The 404 was replaced with `orcwg-cra-hub-issues`
(`github.com/orcwg/cra-hub/issues`, 137 open issues), which is arguably a better
untrusted specimen anyway: an issue tracker anyone can post to is closer to the
threat model than a discussions tab.

**The surprise, and it is the good kind: the drift argument turned out to be
provable in thirty seconds.** The review decision said to ship `verify` disabled
because raw-byte checksums over an EUR-Lex page would drift on nearly every
fetch. Rather than assert that in the ADR, we fetched the English text twice,
seconds apart:

```
712,856 bytes  sha256:bb1249aac7ec…
712,855 bytes  sha256:bf8254e31435…
```

Diffing the two stored copies, the *entire* difference is inside one
`data-dtconfig` attribute belonging to a Dynatrace RUM script, which embeds a
per-request `agentId` and `rpid` in the markup. Not one character of legal text
differs. So the gate would have fired on every single run, for a reason with no
connection to the regulation. ADR-0003 now carries the byte counts and the
mechanism by name instead of a hand-wave. The Markdown source, fetched twice,
produced an identical digest and no second file — content-addressing working
exactly as intended, and a useful control.

**Two bugs we wrote and caught.**

1. `FetchPolicy` is a `@dataclass(frozen=True, slots=True)`, and we used
   `FetchPolicy.timeout` as an argparse default. With `slots=True` the class
   attribute is a **slot descriptor, not the default value** — argparse would
   have handed httpx a `<member 'timeout'>` object as a timeout. Caught by
   printing the parsed args before writing any test. Fixed by reading defaults
   off a module-level `DEFAULT_POLICY` instance, which ruff's bugbear rule had
   already pushed us towards for an unrelated reason (`B008`).
2. `fetch_one(client, source, ...)` took the client positionally while
   `fetch_sources(sources, *, client=...)` took it by keyword. The tests were
   written against the consistent shape and failed, which was the tests being
   right. Changed the function rather than the tests.

**A third bug, found by running the thing rather than testing it.** A clean
clone of the repo — no `data/`, since it is gitignored — reported every trusted
source as `unfetched  recorded, no action needed`. The note was chosen by tier
before status, so "untrusted drift is not an event" leaked onto a case that is
not drift at all. An unfetched source needs fetching whatever its tier. Fixed by
extracting `note_for()` and letting status decide first, with a parametrised
test over all eight tier×status combinations. Worth noting *how* it was found:
41 green tests did not catch it, a clean clone and one glance at the output did.

**Judgement calls.**

- Failures are *returned* from `fetch_sources`, not raised, so one dead URL does
  not hide the other four; the CLI prints them all and exits non-zero. A failed
  fetch writes nothing — no manifest line, no file. An empty document silently
  entering the corpus is worse than a missing one, because retrieval cannot tell
  "nothing was said about this" from "the page failed to load".
- File extension comes from the declared `parser`, not the served
  `Content-Type`, because raw.githubusercontent.com serves Markdown as
  `text/plain`. The registry describes the bytes better than the server does.
- Only 408/425/429 and 5xx are retried. Repeating a 403 is rude, not useful.
- Added `paths.py`. Three modules had independently grown
  `Path(__file__).resolve().parents[2]`, which is a fragile assumption worth
  stating once.

**Debt recorded, not paid.**

- *The `Parser` enum is still a promise.* It names `eurlex-html`,
  `generic-html` and `markdown`, and now also drives file extensions — so it has
  started doing real work while the parsers it names still do not exist. There
  is an import-time check that every `Parser` member has an extension, which
  keeps the two in step, but the debt is real: step 3 has to make the names true.
- *Corrigenda.* The registry declares the OJ text of 20.11.2024.
  32024R2847R(01) and R(04) amend the article text and are not handled — not
  fetched, not reconciled. Any answer citing an amended article would cite
  superseded wording. Noted in `sources.toml`; the open question is whether a
  corrigendum is a separate source or a patch applied to one.
- *Nothing prunes `data/`.* It grows monotonically, one file per distinct
  version. Fine at five sources; needs a retention policy eventually.
- *Nothing enforces acknowledgement.* A stale pin on a trusted source will never
  fail a test. Today that is a social commitment, not a mechanical one.

**Next.** Step 3: parsing and segmentation — EUR-Lex HTML into articles,
recitals and annexes, with the tier stamped onto every segment as ADR-0001
requires. That is also what makes a content checksum possible, and therefore
what arms `verify`.

## 2026-09-12 — Step 3a: segmentation, content checksums, arming the gate

**Built.** `parse.py` (block extraction via stdlib `html.parser`, DE and EN
language profiles), `segment.py` (one segmenter per declared parser, content
checksums), `validate.py` (contiguity, ordering, short-segment warnings),
`Segment` model, `parse` and `validate` CLI commands, committed HTML fixtures,
ADR-0004 and ADR-0005, 49 new tests. No new dependency: `html.parser` is
stdlib, and the alternative would have been BeautifulSoup for a job that turned
out to be forty lines.

**The numbers came out right first time**, which was not expected: 130 recitals,
71 articles, annexes I–VIII, in both English and German, matching the brief's
expectations exactly. Two things had to be fixed before that was true, and both
were found by *looking at the output*, not by reasoning:

1. **All 38 footnotes were inside Article 71.** The article region had a
   beginning and no end. Fixed with a closing-formula marker per language —
   "Done at " / "Geschehen zu " — which is where the enacting terms actually
   stop. Same class of bug at the other end: the ELI link and ISSN line were
   inside Annex VIII, fixed with a document-footer marker.
2. **`validate` demanded recitals from a community FAQ.** Structure checks were
   applied to every document rather than to legal instruments, so the Markdown
   FAQ reported three errors for not being a regulation. Which checks apply is
   now a property of the declared parser.

**The decision that will look wrong at a glance.** EUR-Lex marks recitals with
`id="rct_1"` and articles with `class="oj-ti-art"`. Using them would be a
five-line parser. We match the text `Article 13` instead, because those class
names belong to one Official Journal generation and have changed before, while
the regulation's own wording cannot. The reviewer's instruction was right and
ADR-0004 records why.

The subtlety that made markers workable at all: **only block-level elements may
break a line.** EUR-Lex renders footnote references as `<a>(<span>1</span>)</a>`
inside a paragraph. Split on every tag and that becomes a line reading `(1)`,
indistinguishable from a recital number. Split only on block elements and the
footnote stays inline where it belongs. There is a test for exactly this, and it
is the single load-bearing assumption of the parser.

**The gate is armed, and the evidence is direct.** ADR-0003 deferred blocking
because raw-byte checksums drifted on every EUR-Lex fetch. Now measured on the
two stored responses whose raw digests differ:

```
bb1249aac7ec.html  raw bb1249aac7ec  ->  content 2fe6876e8ef0  (209 segments)
bf8254e31435.html  raw bf8254e31435  ->  content 2fe6876e8ef0  (209 segments)
```

Same content checksum, different bytes. Then verified end to end by actually
refetching (real raw drift, `exit 0`) and by tampering with a pinned content
checksum (`BLOCKING`, `exit 1`). `GATE_ENABLED` is now `True`.

**Where we deviated from the brief, and why.** The instruction was "verify now
BLOCKS in CI". Taken literally that means CI fetches on every push, which
ADR-0003 had rejected as impolite to EUR-Lex and as a network dependency in a
pipeline that had none. Implemented as a **separate `drift` job on a weekly
schedule plus manual dispatch**, which blocks; the push/PR job stays
network-free. So drift blocks in CI, but not on the pull-request path. Flagged
rather than assumed — it is a real reading difference.

Also: `validate` exits non-zero on *errors* only. Short segments are warnings.
Five CRA articles genuinely are one sentence (Articles 29, 48, 50, 66, 67 — the
same five in both languages, which is good evidence they are real and not
truncated), so a hard length floor would have been wrong about actual text. That
is "flag, don't loosen", not a loosened check.

**Judgement calls.**

- `Source` gained two fields, `citation_prefix` and `short_title`. Segment ids
  need a short stable prefix (`cra-de:article:13`) and citations need a name for
  the work. Both are declarative facts a parser cannot infer, so they belong in
  the registry. Added a registry invariant that prefixes are unique, since two
  sources sharing one would mint colliding segment ids.
- Fixtures are **slices of the real document with unmodified markup**, not
  hand-written imitations. A hand-written imitation drifts from reality and the
  tests keep passing while the parser stops working. The excerpts deliberately
  include the signature block, a footnote and the OJ footer — the exact parts
  that get wrongly swept into the last article and last annex.
- `verify` re-derives content checksums from stored bytes on every run instead
  of reading what `parse` wrote. A cached intermediate could go stale against
  the segmenter, and a stale content checksum is the precise failure the gate
  exists to catch.
- Added `tests/factories.py` after adding two required `Source` fields broke 31
  tests across three files, each with its own copy of the same builder.

**Debt paid.** `test_every_declared_parser_has_an_implementation` now asserts
`set(SEGMENTERS) == set(Parser)`. Paying it meant writing the Markdown and
generic-HTML segmenters, both deliberately shallow: split at headings, and a
page with no headings becomes one segment. Honest rather than good — inventing
boundaries where a document has none is what ADR-0004 exists to avoid — but the
GitHub issue list segments badly and will need attention when untrusted
retrieval is actually exercised.

**Debt taken on.** Segment lengths run from 148 characters (Article 29) to
22,000 (Annex VIII). No embedding model takes the long end, so indexing will
need a sub-segment split that preserves the article-level citation. That is
harder than uniform chunking would have been and is recorded in ADR-0004's
consequences, not hidden.

**Still not enforced.** No prompt is assembled anywhere, so the trust boundary
remains a field on a model. Corrigenda R(01) and R(04) are still unapplied: the
model is decided (ADR-0005) and the corpus is knowingly stale.

**Next.** Step 3b as briefed — corrigendum patching — or the roadmap's poison
fixtures. ADR-0005 argues 3b is harder than it sounds: corrigenda are written
for human readers ("for 'shall' read 'should'"), and turning that into a
reliable transformation may end in a reviewed, committed patch file rather than
an automated one.

## 2026-09-12 — Step 4: the thinnest end-to-end path

**Built.** `retrieve.py` (a `Retriever` protocol and an in-memory BM25
implementation), `prompt.py` (tier-aware assembly, pure), `generate.py` (OpenAI
call, citation enforcement, abstention), `telemetry.py` (JSONL call log), the
`ask` command, ADR-0006, 46 new tests. `openai` joins the dependencies; BM25 is
forty lines written here rather than a dependency on something we plan to
delete. Corrigenda stayed unregistered as instructed, and the README gained a
"Known limitations" section saying so.

**The finding, which is the whole point of the step.** Asked the acceptance
question — *"Wer gilt als Hersteller im Sinne der Verordnung?"* — BM25 returns
German segments (good) but the actual definition of "Hersteller" is Article 3,
and it ranks **16th**. Outside any sensible `k`. Two visible reasons:

- Article 3 is 10,930 characters of definitions, and BM25's length
  normalisation (`b = 0.75`) punishes it hard for that.
- Five of the eight query tokens are German stopwords — *wer, gilt, als, im,
  der* — which match nearly everything and dilute the one term that matters.

Both are fixable in ten minutes. Neither is fixable *correctly* without a way to
measure whether the fix helped, and that is exactly the argument for building
evaluation before pgvector. We deliberately did not tune it. A tuned number with
no measurement behind it is worse than an untuned one, because it looks like
evidence.

Related: BM25 returns *something* whenever any word matches, so asking about the
GDPR still retrieves six German segments. Abstention therefore rests entirely on
the model, not on retrieval. A retrieval score floor is an obvious candidate — and
is exactly the kind of threshold that should be set by measurement, not by taste.

**The design decision worth keeping.** The `Retriever` protocol is the artefact
meant to survive this step; `Bm25Retriever` is not. Everything downstream depends
on `retrieve(query, k) -> list[Segment]`. The risk with disposable code that
works is that it quietly becomes permanent, so ADR-0006 states in writing what
this step is allowed to be wrong about (retrieval quality, segment size, answer
quality, injection resistance, cost estimates, scale) and what it is not (the
protocol shape, untrusted rendering, the citation rule, key handling, telemetry).

**Citations are enforced in code, not requested in the prompt.** The prompt asks
for citations; `enforce_citations` drops any cited id that was not in the
retrieved set and converts an answer with no surviving citation into an
abstention. Verified end to end with a stubbed provider: a model claiming
`cra-de:article:999` produces

```
No answer from the corpus.
Reason: cited segments that were not retrieved: cra-de:article:999;
        the answer cited no retrieved segment, so it is not grounded
```

A fabricated citation is the worst thing this system can emit, because it is
wrong in precisely the way that looks right. That deserves a rule, not a request.

**The trust boundary became text.** Untrusted segments are wrapped in
`<untrusted-content>` … `</untrusted-content>`, labelled as data, and the system
prompt states that nothing inside is ever an instruction. `neutralise_delimiters`
strips the closing tag from untrusted text so content cannot end its own box —
the oldest attack on delimiter framing. This is framing, not a defence, and
ADR-0006 says so: it has never been tested against a real attack, because the
poison fixtures do not exist yet.

**Two bugs from the session.**

1. `ask` crashed with `AttributeError: 'Namespace' object has no attribute
   'source_ids'` because the shared `segments_for` helper expects a flag that
   only some subcommands define. Fixed with an explicit default on the parser
   rather than a `getattr` — the flag genuinely does not apply to `ask`, and
   saying so is better than tolerating its absence.
2. Making short segments an error broke the untrusted sources, which have
   dozens of legitimately short sections. The strict rule now applies only to
   legal instruments, matching the earlier decision that check applicability
   follows the declared parser.

**A test that was wrong about the code, twice over.** `test_the_best_match_ranks_first`
asserted that "manufacturers" matches "manufacturer". It does not — there is no
stemming. The test was wrong, but the *limitation* is real and now has its own
named test asserting the gap, rather than being buried in a docstring.

**What is not verified.** There is no `OPENAI_API_KEY` on this machine, so **no
live call was made**. Everything up to the provider boundary is exercised — with
a stub client through the real CLI code path, covering the answered, abstained
and fabricated-citation cases, plus telemetry — but the two acceptance commands
in the brief have not actually been run against OpenAI. The live smoke test
exists, is marked `live`, and is deselected by default. This is the one
"done when" criterion that is unconfirmed rather than met.

**Judgement calls.**

- Added `ask --show-prompt`, which prints the assembled prompt and exits without
  calling the model or needing a key. Not asked for. Justified because the
  trust-boundary rendering is the most interesting thing in this step and it
  should be inspectable by anyone who clones the repo, key or not.
- The telemetry `CallRecord` has a fixed shape with no free-form dict, so a
  credential has nowhere to land by accident. Provider errors record the
  exception *class name* only: 401 messages have been known to echo request
  headers. There are tests asserting a key cannot reach the record, the log or
  the client's `repr`.
- Model defaults to `gpt-4o-mini` via `CRA_MODEL`. Cheap on purpose; this step
  is about whether the path works.
- Deleted `data/calls.jsonl` at the end of the session. Every record in it came
  from a stub, and fabricated cost data in a real log would mislead later.

**Next.** Evaluation, out of roadmap order. This step produced a concrete,
measurable deficiency — a definition at rank 16 — and fixing retrieval without a
way to tell whether a change helped would be guessing with extra steps.

## 2026-09-12 — Step 5a: golden set and retrieval evaluation

**Built.** `eval/golden.toml` (40 drafted items), `golden.py` (loader,
validated like the registry), `evaluate.py` (metrics, slicing, report
rendering), the `eval` command, `docs/eval/baseline-2026-09-12.md`, ADR-0007,
31 new tests. No new dependency. **`retrieve.py` is untouched**, which was the
hard constraint of the step.

**The measurement found something worse than what we were looking for.**
ADR-0006 left a known complaint: Article 3 at rank 16 for a definition
question. The first eval run produced a sharper one.

> **Article 13 ranks 47th for a question that is verbatim its own title.**

Asked *"What are the obligations of manufacturers under this Regulation?"*,
BM25 puts Article 13 — titled *Obligations of manufacturers* — in 47th place.
It scores 11.6 against a top score of 16.8, and the segments beating it include
a 450-character article that mentions manufacturers once. Article 13 is 15,386
characters, and `b = 0.75` length normalisation charges it the full penalty for
that. Nothing about staring at the code would have surfaced this; it took a
labelled question and a number.

Also worth recording: an **untrusted** FAQ section ranked 3rd for that question
about statutory obligations. Retrieval has no tier preference at all. Whether it
should is a real design question — the trust boundary currently governs how
segments are *rendered*, not how they are *ranked*.

**The slice that justified the whole step.**

| slice | n | R@1 | R@10 | MRR@10 |
| --- | ---: | ---: | ---: | ---: |
| statute vocabulary | 17 | 0.35 | 0.71 | 0.455 |
| practitioner vocabulary | 13 | 0.08 | 0.69 | 0.185 |

A 2.5× MRR gap between the same corpus asked two ways. The overall MRR of 0.338
would have read as one uniform mediocrity; the slice says the system is roughly
adequate for people who already know the regulation's wording and close to
useless for anyone else. That is a different problem with a different fix.

**Retrieval hands over ten distractors for every unanswerable question.** Zero
of ten unanswerable items returned nothing — mean 10.0 segments returned —
including *"What is the best way to keep sourdough starter alive over winter?"*
With no stopword list, `the`, `best`, `way`, `keep` and `over` all match
something in 488 segments. So abstention is entirely the model's problem, and
the retrieval layer contributes nothing to it. A score floor is the obvious
answer and is exactly the sort of threshold that should be set by measurement.

**The finding I did not want: the untrusted tier is empty of content.** Drafting
the five `untrusted_only` items meant reading what the untrusted sources
actually contain, and they contain nothing usable:

- `orcwg-cra-hub-faq` is a **link index**. Its answers live on
  `cra.orcwg.org/faq/...` pages we never registered.
- `orcwg-cra-hub-issues` and `ossf-cyber-policy-discussions` are **rendered in
  the browser**. A static fetch captured the GitHub navigation menu and a
  literal "Uh oh! There was an error while loading. Please reload this page."
  Twenty and twenty-eight segments of chrome, zero of CRA discussion.

So step 2b happily fetched 694 KB of nothing, verified its checksums, pinned it,
and reported everything clean — because every check we built asks whether the
bytes are *stable*, and none asks whether they are *useful*. The five
`untrusted_only` items are therefore about meta-facts of a community document
(its disclaimer, its maturity process, which questions it thinks need Commission
guidance) rather than contested interpretation. Honest, and thin. Fixing it is a
corpus problem: register the actual FAQ answer pages, or wait for the authored
poison fixtures.

**A durability problem with untrusted gold labels.** Article ids are permanent
by ADR-0005. Untrusted segment ids are *positional* — `orcwg-faq:section:3` is
"the third heading" — so an upstream edit that inserts a heading silently
reassigns every label after it. The five untrusted labels will rot, and the
notes in `golden.toml` say so. Content-addressed or heading-slug ids would fix
it; that is a segmentation change and not this step.

**Division of labour, as agreed.** All 40 items are `verified = false`. `eval`
refuses to score at all by default:

```
No verified golden items: all 40 are `verified = false`.
Nobody has checked the gold labels by hand, so scoring them would produce a
number that looks like a measurement and is not.
```

`--include-unverified` gets past it and stamps the report header as provisional.
The refusal is deliberately annoying: labels drafted by a model, scored by the
same model's code and quoted later as a measurement is the exact failure mode
worth designing against. Composition came out at 25 answerable / 10 unanswerable
/ 5 untrusted-only, 21 statute / 19 practitioner, 27 EN / 13 DE — the language
split is more English-heavy than intended and is worth rebalancing during
verification.

**Metric decisions worth naming.**

- `recall@k` is **true recall** — the fraction of an item's gold labels in the
  top k — not hit rate. A two-label item cannot score 1.00 at k=1, which is
  intended: one lucky hit should not stand for both. Documented in the report.
- Unanswerable items are excluded from recall entirely rather than scored as
  zeros or ones. Either choice would have silently moved the headline number.
  They get their own table measuring what retrieval handed over.
- Gold labels naming segments outside the corpus are reported as **broken
  labels**, not scored as misses. A typo in the golden set and a retrieval
  failure look identical in a number, and only one of them is retrieval's fault.

**What went wrong in the writing.** Test item ids of `"a"` and `"b"` failed the
loader's `min_length=3`. Trivial, but a fair hit: the validation rule I wrote an
hour earlier caught my own sloppiness in the test helper.

**The constraint held.** `git diff` shows no change to `retrieve.py`. Every
temptation to fix a number in this commit — a stopword list, lowering `b`, a
tier boost — was left alone. They are now hypotheses with a baseline to test
against, which is worth more than the ten minutes each would have taken.

**Next.** 5b, presumably: Achim verifies the labels and rewrites half into real
practitioner phrasing, at which point the numbers stop being provisional. Only
then is there a baseline worth defending, and only then do thresholds (5c) mean
anything.

## 2026-09-12 — Step 2c: repairing the untrusted sources

**Built.** `github.py` (issues and comments via the REST API, repository
Markdown via raw file hosting), `plausibility.py` (the new check class),
`problems.py` (shared `Problem`/`Severity`), two new parsers with stable segment
ids, four repaired untrusted sources, ADR-0008 and ADR-0009, 40 new tests. No
new dependency.

**A green pipeline transporting nothing.** This is the entry worth writing the
blog post around, so it is worth being precise about how the failure survived.

By the end of step 2b the project had five layers of checking, and every one of
them passed on a document that was a GitHub navigation menu:

| check | what it asked | verdict on the chrome |
| --- | --- | --- |
| raw checksum | did the bytes change? | stable ✓ |
| content checksum | did the extracted text change? | stable ✓ |
| pins | has a human approved this digest? | approved ✓ |
| drift gate | did trusted content move? | clean ✓ |
| structural validation | are the article numbers contiguous? | not a legal instrument, no findings ✓ |

None of them was broken. Every one was answering the question it was designed to
answer, correctly. **They all asked whether the bytes were *stable*. None asked
whether they were *useful*.** A navigation menu is beautifully stable; it hashes
reproducibly forever and has no missing article numbers because it is not a
regulation. So the pipeline was green, the report said five sources, and the
corpus carried two.

That is why the fix is a *check class*, not a patch. `plausibility.py` asks a
question none of the existing machinery could: is there anything here? Stability
and usefulness are orthogonal, and we had built five of one and zero of the
other while feeling well covered.

The thresholds came out of measurement rather than taste — text as a fraction of
raw bytes was 0.485 and 0.506 for the real EUR-Lex exports and 0.014 for both
broken pages, so the floor sits at 0.10 with two orders of magnitude of daylight
on either side. Plus literal client-render markers, because "Uh oh! There was an
error while loading" was *in our corpus, checksummed and pinned*, and a list of
observed failures needs no justification a heuristic would.

**The repair.** Untrusted content now comes from APIs and raw files, never from
rendered pages. github.com serves issue lists as an application shell; the REST
API serves the same content as data. The FAQ answers turned out to be in the
repository all along as ~85 Markdown files — we had registered the link index.

Untrusted tier: **70 segments of navigation chrome → 1,383 segments of real
argument.** Corpus 488 → 1,801.

**And retrieval got worse.** This was the surprise, and it is a good one:

| slice | before repair | after repair |
| --- | ---: | ---: |
| overall MRR@10 | 0.338 | 0.230 |
| statute MRR@10 | 0.455 | 0.322 |
| practitioner MRR@10 | 0.185 | **0.033** |
| practitioner R@5 | 0.23 | **0.00** |

Community discussion is *written in practitioner vocabulary* — it is
practitioners writing it — so it outcompetes the regulation precisely on the
questions the regulation was already hardest to retrieve for. Asked "What are
the obligations of manufacturers?", the top four results are now GitHub issues
and FAQ answers, with the first article fifth.

The earlier numbers were flattered by an empty untrusted tier. Nothing regressed
except our information about ourselves, which improved. Recorded as the measured
cost of tier-blind ranking in ADR-0008 rather than buried.

**Two ADRs, and the harder one was ADR-0008.** Ranking stays tier-blind. The
tempting fix for the crowding above is a 0.7 multiplier on untrusted scores, and
it is wrong for a reason that took a while to articulate: *a ranking penalty
makes the prompt-level defence untestable*. If ranking suppresses untrusted
content, an injection test that passes cannot distinguish "the defence held"
from "the attack never arrived". The defence would be shielded from evaluation
by the mechanism meant to support it. It is also a probabilistic barrier dressed
as a security control — downranking does not exclude, it just requires a
slightly better term match. The whole boundary now rests on composition, which
is a single point of failure and is stated as one.

**Stable ids, which we got almost by accident.** Fetching from APIs supplied
real identifiers, so `section:3` ("the third heading", silently reassigned by
any upstream insertion) became `issue-137`,
`issue-137-comment-2574583778`, and
`stewards-obligations-what-must-a-steward-do`. There is a test asserting that
inserting a new issue does not renumber the existing ones. Where no stable
identifier exists — an arbitrary web page — ids stay positional and ADR-0009
says so. A content hash would look stable and be useless: nobody can follow
`section:a3f9c2` back to anything, and it changes when a typo is fixed.

**One instruction I could not carry out.** The brief asked for "the ORC WG's
programmatically generated CRA Markdown copy". It does not exist. I checked all
eight `orcwg` repositories, the full `cra-hub` tree, the website repository and
`cra.orcwg.org/cra/`, `/cra-text/`, `/regulation/` (all 404). There is no
community Markdown mirror of the regulation.

What does exist, and fills the same role almost exactly, is a programmatically
generated Markdown copy of the **European Commission's own CRA FAQ**, sitting in
the ORC WG website repository, whose first line reads:

> This document was not originally written in Markdown, so errors may have
> occurred during the conversion. Please check the original PDF for accuracy.

Official in origin, machine-converted, community-hosted, and self-declaredly
capable of diverging from the authentic version. Registered as
`ec-cra-faq-markdown-mirror`. It is the Commission's FAQ rather than the CRA
text, so it is a substitution and is flagged as one.

**Cost of the repair.** The registry now carries API URLs, so a reviewer can no
longer click a source and see what it is — a real loss in reviewability, traded
for a source that works. Fetch now segments every document in order to check it.
GitHub's unauthenticated limit of 60 requests an hour forced a page cap of 8 per
collection and comments from the repository-wide endpoint rather than one
request per issue.

**Housekeeping.** The five `untrusted_only` golden items are deleted, with a
comment in `golden.toml` explaining why and what should replace them. They tested
meta-facts about a link index and their labels were positional. A new baseline
was taken rather than editing the old one, which stands as the record of what the
system did when the corpus was still hollow.

**Next.** Re-author the untrusted golden items against 1,383 segments of real
community argument, and verify the drafted labels. Then generation evaluation —
the crowding finding above is precisely the case where retrieval metrics cannot
tell you whether the answer was good, only that the regulation ranked fifth.
