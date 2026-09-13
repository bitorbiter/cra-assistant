# ADR-0009: Fetch untrusted content from APIs, and check plausibility at ingest

- Status: accepted
- Date: 2026-09-12

## Context

Three of the four untrusted sources in the registry contained nothing.

`orcwg-cra-hub-issues` and `ossf-cyber-policy-discussions` were registered as
github.com pages. GitHub serves those as an application shell that fills itself
in from the browser, so a static fetch returned a navigation menu, a repository
header, and the literal text:

> Uh oh! There was an error while loading. Please reload this page.

Twenty and twenty-eight segments of that, respectively. Zero segments of
discussion. The third, `orcwg-cra-hub-faq`, was the FAQ's *index* — a list of
links to answer pages we never fetched.

None of this was noticed for two full steps, and the reason is the part worth
recording. Every check the project had asked whether the bytes were **stable**:

- checksums over raw bytes,
- content checksums over extracted segment text,
- pins reviewed and committed by a human,
- a drift gate that blocks CI for trusted sources,
- structural validation of recital, article and annex numbering.

All of them passed. They were *correct*. A navigation menu is perfectly stable,
hashes reproducibly, and has no missing article numbers because it is not a
legal instrument. The pipeline was green while transporting nothing, and it
would have stayed green forever, because stability and usefulness are different
properties and we had only ever measured one of them.

## Decision

Two changes, one to how untrusted content is acquired and one to what is checked.

**Untrusted content comes from APIs and raw files, never from rendered pages.**

- GitHub issues and their comments come from the REST API as JSON.
- Repository documents come from raw file hosting as their source text.
- A source URL pointing at a rendered listing page is refused at parse time
  rather than fetched and hoped for.

**Every fetched document passes an ingest plausibility check**, at both tiers,
before it is stored or recorded:

