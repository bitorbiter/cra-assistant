# Retrieval baseline — 2026-09-12

> **Corpus note, added 2026-09-13:** measured on an 800-comment truncation of `orcwg-cra-hub-issues` — the page cap cut the collection silently, and fetched to completion it has 1,061 comments. Kept as recorded, superseded as a measurement of the full corpus ([ADR-0009](../adr/0009-untrusted-content-from-apis.md)).

- Items scored: **25** with gold labels, **10** unanswerable, 35 total
- Corpus: 1801 segments
- Retrieval depth: k=10
- Retriever: in-memory BM25, no stemming, no stopword list (ADR-0006)

> **These numbers are provisional.** 35 of 35 items are `verified = false`: the gold labels were drafted and have not been checked by hand. Treat this as a shape, not a measurement.

## Overall

| Slice | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| all labelled items | 25 | 0.16 | 0.28 | 0.48 | 0.230 |

## By vocabulary

| Slice | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| statute | 17 | 0.24 | 0.41 | 0.59 | 0.322 |
| practitioner | 8 | 0.00 | 0.00 | 0.25 | 0.033 |

*The slice that matters. Statute vocabulary uses the regulation's own words; practitioner vocabulary is how somebody with the problem actually asks.*

## By answer type

| Slice | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| answerable | 25 | 0.16 | 0.28 | 0.48 | 0.230 |
| unanswerable | 0 | — | — | — | — |
| untrusted_only | 0 | — | — | — | — |

## Unanswerable items

Recall is undefined with no gold label, so these are measured differently. Retrieval cannot abstain; the question is how much plausible material it hands the model anyway.

| n | returned nothing | mean segments returned |
|---:|---:|---:|
| 10 | 0 (0%) | 10.0 |

## Per item

| id | type | vocab | lang | first hit | R@10 | note |
|---|---|---|---|---:|---:|---|
| `def-manufacturer-de` | answerable | statute | de | not found | 0.00 | *(unverified)* |
| `manufacturer-obligations-en` | answerable | statute | en | not found | 0.00 | *(unverified)* |
| `report-exploited-vulnerability-en` | answerable | statute | en | 7 | 1.00 | *(unverified)* |
| `report-deadline-practitioner-de` | answerable | practitioner | de | 6 | 1.00 | *(unverified)* |
| `single-reporting-platform-en` | answerable | practitioner | en | not found | 0.00 | *(unverified)* |
| `authorised-representative-en` | answerable | statute | en | 4 | 1.00 | *(unverified)* |
| `importer-obligations-de` | answerable | statute | de | 2 | 1.00 | *(unverified)* |
| `distributor-check-practitioner-en` | answerable | practitioner | en | not found | 0.00 | *(unverified)* |
| `oss-steward-duties-en` | answerable | statute | en | 6 | 1.00 | *(unverified)* |
| `oss-steward-practitioner-de` | answerable | practitioner | de | 10 | 1.00 | *(unverified)* |
| `security-attestation-foss-en` | answerable | statute | en | 1 | 1.00 | *(unverified)* |
| `declaration-of-conformity-content-en` | answerable | statute | en | 1 | 1.00 | *(unverified)* |
| `ce-marking-de` | answerable | statute | de | 1 | 1.00 | *(unverified)* |
| `technical-documentation-en` | answerable | statute | en | not found | 0.00 | *(unverified)* |
| `conformity-assessment-choice-practitioner-en` | answerable | practitioner | en | not found | 0.00 | *(unverified)* |
| `penalties-maximum-en` | answerable | statute | en | not found | 0.00 | *(unverified)* |
| `penalties-practitioner-de` | answerable | practitioner | de | not found | 0.00 | *(unverified)* |
| `application-date-en` | answerable | statute | en | 4 | 1.00 | *(unverified)* |
| `deadline-practitioner-de` | answerable | practitioner | de | not found | 0.00 | *(unverified)* |
| `scope-en` | answerable | statute | en | not found | 0.00 | *(unverified)* |
| `important-products-en` | answerable | statute | en | not found | 0.00 | *(unverified)* |
| `critical-products-de` | answerable | statute | de | 6 | 1.00 | *(unverified)* |
| `essential-requirements-en` | answerable | statute | en | not found | 0.00 | *(unverified)* |
| `user-information-de` | answerable | statute | de | 1 | 1.00 | *(unverified)* |
| `sbom-practitioner-en` | answerable | practitioner | en | not found | 0.00 | *(unverified)* |
| `un-gdpr-dpo-en` | unanswerable | practitioner | en | 10 returned | — | *(unverified)* |
| `un-gdpr-breach-deadline-de` | unanswerable | statute | de | 10 returned | — | *(unverified)* |
| `un-nis2-essential-entities-en` | unanswerable | statute | en | 10 returned | — | *(unverified)* |
| `un-nis2-management-liability-de` | unanswerable | practitioner | de | 10 returned | — | *(unverified)* |
| `un-machinery-safety-en` | unanswerable | statute | en | 10 returned | — | *(unverified)* |
| `un-ai-act-high-risk-en` | unanswerable | statute | en | 10 returned | — | *(unverified)* |
| `un-product-liability-en` | unanswerable | practitioner | en | 10 returned | — | *(unverified)* |
| `un-dora-financial-de` | unanswerable | practitioner | de | 10 returned | — | *(unverified)* |
| `un-out-of-domain-en` | unanswerable | practitioner | en | 10 returned | — | *(unverified)* |
| `un-out-of-domain-de` | unanswerable | practitioner | de | 10 returned | — | *(unverified)* |

## How to read this

- **R@k** is true recall: the fraction of an item's gold labels appearing in the top k. An item with two gold labels cannot reach 1.00 at k=1.
- **MRR@10** is the mean reciprocal rank of the *first* gold label within the top 10.
- A low score is a finding, not a bug. This step changes no retrieval code.
