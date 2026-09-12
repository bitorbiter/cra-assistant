# ADR-0008: Ranking is tier-blind; the trust boundary is enforced in composition

- Status: accepted
- Date: 2026-09-12

## Context

Evaluating retrieval turned up something nobody had decided: asked "What are the
obligations of manufacturers under this Regulation?", the third-ranked segment
was an untrusted community FAQ heading, above most of the regulation itself.

Retrieval has no idea the trust tiers exist. It ranks on term overlap, and an
untrusted document that happens to use the question's words outranks a trusted
one that does not. That is currently an accident of BM25 rather than a decision,
and an accident in the security-relevant part of a system is worth converting
into a decision either way.

The obvious response is a ranking penalty: multiply untrusted scores by 0.7, or
push untrusted results below trusted ones. It is a few lines, it makes the
result lists look more sensible, and it feels like defence in depth.

## Decision

**Ranking stays tier-blind.** No tier weighting, no tier-ordered results, no
untrusted penalty in `retrieve.py`. A segment's trust tier does not affect
whether it is retrieved or where it ranks.

The trust boundary is enforced entirely in **composition** — in how a segment is
rendered into a prompt ([ADR-0006](0006-walking-skeleton.md)): untrusted content
is delimited, labelled as data, and declared never to be an instruction.

## Rationale

The decisive argument is that a ranking penalty makes the prompt-level defence
**untestable**.

The claim this project exists to demonstrate is that untrusted material can be
used as evidence without being obeyed. Testing that claim requires untrusted
material — including, once the poison fixtures exist, deliberately hostile
material — to actually reach the prompt. If ranking suppresses untrusted content,
then an injection test that passes tells you nothing: you cannot distinguish "the
prompt-level defence held" from "the attack never got near the model". The
defence would be protected from evaluation by the very mechanism meant to
support it, and it would go stale without anyone noticing.

Worse, a penalty is a *probabilistic* barrier presented as a security control.
Downranking does not exclude; it makes an attack need a slightly higher term
overlap. An attacker who writes a document matching a question's vocabulary
closely enough still gets in, and now gets in against a system whose owners
believe ranking protects them. A boundary that holds only when scores fall a
certain way is not a boundary.

Keeping ranking blind also keeps the two questions honest and separately
measurable. "Did the right evidence come back?" is a retrieval question, scored
by the golden set ([ADR-0007](0007-measure-before-tuning.md)). "Was untrusted
evidence treated as authority?" is a composition question, scored by injection
tests. Mixing the tier into ranking would make a retrieval regression and a
trust regression show up in the same number.

Finally: untrusted content ranking well is often correct. The community FAQ
answers questions the regulation genuinely does not settle. Suppressing them by
policy would throw away the material that makes the corpus worth having, to buy
a feeling of safety that the composition layer is supposed to provide properly.

## Consequences

- Untrusted content can and will appear at the top of result lists, and answers
  may be composed largely from it. That is intended, and the answer must make it
  visible — the CLI marks untrusted citations, and the prompt states their
  status. **The cost is now measured rather than hypothetical: see the note
  below.**
- The prompt-level defence is exercised by real traffic rather than shielded
  from it, so injection tests will mean something when they arrive.
- Retrieval metrics measure retrieval only, and are not quietly a trust metric.
- **The whole boundary rests on composition.** If prompt rendering is wrong,
  nothing else catches it. That concentration of risk is deliberate — one place
  to get right, test and audit — but it is a single point of failure and should
  be treated as one.
- A retrieval-only bug (an untrusted segment reaching a caller that forgets to
  render it as untrusted) has no second line of defence. The mitigation is that
  the tier travels on every segment ([ADR-0001](0001-two-tier-trust-model.md)),
  so a caller has to actively ignore it rather than merely fail to look it up.
- If the corpus grows to where untrusted material crowds out the regulation, the
  fix is better retrieval or better composition, not a thumb on the scale.

## Rejected alternatives

### Downrank untrusted content

Multiply untrusted scores by a constant below one, or apply a rank penalty.

Rejected primarily because it makes the prompt-level defence untestable, as
above: a passing injection test would no longer distinguish a working defence
from an attack that never arrived. Secondarily because the constant would be
unjustifiable — 0.7 and 0.5 are equally arbitrary, and
[ADR-0007](0007-measure-before-tuning.md) has just committed to not inventing
retrieval constants without measurement. And because it degrades answers for the
questions where community interpretation is the only available material.

### Return trusted results first, untrusted only to fill the remainder

A hard ordering rather than a weighting.

Rejected for the same testability reason, more severely: with any reasonable `k`
the untrusted tier would almost never be reached on a corpus containing 418
trusted segments, so untrusted content would effectively be unreachable and the
poison fixtures would never be retrieved at all. It also silently answers a
question nobody asked — that a weak trusted match beats a strong untrusted one —
which is often wrong.

### Let the caller choose a tier filter

Expose `retrieve(query, k, tiers=...)` and let each call site decide.

Rejected as an interface, though not forever. It moves a security-relevant
decision to every call site, which is the pattern
[ADR-0001](0001-two-tier-trust-model.md) rejected for tier lookup. If a genuine
need appears — a "regulation only" mode for a user who wants exactly that — it
should be an explicit product feature with its own name, not an optional
parameter that call sites can forget.


## Notes

### What tier-blindness actually costs, measured

Repairing the untrusted tier ([ADR-0009](0009-untrusted-content-from-apis.md))
grew the corpus from 488 segments to 1,801 — 418 trusted, 1,383 untrusted — and
retrieval scores against the same golden items went **down**:

| slice | before repair | after repair |
| --- | ---: | ---: |
| overall MRR@10 | 0.338 | 0.230 |
| statute MRR@10 | 0.455 | 0.322 |
| practitioner MRR@10 | 0.185 | **0.033** |
| practitioner R@5 | 0.23 | **0.00** |

The mechanism is visible in a single query. Asked "What are the obligations of
manufacturers under this Regulation?", the top four results are now GitHub
issues and community FAQ answers, with the first trusted segment fifth.

This is not an argument against tier-blind ranking, and it is not an argument
that the repair was wrong — the earlier numbers were flattered by an untrusted
tier that contained nothing to compete with. It is the honest price of the
decision, and it is concentrated exactly where the corpus was already weakest:
community discussion is *written in practitioner vocabulary*, because it is
practitioners writing it, so it outcompetes the regulation precisely on the
questions the regulation was already hard to retrieve for.

Two things follow. First, the answer is better retrieval, not a thumb on the
scale — a question about what the regulation *requires* should find the
regulation because the retriever understands the question, not because the
competition was excluded by policy. Second, this makes the case for measuring
generation separately: an answer drawn from four community sources and one
article may still be a good answer, provided it says which is which, and
retrieval metrics alone cannot tell us.
