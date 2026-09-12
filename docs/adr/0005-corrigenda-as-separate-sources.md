# ADR-0005: Corrigenda are separate sources, patched at composition, invisible in citations

- Status: accepted
- Date: 2026-09-12
- Implementation: **deferred**. This ADR decides the model; the patching is not built.

## Context

The Cyber Resilience Act as we fetch it is the Official Journal text of
20 November 2024. Two corrigenda — CELEX `32024R2847R(01)` and `32024R2847R(04)`
— amend the article text since publication.

A corrigendum is not an amendment in the usual sense. It corrects errors in the
published text: a wrong cross-reference, a mistranslation, a number that should
have been different. The legal fiction is that the corrected text is what the
regulation always said. It is published as its own document, with its own CELEX
number, listing changes against the original.

So the same article now has two textual states, and a citation to it means the
corrected one. Something has to decide where that correction lives.

This matters more here than in a general document system, because the whole
project is built on the claim that a citation can be checked. Citing Article 13
and quoting superseded wording is precisely the failure the citations exist to
prevent — and it fails invisibly, since the citation itself looks right.

## Decision

A corrigendum has three different identities, one per stage:

**At fetch time it is a separate source.** It has its own URL, its own CELEX id,
its own checksum and its own entry in the registry, exactly like any other
document. It is fetched, stored and pinned on its own terms.

**At composition time it is a patch.** Building the corpus applies the
corrigendum's corrections to the segments of the regulation it corrects,
producing corrected segment text. The corrigendum's own segments are not
retrievable as answers in their own right.

**At citation time it is invisible.** A corrected Article 13 is cited as
"Article 13", not "Article 13 as corrected by 32024R2847R(01)". The reader of an
answer wants the law; the provenance of the correction belongs in metadata.

Consequently **a segment id never encodes a version**. `cra-de:article:13` is
the permanent name of that article in that language, whatever corrigenda have
been applied to its text. Which corrections a segment carries is recorded in its
`text_version` field, which lists the CELEX ids applied, in order, and is empty
today.

Until patching is built, segments affected by an unapplied corrigendum will be
**flagged as carrying an unapplied correction**, so the gap is visible in the
data rather than only in this document.

## Rationale

Separating the three roles follows from each stage having a different job.

Fetching is about provenance: where did these bytes come from, who published
them, do they still match what we approved. A corrigendum has a publisher, a URL
and a checksum of its own, so it is a source. Modelling it as anything else would
mean it has no pin and no drift detection, which for a document that changes the
law would be a strange omission.

Composition is about producing the text a reader should see. That text is the
corrected text, so the correction has to be applied somewhere, and this is the
only stage where both documents are in hand.

Citation is about what a human verifies. Someone checking "Article 13" opens the
consolidated regulation and reads Article 13. A citation carrying corrigendum
apparatus would be technically fuller and practically worse.

The stable-id rule is the load-bearing part. A segment id is a permanent name:
it will appear in evaluation fixtures, in cached retrieval results, in
telemetry, in stored answers, and eventually in things outside this repository.
If a corrigendum minted a new id, then every one of those references would
silently point at a superseded segment, and the system would keep answering from
text it had itself marked as outdated. Ids name *what the thing is*; content
digests and `text_version` describe *what it currently says*. Conflating those is
how a citation system starts lying.

`text_version` as a list rather than a flag is deliberate: there are already two
corrigenda, order of application matters, and "which corrections are in this
text" is the question an auditor will ask.

## Consequences

- A segment id can be treated as permanent by everything downstream. Caches,
  fixtures and stored answers stay valid across corrigenda.
- Corrigenda get checksums, pins and drift detection for free, because they are
  ordinary sources.
- The content checksum of the regulation *will* change when patching is turned
  on. That is correct — the text really does change — and it will be a blocking
  drift event on a trusted source, which is exactly the intended behaviour of
  [ADR-0003](0003-drift-policy.md)'s gate. It must be acknowledged in the pin
  file with a note, not waved through.
- Composition becomes a real stage with its own logic and its own failure modes,
  rather than a straight copy from segmentation.
- **Applying a corrigendum is harder than this ADR makes it sound.** Corrigenda
  are written for human readers: "on page 42, in Article 13(2), for 'shall' read
  'should'". Turning that into a reliable text transformation is the actual work,
  and it may not be fully automatable. A plausible outcome is a committed patch
  file, reviewed by a human, with the corrigendum document as its evidence.
- **Until then the corpus is knowingly out of date**, and any answer citing an
  affected article quotes superseded wording. This is recorded in
  `registry/sources.toml`, in the README and here. It is the most serious known
  correctness gap in the project.

## Rejected alternatives

### Treat the corrigendum as a new version of the source

Point the registry entry at the corrected consolidated text and let the existing
drift machinery notice that the document changed.

Rejected because the consolidated text is a different publication with a
different status, and because it discards the correction's own provenance: we
would know the text changed but not which corrigendum changed it, or when, or
what it said before. It also makes the two corrigenda invisible as documents,
even though they are the citable authority for the change. Attractive for its
simplicity, and the simplicity is the problem — it throws away the audit trail
that makes the citations worth having.

### Version-qualified segment ids

Mint `cra-de:article:13@R01` when a corrigendum applies, keeping every textual
state addressable.

Rejected because it makes every downstream reference conditional on a version
that did not exist when the reference was written. An evaluation fixture pinned
to `cra-de:article:13` would break, or worse, keep resolving to the superseded
segment. It also pushes the question "which version did you mean?" onto every
caller, including the generation step, where the answer is always "the current
one". Addressability of historical states is a real requirement for an archive;
this is a retrieval system for current law, and it can get history from
`text_version` plus the stored raw bytes of every fetch, which are kept anyway.

### Ignore corrigenda

Cite the Official Journal text as published and note the limitation.

Rejected as a permanent answer, though it is honestly what is shipped today. The
corrigenda change article text; a compliance question answered from uncorrected
wording can be wrong in a way the citation actively conceals, because the
citation looks correct. Acceptable as a documented gap for one more step;
unacceptable as a design.
