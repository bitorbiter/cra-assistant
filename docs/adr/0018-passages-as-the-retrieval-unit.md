# ADR-0018: Retrieve passages, cite articles

- Status: accepted — implemented 2026-09-19; see [Outcome](#outcome--2026-09-19)
- Date: 2026-09-19

## Context

An external reviewer reproduced the ranks of four central questions on the
current corpus. They are the reason this ADR exists:

| question | expected source | rank |
| --- | --- | ---: |
| obligations of manufacturers | `cra-en:article:13` | **223** |
| maximum penalties | `cra-en:article:64` | 74 |
| scope | `cra-en:article:2` | 20 |
| definition of manufacturer (DE) | `cra-de:article:3` | 21 |

The first is a question whose wording *is the article's title*. BM25 penalises
length, and Article 13 is 15,386 characters, so the article that answers the
question loses to short recitals that mention a word from it.

The same length is a second, independent failure. Prompt assembly clips a
segment at 4,000 characters, so **25 trusted segments are indexed in full and
delivered in part**. Annex I loses 1,474 characters, which is Part II points (5)
to (8); Annex VIII is 21,876 characters and reaches the model as its first fifth.
Retrieval can rank a segment first for text the model will never see.

On the verified answerable slice, R@5 is 0.38 and MRR@10 is 0.335
([baseline-2026-09-19](../eval/baseline-2026-09-19.md)).

## Decision

**The retrieval unit becomes the passage. The citation unit stays the article.**

A `Passage` is a numbered paragraph of an article, a point of an annex, or a
whole recital, carrying a reference to the `Segment` it came from. Passages are
built by the document's own line structure and markers, as ADR-0004 requires of
segments — never by fixed-size windows, which that ADR rejected for destroying
the citation.

- **Retrieval** scores passages, so a long article competes paragraph by
  paragraph and its length stops being a penalty.
- **The prompt** delivers the matched passages, each labelled with its parent
  segment's citation and id. The model sees the paragraph that matched, not the
  first 4,000 characters of the article it belongs to.
- **Citations** stay article-level: the model cites `cra-en:article:13`, the id
  keeps its meaning as a permanent name (ADR-0005), and every committed gold
  label, pin and report stays valid.
- **Span validation** continues to check the quoted text against what was
  delivered, which is now the passage.

Segment ids are unchanged, so nothing in `eval/golden.toml`, `registry/pins.toml`
or the published reports needs rewriting.

## Pre-registered evaluation

Written before the code, because the tempting failure here is to tune on
everything and report the number that came out.

**Split.** Of the 23 verified answerable golden items, hold-out is every item
whose `sha256(id)` modulo 3 is 0, tuning is the rest. Fixed now:

- **Hold-out (10):** `authorised-representative-en`, `def-manufacturer-de`,
  `oss-steward-practitioner-de`, `penalties-practitioner-de`,
  `report-deadline-practitioner-de`, `sbom-practitioner-en`,
  `security-attestation-foss-en`, `single-reporting-platform-en`,
  `technical-documentation-en`, `user-information-de`.
- **Tuning (13):** everything else, including the three articles named in the
  table above.

The hold-out slice is scored once, at the end, after the splitting rule is
final. If it is scored and then the rule changes, it stops being a hold-out and
this ADR says so rather than pretending otherwise.

## Prediction

| measure, verified answerable items | now | predicted |
| --- | ---: | ---: |
| MRR@10, tuning slice | 0.335 (all 23) | 0.55 or better |
| R@5, tuning slice | 0.38 (all 23) | 0.60 or better |
| MRR@10, hold-out slice | — | within 0.10 of the tuning slice |
| `cra-en:article:13` rank | 223 | top 5 |
| `cra-en:article:64` rank | 74 | top 10 |
| calls delivering a clipped unit | 45 of 312 in the last attack run | near zero |

Three things I expect to get worse or stay flat, written down now:

- **Questions whose answer is a whole article** — "what are the obligations of
  manufacturers" — are answered by one paragraph of twenty-five. Recall counts
  the parent, so the metric will look fine while the model sees less of the
  article than before. This is the risk the metric cannot see.
- **Unanswerable items** already retrieve a full window of irrelevant material
  and will continue to; passages will not change abstention behaviour.
- **Prompt cost** should fall, because a passage is smaller than a clipped
  article, but k passages from one article may crowd out other sources.

### What would falsify this

- The hold-out slice does not improve, or moves more than 0.10 away from the
  tuning slice: the gain was fitted to the questions I looked at.
- Any item that currently retrieves its label loses it.
- Mean delivered characters per question rise.
- The unanswerable items start returning fewer segments, which would mean
  passages are suppressing rather than reordering.

## Outcome — 2026-09-19

Status: **accepted, with two falsification conditions fired and one open defect.**

Measured with the shipped evaluator, `k = 10` distinct segments on both sides,
same corpus (2,062 segments) and same items. "Before" is the whole-segment
retriever at `HEAD` prior to this change, re-run rather than quoted, because the
evaluator changed at the same time (see *A measurement defect found on the way*).

| verified answerable, n=23 | before | after |
| --- | ---: | ---: |
| R@1 | 0.13 | 0.32 |
| R@5 | 0.38 | **0.63** |
| R@10 | 0.63 | **0.72** |
| MRR@10 | 0.335 | **0.610** |

Against the prediction, on the tuning slice, which is what the prediction was
written against:

| prediction | predicted | measured | |
| --- | ---: | ---: | --- |
| MRR@10, tuning slice | 0.55 or better | 0.556 | met |
| R@5, tuning slice | 0.60 or better | 0.69 | met |
| MRR@10, hold-out within 0.10 of tuning | ±0.10 | +0.123 | **fired** |
| `cra-en:article:13` rank | top 5 | 4 | met |
| `cra-en:article:64` rank | top 10 | 5 | met |
| calls delivering a clipped unit | near zero | 0 of 328 | met |

The hold-out slice (n=10) was scored once, after the rule was final: R@5 0.55,
MRR@10 0.679, against 0.43 and 0.355 before. It improved, and by more than the
tuning slice did.

### The falsification conditions

Four were registered. Two did not fire:

- **Mean delivered characters per question rose** — no. They fell, 16,648 to
  11,190 at `k=8`, and no delivered unit is clipped any more (0 of 328, against
  25 trusted segments that were indexed whole and delivered in part).
- **Unanswerable items return fewer segments** — no. Ten before, ten after.

Two fired, and neither is worked around:

**1. The hold-out moved 0.123 away from the tuning slice.** The condition is
written symmetrically — "moves more than 0.10 away" — and it is breached by
0.023. The direction is the opposite of the one the condition exists to catch:
the hold-out scored *higher* than the slice the rule was tuned on (0.679 against
0.556), so this is not the gain evaporating off the questions I looked at. It is
recorded as fired because the condition says what it says, and reading a
symmetric threshold as one-sided after seeing which way it broke is exactly the
move pre-registration is meant to prevent.

**2. Four items lose a gold label they used to retrieve.** The condition was
"any item that currently retrieves its label loses it".

| item | lost | still retrieves |
| --- | --- | --- |
| `application-date-en` | `cra-en:article:71` | nothing |
| `def-manufacturer-de` | `cra-de:article:21` | `cra-de:article:3` (rank 2) |
| `security-attestation-foss-en` | `cra-en:recital:21` | `cra-en:article:25` |
| `early-application-dates-en` | `cra-en:article:71` | `cra-en:recital:126` |

`application-date-en` is the serious one: "From what date does this Regulation
apply?" no longer retrieves Article 71 at all, which fell from inside the top ten
to rank 14. Article 71 is 446 characters and splits into exactly one passage, so
nothing about it changed. What changed is everything around it: Articles 2, 69
and 4 now compete paragraph by paragraph and displace it. This is the ADR's own
predicted trade-off running in the other direction — the length penalty that was
burying Article 13 was also what floated Article 71.

It is worth naming that Article 71 is the article the `auth-notice` attack
targets, the one residual breach the security thread ended on.

### An open defect: the tier-collapse control regressed

Not a registered condition, and it should have been. The five `untrusted_only`
items exist to show the system *does* use community sources when only they
answer the question — the control the tier-collapse measurement rests on.

| item | gold ranks before | after |
| --- | --- | --- |
| `ut-steward-reporting-clock` | 2, 3, 5 | absent |
| `ut-steward-eol-versions` | 1, 2, 3, 4 | 1, 7 |
| `ut-unincorporated-group-steward` | 5, 3, 2, 7 | 7 |
| `ut-one-person-company-steward` | 3, 5 | absent |
| `ut-sponsorware-manufacturer` | 1 | 1 |

The cause is `TOP_PASSAGES_PER_SEGMENT = 2` and the decision to score a segment
as the **sum** of its two best passages. Untrusted comments have a median of one
passage; trusted articles split into many. A segment with two matching passages
can therefore score up to twice a segment with one, and that is a length bonus
reintroduced by the back door — the mirror image of the length penalty this ADR
set out to remove.

Measured on the tuning slice and the controls only, never the hold-out:

| aggregation | tuning R@5 | tuning MRR@10 | controls retrieving a label |
| --- | ---: | ---: | ---: |
| sum of the best two (shipped) | 0.69 | 0.556 | 3 of 5 |
| the best one only | 0.54 | 0.451 | 5 of 5 |

That is a real trade-off, not a bug with a right answer, and it is **not** taken
here. The hold-out has been scored; changing the rule now on evidence from the
controls would be fitting the rule to the items I just looked at, which is the
one thing this ADR's structure exists to prevent. It belongs in a new ADR with
its own prediction and its own hold-out.

### A measurement defect found on the way

The evaluator's `k` stopped meaning what it says. Ranks are computed over
distinct segments, but the window was `k` *passages*, and with up to two passages
per segment a ten-passage window held 5.3 distinct segments on average. R@10
could not reach ten, R@10 and R@5 came out identical, and the comparison against
every earlier baseline was silently unfair to the new retriever.

`evaluate.run` now asks for `k * TOP_PASSAGES_PER_SEGMENT` passages for ranking
and keeps the delivered window at `k`, which is what `ask` sends and what prompt
cost must be measured on. This was found and fixed *after* the hold-out had been
scored, so the hold-out was scored twice: R@5 0.55 and MRR@10 0.650 under the
narrow window, R@5 0.55 and MRR@10 0.679 under the corrected one. Widening a
ranking window can only find a gold label earlier or not at all, so the
correction cannot lower a score, and every conclusion above holds under both
readings. Recorded rather than quietly re-reported.

### Consequences

- The retrieval unit and the citation unit are now different things. Anything
  reading `Retriever.retrieve` gets passages, and `k` counts passages there;
  `evaluate.run` counts segments. The two are not interchangeable.
- `Passage.full_text` exists so citation checking can still validate a span
  against the parent segment when the model cites something not delivered.
- One passage in the corpus still exceeds 4,000 characters: a base64 data URI on
  a single line in `ec-faq-mirror`. It has no markers to split on, and clipping
  it loses nothing a reader wants.
- The three regressions predicted above stand. The first — a question answered by
  a whole article now sees one paragraph of twenty-five — is still the risk the
  metric cannot see, and recall cannot detect it because recall counts the parent.

## Rejected alternatives

**Fixed-size windows with overlap.** Rejected in ADR-0004 and still rejected: a
window has no name a reader can verify, and one that spans an article boundary
attributes one article's obligation to another.

**Passages as first-class segments.** Giving each paragraph its own id would let
retrieval and citation share a unit, and would invalidate every gold label, pin
and published report, while making citations point at something the regulation
does not name. A segment id is a permanent name (ADR-0005).

**A larger retrieval window.** Priced in the baseline: depth 20 costs 2.3× the
tokens of depth 8 for +0.10 recall@10, and does nothing about the 4,000-character
cutoff.

**Embeddings and pgvector.** Deferred deliberately. The in-memory index is
adequate at 2,062 segments, and a vector index would paper over the length
problem rather than fix it — the same long-segment text would be embedded whole.
