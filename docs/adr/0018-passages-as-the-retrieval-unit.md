# ADR-0018: Retrieve passages, cite articles

- Status: proposed — prediction recorded, implementation not yet written
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
