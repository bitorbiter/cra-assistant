# ADR-0007: Measure retrieval before tuning it; keep the golden set as committed data

- Status: accepted
- Date: 2026-09-12

## Context

[ADR-0006](0006-walking-skeleton.md) built a deliberately dumb retriever and
produced one concrete complaint: asked for the definition of *Hersteller*, BM25
ranked Article 3 sixteenth. Two causes were visible — length normalisation
punishing a long definitions article, and German stopwords diluting the query —
and both have obvious fixes.

That is exactly the moment to be careful. A stopword list would take ten minutes
and would certainly change the number for that one question. Whether it improves
retrieval overall is a different claim, and nothing in the repository could
settle it. The same is true of every parameter that pgvector would introduce:
which embedding model for a bilingual corpus, what to embed given segments run
from 148 to 22,000 characters, how to weight sparse against dense.

Tuning without measurement produces a system that is more complicated, feels
better, and cannot be shown to be better. For a portfolio project whose subject
is verifiability, that would be a poor thing to build.

## Decision

Measure first, and change no retrieval code in the step that establishes the
measurement.

- **A golden set as committed data**, `eval/golden.toml`, validated through
  pydantic exactly like `registry/sources.toml` ([ADR-0002](0002-registry-as-committed-data.md)).
- **Gold labels reference segment ids**, which never encode a version
  ([ADR-0005](0005-corrigenda-as-separate-sources.md)). That is what makes the
  set durable: a corrigendum changes what Article 13 says, not that Article 13
  is the answer.
- **Retrieval and generation are measured separately.** This ADR covers
  retrieval only: recall@1, recall@5, recall@10 and MRR@10.
- **Reported overall and sliced** by vocabulary and by answer type. The slices
  are the point; the overall number is nearly meaningless on its own.
- **Drafted labels are not authority.** Items carry `verified: false` until a
  human has checked them, and `eval` refuses to score unverified items unless
  `--include-unverified` is passed, which stamps the report as provisional.
- **Baselines are committed, append-only, never edited**, one file per
  measurement under `docs/eval/`.
- **The offline half runs on every push and does not block.**

## Rationale

The golden set is committed data for the reason the registry is: a gold label
*defines what correct means*, so changing one should be as visible in a pull
request as changing a trusted source. A label quietly weakened to make a metric
improve is the single easiest way to fake progress on this kind of project, and
the mitigation is that the weakening appears in a diff with a name attached.

Splitting retrieval from generation matters because they fail differently and
one masks the other. If an answer is wrong, it is either because the right
segment never arrived or because the model misused a segment that did. Measuring
end to end conflates the two and rewards fixing whichever is easier. Retrieval
also measures offline, deterministically and for free — which is what lets it
run per-push, while anything involving the model cannot.

Slicing by vocabulary is the decision that changes what gets built. A retrieval
system that scores well only on questions phrased in the regulation's own words
is a search box for people who already know the answer. The measurement bore
this out immediately and starkly, on the drafted set:

| slice | R@1 | R@10 | MRR@10 |
| --- | ---: | ---: | ---: |
| statute vocabulary | 0.35 | 0.71 | 0.455 |
| practitioner vocabulary | 0.08 | 0.69 | 0.185 |

A 2.5× gap in MRR between the same corpus asked two ways. Without the slice, the
overall MRR of 0.338 would have looked like one uniform mediocrity to be fixed
with one uniform change.

Refusing to score unverified items is a guard against a specific, likely
failure: labels drafted by a model, scored by the same model's code, quoted
later as a measurement. The refusal is annoying by design. Nobody should be able
to produce an authoritative-looking table without a person having read the
questions.

Reporting without blocking follows from having no baseline. A threshold picked
today would be picked from the number we happen to have got, which makes it a
description of the present rather than a standard. Thresholds arrive once there
is a series to argue from.

## Consequences

- Every change to retrieval can now be argued about with numbers, and every
  argument has to survive the practitioner slice, not just the overall one.
- The gap between statute and practitioner phrasing is quantified rather than
  suspected, and is the strongest available argument for what to build next.
- CI gains an offline job on every push, at the cost of fetching the corpus each
  time (the corpus is not committed). No API key is involved.
- **The golden set is drafted, not verified, so today's numbers are provisional
  and the report says so.** They describe the shape of the problem; they are not
  yet a baseline anybody should defend.
- Verifying 40 items by hand is real work, and it is the bottleneck before any
  of this becomes authoritative.
- The `untrusted_only` slice is weak for a corpus reason, not an evaluation one:
  the registered untrusted sources contain almost no substantive interpretation.
- Baselines accumulate as files. Five of them will be a useful series; fifty
  will be clutter needing a summary.

## Rejected alternatives

### Fix the obvious retrieval problems first, measure afterwards

Add a German and English stopword list, lower `b`, then build the golden set
against the improved retriever.

Rejected because the baseline would then describe a system that was already
tuned by intuition, and the tuning could never be evaluated — there would be no
"before". It also gets the epistemics backwards: the stopword list is a
hypothesis, and this step is how hypotheses get tested. Worth noting that the
measurement did not merely confirm the known problem, it found a worse one that
no amount of staring at the code would have surfaced (see below), which is the
argument for this ordering in a sentence.

### Generate the golden set from the corpus automatically

Have a model read each article and write a question it answers, yielding
hundreds of items for nothing.

Rejected because it measures the wrong thing. A question generated *from* a
segment is phrased in that segment's vocabulary, so the set would consist almost
entirely of statute-vocabulary items — precisely the slice that already scores
well — and the practitioner gap would have been invisible. It would also make
the labels circular: the same family of model that generates the question
supplies the ground truth for whether it was answered. Drafting candidates
automatically and having a human rewrite and verify them, which is what this
step does, keeps the volume benefit without the circularity.

### Measure end to end only, on final answers

Score the system on whether the answer is right, and skip retrieval metrics.

Rejected because it cannot localise a failure, costs money per run so cannot run
per-push, and is non-deterministic. It is also the more expensive measurement to
build well, and building it first would have delayed learning anything. It is
the natural next step, not a replacement for this one.

### Make the evaluation a blocking CI gate immediately

Fail the build when recall drops.

Rejected because any threshold available today is arbitrary. Worse, a gate set
from provisional numbers over unverified labels would give the labels an
authority they have not earned — the metric would start defending a draft.

## Notes

Two findings from the first run, recorded here because they are the evidence for
the ordering this ADR argues for.

**Article 13 ranks 47th for a question that is verbatim its own title.** Asked
"What are the obligations of manufacturers under this Regulation?", BM25 puts
Article 13 — titled *Obligations of manufacturers* — in 47th place, because its
15,386 characters attract the full weight of length normalisation while a
450-character article mentioning manufacturers once wins. This is a worse and
more specific failure than the Article 3 case that motivated the step, and it
was not predicted.

**Retrieval hands the model ten plausible distractors for every unanswerable
question**, including "How do I keep sourdough starter alive over winter?" Zero
of ten unanswerable items returned nothing, because with no stopword list every
question matches something. Abstention therefore rests entirely on the model. A
retrieval score floor is the obvious response and is exactly the kind of
threshold that should be set by measurement rather than by taste.
