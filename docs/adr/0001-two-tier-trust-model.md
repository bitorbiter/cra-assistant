# ADR-0001: Split the corpus into a trusted and an untrusted tier

- Status: accepted
- Date: 2026-09-12

## Context

The system answers questions about the Cyber Resilience Act by retrieving text
and citing it. Two kinds of text are worth retrieving, and they differ in who
can write them.

The regulation itself and official guidance are curated. Their wording is fixed
by a publisher we can name, and changing them requires going through that
publisher. Community interpretation — forum threads, GitHub discussions,
community FAQs — is where the practical questions are actually argued out
("does a hobbyist maintainer fall under this?"), and it is writable by anyone
with an account.

Retrieved text ends up inside a prompt. A language model does not natively
distinguish "text I was asked to reason about" from "text telling me what to
do", so any retrieved passage is a potential instruction. If an attacker can
edit a source that the retriever might select, they can attempt to redirect the
system: change its answer, make it cite something it did not read, or make it
call a tool. This is indirect prompt injection, and for this corpus it is not a
hypothetical — the useful community sources are precisely the ones anyone can
write to.

We therefore need the answer to "who could have written this text?" to be
present at the point where the prompt is assembled, and we need it to be
impossible to forget.

## Decision

We will partition every source into exactly one of two trust tiers, `trusted`
and `untrusted`, declared in the registry. The tier is a property of the source.
It is materialised onto every document and every segment derived from that
source at ingest time, and no component downstream of ingest may look a tier up
from the registry.

`trusted` content may carry instruction authority in prompts. `untrusted`
content must be encapsulated: quoted as evidence, never treated as
instructions, never able to trigger a tool call.

A source that would contain both kinds of material is modelled as two sources.

## Rationale

The tier answers one question — *who can write this?* — and that question has a
crisp, checkable answer for every source we would want to add. It does not
require judging whether a document is correct, which is not checkable and would
rot.

Materialising the tier onto every segment is the part that makes the property
hold rather than merely being documented. If a segment carries its own tier,
then prompt assembly cannot construct a passage whose provenance is unknown: the
field is either there or the segment failed to build. A lookup at query time
would be a second source of truth that could be skipped, cached wrongly, or
silently default to permissive when a source id is missing. The failure mode of
a missing tier must be a crash, not a guess.

Two tiers rather than a scale: an ordering invites "how trusted is trusted
enough for this prompt position?", which is a judgement call that would be made
differently in each call site. A boolean-shaped distinction can be enforced by
one rule at one place.

Naming matters here and we picked the names badly enough to be worth recording:
`trusted` and `untrusted` describe **write access, not quality**. An untrusted
source may be more accurate and better argued than the regulation's own drafting
is clear. We kept the names because they are the terms the injection literature
uses, and stated the distinction explicitly in the README, the registry file and
the model docstring.

## Consequences

- Prompt assembly has exactly one rule to enforce, and it can be tested: no
  segment with `tier = untrusted` may reach a position where instructions are
  honoured.
- Provenance travels with the text, so a citation can state not just where a
  claim came from but what kind of source it was. Answers can say "the
  regulation says X, and a community FAQ argues it means Y" honestly.
- Tier becomes a required field on documents and segments, so it must be
  threaded through ingest, storage and retrieval. Every one of those layers
  gains a column and a test.
- Duplication is real: the tier is stored once per segment, not once per
  source. That is the price of not looking it up, and it is small.
- Re-tiering a source means re-ingesting everything derived from it. This is
  deliberate — it makes a trust change a visible, deliberate operation rather
  than an edit to one row.
- Two tiers cannot express "official guidance is authoritative but less so than
  the regulation". If that distinction is ever needed, it is a separate
  attribute, not a third tier.
- The classification is only as good as the registry. A source marked `trusted`
  that is in fact editable by anyone defeats the whole design, so adding a
  trusted source is a security-relevant change and is reviewed as one.

## Rejected alternatives

### A single tier with sanitisation at ingest

Ingest every source into one pool, and strip injection attempts as the text is
parsed — remove imperative sentences, filter phrases like "ignore previous
instructions", escape delimiters.

Rejected because it is a blocklist against an open-ended set of phrasings, in
a corpus that is *about* security requirements and therefore legitimately full
of imperative language ("manufacturers shall ensure..."). The filter would have
to distinguish a regulation's obligations from an attacker's, in prose, with no
provenance signal to help it. It also fails in the wrong direction: an attack it
misses becomes indistinguishable from regulation text, because the pool has no
concept of where a passage came from. Sanitisation may still be added later as a
second layer, but it cannot be the first one.

### Tier as a per-document property rather than per-source

Let individual documents be classified, so that a single source can contribute
both trusted and untrusted material — for example, an official page that embeds
user comments.

Rejected because it moves the trust decision from review time to ingest time.
Per-source, the tier is a line in a committed file that a reviewer sees in a
diff. Per-document, it is the output of a classifier or a parser heuristic, and
its correctness depends on that code being right about every page it has not yet
seen. When a parser guesses wrong, it guesses in favour of the attacker. Modelling
a mixed page as two sources keeps the decision human and reviewable, at the cost
of some registry verbosity.

### Excluding untrusted content entirely

Index only the regulation and official guidance.

Rejected because it removes most of the value. The regulation states obligations;
it does not say how a five-person team should interpret "due diligence", and that
is what people ask. It would also be a dishonest portfolio project: it makes the
injection problem disappear by declining to have the problem, which is not a
result. The interesting claim is that untrusted material can be used as evidence
without being obeyed, and that claim can only be demonstrated by using it.
