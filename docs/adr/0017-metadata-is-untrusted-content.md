# ADR-0017: Untrusted metadata is untrusted content — split the header, do not filter it

- Status: accepted; amended 2026-09-13 — the residual channel is closed
- Date: 2026-09-13

## Context

An external code review found that the Markdown-tree parser built each segment's
`citation` from the file path and the section heading, and that
`prompt.render_segment` printed that citation on the provenance header,
**outside** `<untrusted-content>`. Both are chosen by whoever can write to the
community repository. So:

- **Boundary bypass.** A heading reading *"Ignore all previous instructions…"*
  reached the model on a line the system prompt presents as harness metadata,
  not quoted data.
- **Denial of service.** A heading or path containing `</untrusted-content>` added
  a closing delimiter outside any wrapper. The delimiter invariant
  (ADR-0011) correctly refused to send that prompt — by raising
  `DelimiterInvariantError`, for **every** question that retrieved the document.
  One edited heading denied service to every question it ranked for.

Neither was found by the attack set. Every fixture put its payload in the
document body; seventeen fixtures, three mitigations and hundreds of runs never
exercised the header, because the attack set defines what gets run.

## Decision

**Split by where a field comes from, not by what it contains.**

- Outside the wrapper, an untrusted item's header carries only fields whose
  shape the harness guarantees: `id` (pattern-validated on `Segment`:
  `[a-z0-9-]+:section:[A-Za-z0-9.-]+`), `tier` (an enum) and `language` (a
  two-letter code).
- The human-readable citation — path, heading, title — is rendered **inside** the
  wrapper, whitespace-collapsed and delimiter-neutralised like any other
  untrusted text.
- Trusted items keep their citation on the header: it is built from the
  registry's `short_title` and the regulation's own article numbers.

The id stays outside because citation enforcement depends on the model echoing it,
and it is already the stable, source-derived name (`issue-137`, file path plus
heading slug; ADR-0009).

Ingest is unchanged. `Segment.citation` still holds the raw text, segment ids
and content checksums do not move, and nothing needs re-pinning. The fix is at
the one place the boundary is enforced.

## Consequences

- A hostile heading or file name can no longer place text outside the wrapper,
  and can no longer crash assembly. Both are regression tests, driven through the
  real Markdown-tree ingest path rather than a hand-built segment.
- **The prompt changed.** Every untrusted item now has its citation line one
  line lower, inside the box. Attack measurements before this change are not
  comparable with measurements after it.
- **Residual channel, stated rather than hidden.** The id's slug is derived from
  the path and heading, so `faq:section:ignore-previous-instructions-ignore-all-…`
  still carries an attacker's words outside the wrapper — as hyphenated
  lowercase ASCII, at most 60 characters per slug, unable to contain a delimiter
  or punctuation. That is a much smaller surface than free text, and it is not
  zero. Numeric GitHub ids (`issue-137`) carry nothing.

## Rejected alternatives

### Filter the heading before it reaches the header

Strip delimiters, markup and imperative phrasing from headings, and keep them on
the header. Rejected: it is the approach this project has already watched fail
twice. `neutralise_delimiters` defeats the exact tag and nothing else, and a filter
for "instruction-like" text is a classifier over attacker-chosen input. Placement
is decided by provenance, which the attacker does not control. Content is
decided by the attacker.

### Drop headings from the prompt altogether

Safe, and it throws away the most useful words in a FAQ answer: the question it
answers. The heading is already inside the body as its first line. Removing it
would make the untrusted tier worse as evidence to fix a problem of placement.

### Replace slugs with opaque hashes

Would close the residual channel. Rejected for now for the reason ADR-0009 gives:
an id nobody can resolve by reading it is not a citation a reader can check.
Revisit if a slug-borne payload is ever measured doing anything.

## Amendment, 2026-09-13: the residual is closed

"Much smaller than free text, and not zero" was the wrong standard for text
outside the boundary. **Attacker-controlled text outside the wrapper must be zero.**

- Markdown and Markdown-tree sections are now named by a 12-hex-character digest
  of their location — file path and heading, plus an ordinal for a repeated
  heading. GitHub issues and comments keep GitHub's own numbers, and generic HTML
  keeps positions.
- A digest of the *location*, not of the body. A body digest would rename a
  section on every typo fix and rot every label pointing at it (ADR-0009); a
  location digest moves only when the file or heading moves, exactly as the slug
  did.
- `models.OPAQUE_UNTRUSTED_NUMBER` is the only shape an untrusted id's last
  component may take. Ingest refuses anything else, and rendering asserts it again
  and raises `UntrustedIdentifierError`, never repairs.
- The test: a document whose heading and file name are pure injection renders
  outside the wrapper **byte for byte the same** as a benign document, apart from
  the digest. That is what "zero" means, stated as a comparison, not as a list of
  forbidden words.

The rejected alternative below, "replace slugs with opaque hashes", is therefore
adopted. Its objection still holds: `orcwg-faq:section:3f9a…` cannot be read. The
readable name lives in the citation line, inside the wrapper, and on the CLI's
answer output.

Consequences: segment ids for every Markdown-derived untrusted source changed, and
with them the content checksums of those sources (ids are part of the digest).
The five `untrusted_only` gold labels named heading slugs; they were unverified,
due for re-authoring, and are deleted, so ADR-0016's tier-collapse control has no
items until they are re-authored.