- known client-render markers ("there was an error while loading", "you need to
  enable JavaScript", …),
- a text-to-markup ratio floor for markup formats,
- a minimum segment count and a minimum quantity of extracted text.

A failing document is treated exactly like a failed HTTP fetch: nothing stored,
nothing added to the manifest, a loud error naming the numbers.

**Failure is an error at both tiers.** A trusted source yielding nothing is
obviously wrong. An untrusted source yielding nothing is also wrong — not
because the content matters less, but because a source that cannot contribute
evidence has no business being in the registry, and leaving it there means the
corpus reports six sources while carrying three.

## Rationale

Plausibility is a **different class of check** from stability, and that framing
is the actual lesson. Stability asks *did this change?*; plausibility asks *is
there anything here?* No amount of the first implies the second, and we had
built five layers of the first while believing we were covered.

The thresholds are set from measurement rather than taste. Extracted text as a
fraction of raw bytes:

| document | ratio |
| --- | ---: |
| EUR-Lex English HTML | 0.485 |
| EUR-Lex German HTML | 0.506 |
| GitHub issues page (broken) | 0.014 |
| OpenSSF discussions page (broken) | 0.014 |

The floor is 0.10 — an order of magnitude below the good documents and seven
times above the bad ones. It does not need to be precise because the populations
are two orders of magnitude apart; a check that only fires in an unambiguous case
is a check nobody will be tempted to weaken.

The client-render markers are literal strings rather than a heuristic, because
these are the exact phrases that were sitting in the corpus. A heuristic would be
cleverer and would need justifying; a list of observed failures needs only to be
extended when a new one is observed.

Fetching from APIs also fixed a second problem that was not the stated one:
**stable segment ids**. Positional ids (`section:3` meaning "the third heading")
silently reassign themselves when anything upstream is inserted, so a citation or
a gold label pointing at one rots without failing. APIs supply real identifiers,
and segment ids now use them:

| source kind | id | stable against |
| --- | --- | --- |
| GitHub issues | `issue-137` | new issues, closed issues, new comments |
| GitHub comments | `issue-137-comment-2574583778` | anything else in the repo |
| repository Markdown | `stewards-obligations-what-must-a-steward-do` | file reordering, new files |
| Markdown document | `when-is-a-product-with-digital-elements` | new sections above it |

**Where no stable identifier exists, we say so rather than invent one.** An
arbitrary web page fetched as `generic-html` still gets positional ids, because
the page offers nothing better. A content hash would be stable-looking but
useless: nobody can follow `section:a3f9c2` back to anything, and it changes when
a typo is fixed. Positional-and-honest beats stable-and-meaningless, and the
limitation is recorded here and tested.

## Consequences

- The untrusted tier went from 70 segments of navigation chrome to roughly 1,380
  segments of real argument — issues, comments, and community FAQ answers.
- Untrusted citations are now durable enough to be worth writing down, which is
  what makes re-authoring the `untrusted_only` golden items worthwhile.
- The registry now carries API URLs rather than human-facing ones. A reviewer
  can no longer click a source URL and see what it is, which is a genuine loss
  in readability traded for a source that actually works.
- GitHub's unauthenticated rate limit is 60 requests an hour, so fetching is
  capped at 8 pages per collection and comments come from the repository-wide
  endpoint rather than one request per issue. `GITHUB_TOKEN` raises the limit if
  set; it is optional and never logged.

  *Added 2026-09-13.* **The cap truncated silently, and it already had.** Reaching
  8 pages with a next page still offered returned what had arrived, which was then
  stored, checksummed, pinned and segmented as the whole collection.
  `orcwg-cra-hub-issues` holds exactly 800 comments; GitHub reports 11 pages, so
  between 1,001 and 1,100 exist, and the newest are the ones missing. Found by
  external code review, not by any check: every check here asks whether a document
  is stable or plausible, and a truncated collection is both. Reaching the cap with
  pages remaining now raises `IncompleteFetchError` and stores nothing, and every
  manifest record carries `item_counts` and `segment_count`, so a collection
  sitting on a page-size multiple is visible as a number.
- Fetch now segments every document in order to check it, so fetching costs a
  parse. At this corpus size that is imperceptible.
- **The plausibility check is a floor, not a guarantee.** It catches documents
  that are empty or are obviously the wrong thing. A source serving fluent,
  well-structured text about the wrong subject passes it comfortably, and
  nothing here would notice.
- Author logins are deliberately not copied into the corpus. The `html_url`
  identifies the author to anyone who follows it, so there is no reason to hold
  personal data we have no use for.

## Rejected alternatives

### Render the pages with a headless browser

Run Playwright, wait for the client-side render, capture the resulting DOM.

Rejected on weight and on trust. It adds a browser to the dependency tree, to CI
and to any deployment, for content that is already available as JSON from a
documented API. It is also considerably worse for the project's actual subject:
executing a page's own JavaScript to obtain untrusted content means running code
supplied by the same untrusted party whose text we are trying to contain. Using
the API is simpler, faster, more stable, and does not require executing anything.

### Download the repository as a tarball

One request instead of one per file, no rate-limit pressure at all.

Genuinely attractive and rejected narrowly. It is neither an API response nor a
raw file, so it sits outside the rule this ADR is establishing, and it introduces
archive handling — member filtering, size caps, path traversal — for a saving
that matters only at a repository size we do not have. Worth revisiting if the
document count grows; the rule should then be amended explicitly rather than
quietly bent.

### Warn on implausible untrusted sources instead of failing

Let a thin untrusted source through with a warning, on the grounds that untrusted
content is optional anyway.

Rejected because it is how the original bug survived. A warning in a corpus
pipeline that prints hundreds of legitimate warnings is invisible, and "untrusted
content is optional" is exactly the reasoning that let three empty sources sit in
the registry looking like coverage. If a source yields nothing, the honest state
is that it is not a source.

### Check plausibility only at validate time, not at fetch

Keep fetching simple; catch the problem in the separate validation command.

Rejected because it would let an empty document be stored, checksummed, pinned
and recorded as a clean observation, with only a later command dissenting — which
is very nearly the situation we are fixing. The check runs at both, but fetch is
where it has to bite: a document that cannot be used should never become part of
the record.
