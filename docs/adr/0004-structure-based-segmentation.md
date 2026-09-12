# ADR-0004: Segment along the document's own structure, detected from text markers

- Status: accepted
- Date: 2026-09-12

## Context

Retrieval needs units smaller than a 700 KB document. The choice of unit decides
what a citation can say.

The whole point of this project is that an answer points at something checkable.
"Regulation (EU) 2024/2847, Article 13" is checkable by anyone with the Official
Journal open. "Chunk 47 of the CRA, characters 21,000–22,000" is not, and neither
is a passage that happens to span the end of Article 12 and the start of
Article 13.

The regulation already has units. It is divided into 130 recitals, 71 articles
and 8 annexes, and those divisions are what lawyers, regulators and compliance
officers cite. Nothing we invent will be a better unit than the one the document
declares about itself.

Separately, [ADR-0003](0003-drift-policy.md) left a gate unarmed because a
checksum over raw response bytes was useless: two EUR-Lex responses seconds
apart differ inside an analytics attribute. A checksum over extracted text would
not have that problem, but extraction did not exist yet.

## Decision

We will segment along the legal structure — recital, article, annex — and detect
those boundaries from **text markers**, not from EUR-Lex's HTML classes.

Markers live in a per-language profile: the line `Article 13` in English, the
line `Artikel 13` in German, and likewise for annexes, the recitals introduction
and the closing formula. Adding a language means adding a profile.

Each segment gets a content checksum over its extracted text, and each document
gets a checksum over its segments' ids and digests. That document checksum is
what arms the drift gate from ADR-0003.

Sources with no legal structure (a community FAQ, a forum page) segment at
headings, and a page with no headings becomes one segment.

## Rationale

Text markers over HTML classes is the decision most likely to look wrong at a
glance, because the classes are right there and easier to match. EUR-Lex marks
recitals with `<div class="eli-subdivision" id="rct_1">` and articles with
`<p class="oj-ti-art">`; a five-line parser could use them.

We are not using them because those names belong to one Official Journal
generation and have changed between generations. They are presentation metadata,
maintained for a rendering pipeline, with no promise of stability to anyone. The
string `Article 13` is the regulation's own wording. It is fixed by the document
being what it is, it appears identically in the PDF and the print edition, and it
will outlive several redesigns of the website. When the markup changes, a
class-based parser breaks silently and returns fewer segments; a marker-based one
keeps working.

Two details had to be got right for markers to be usable at all, and both are
worth recording because both were found by running the parser rather than by
reasoning:

*Only block-level elements may break a line.* EUR-Lex renders a footnote
reference as `<a>(<span>1</span>)</a>` inside a paragraph. Splitting text at
every tag turns that into a line reading `(1)` — indistinguishable from a recital
number. Splitting only at block elements keeps the footnote inline, where it
belongs, and leaves `(1)` on its own line only when it really is a recital.

*Regions need ends, not just beginnings.* Articles are bounded below by the
closing formula ("Done at Strasbourg…"), and annexes by the Official Journal
footer. Without those two boundaries, all 38 footnotes land inside Article 71 and
the ISSN line lands inside Annex VIII.

The content checksum falls out of extraction and settles ADR-0003's open
question. Measured on the two EUR-Lex responses whose raw digests differ: both
produce 209 segments and an identical content checksum. That is the evidence that
made arming the gate honest rather than hopeful.

## Consequences

- Citations name what the regulation names. A segment is an article, and its id
  is stable enough to be a permanent identifier ([ADR-0005](0005-corrigenda-as-separate-sources.md)).
- Retrieval never returns a passage that straddles two articles, so an answer
  cannot silently attribute one article's obligation to another.
- **Segment lengths are wildly uneven, and this is the main cost.** In the CRA
  they run from 148 characters (Article 29, one sentence) to 22,000 (Annex VIII).
  Any embedding model has a context limit well below the long end, so retrieval
  will need a second, sub-segment split — and that split has to be built without
  losing the article-level citation, which is more work than if everything were
  uniform from the start. This is deferred to the indexing step and is a real
  debt, not a detail.
- **A parser per source family.** The EUR-Lex profile does not help with a
  Commission guidance PDF or a national implementing act. Every new document
  shape is new code plus new fixtures. With five sources that is cheap; it does
  not scale to fifty, and it is the natural pressure point if the corpus grows.
- Validation becomes possible and worthwhile: contiguous numbering is a strong
  check, and a missing Article 47 is caught rather than silently absent.
- The two untrusted HTML sources segment badly — a GitHub issue list has little
  heading structure, so segments are large and arbitrary. Honest, but weak, and
  it will need attention when untrusted retrieval is actually exercised.
- Adding a language is cheap (a profile) as long as the document is an OJ HTML
  export. The German profile needed nothing but its own words.

## Rejected alternatives

### Fixed-size windows with overlap

The standard approach: 1,000-character chunks, 200 characters of overlap.

Rejected because it destroys the citation, which is the product. A window has no
name a reader can verify, and a window that spans an article boundary produces an
answer attributing one article's requirement to another — the single most
damaging error this system can make, and one that looks perfectly plausible. It
would also make the content checksum useless for drift detection, because
inserting one sentence early in the document reflows every subsequent window.
Fixed-size splitting *within* an over-long segment is still likely, but as a
second pass under the article, not instead of it.

### Parse the Formex XML instead of the HTML

EUR-Lex publishes a structured XML rendition, with articles and recitals as
first-class elements. No marker detection, no boundary heuristics.

Genuinely tempting, and the closest call here. Rejected for two reasons. First,
availability: the XML is not offered for every document we expect to add, and
official guidance is published as HTML and PDF, so an XML-only path would have to
be complemented by an HTML path anyway — and then we maintain two. Second,
Formex is a large schema whose generations differ, so "no heuristics" overstates
it; we would trade marker detection for schema-version handling. Worth revisiting
if the corpus turns out to be overwhelmingly OJ documents, in which case this ADR
should be superseded rather than patched.

### LLM-based semantic chunking

Ask a model where the meaningful boundaries are.

Rejected on determinism, cost and, decisively, trust. Segmentation is the step
that assigns provenance, and this corpus contains material written by anyone
([ADR-0001](0001-two-tier-trust-model.md)). A model that reads untrusted document
text in order to decide how to cut it up is a model taking instructions from
untrusted text at the exact moment the trust boundary is being drawn — an
injection surface placed underneath the mechanism meant to contain injection.
Beyond that, it would be non-reproducible (so no stable content checksum, so no
drift gate), it would cost tokens per document per run, and it would be worse
than a regular expression at a task the document already solves by having
headings.
