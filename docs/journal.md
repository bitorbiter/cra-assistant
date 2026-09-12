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
