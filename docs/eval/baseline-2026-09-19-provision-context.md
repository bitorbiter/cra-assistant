# Retrieval baseline — 2026-09-19

> **Superseded, kept as recorded.** Adds the governing provision to each delivered sub-point and the `delivered coverage` column, still scoring a segment as the sum of its best two passages. That aggregation was reverted the following day: [baseline-2026-09-20-best-passage.md](baseline-2026-09-20-best-passage.md).

- Items scored: **28** with gold labels, **10** unanswerable, 38 total
- Corpus: 2062 segments
- Retrieval depth: k=10 — **ranking** measures are scored over 10 distinct segments, while the prompt delivers 10 passages, which may come from fewer sources (ADR-0018)
- Retriever: in-memory BM25, no stemming, no stopword list (ADR-0006)
- Model for cost estimates: `gpt-4o-mini-2024-07-18` (ADR-0010)

## Overall

| Slice | n | R@1 | R@5 | R@10 | MRR@10 | delivered coverage |
|---|---:|---:|---:|---:|---:|---:|
| all labelled items | 28 | 0.31 | 0.56 | 0.66 | 0.579 | 0.56 |

## Retrieval depth

| k | R@1 | R@5 | R@10 | MRR@10 | delivered coverage | mean prompt tokens | est. $/question (gpt-4o-mini-2024-07-18) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0.31 | 0.56 | 0.62 | 0.575 | 0.51 | 2,928 | 0.000439 |
| 20 | 0.31 | 0.56 | 0.66 | 0.579 | 0.68 | 6,709 | 0.001006 |

*The recall columns and MRR@10 are **ranking** measures, scored over k distinct segments. **Delivered coverage** is the fraction of gold labels inside the k passages the prompt actually carries, and it is the column that belongs beside the cost: they describe the same window. The two diverge when several passages of one article fill the prompt — Article 64 ranks fifth for the maximum-penalties question and appears in none of the eight passages delivered at the default depth.*

*Recall cutoffs are fixed at (1, 5, 10), so R@10 is unchanged by a k below 10 and identical across rows once k exceeds it; MRR@10 likewise. What the sweep shows is what each depth costs and how much of the gold set it puts in the window at all.*

*Token counts are estimated at 4.0 characters per token, not measured with a tokeniser. Prompt tokens only; completions are extra. No model was called to produce this table.*

## By vocabulary

| Slice | n | R@1 | R@5 | R@10 | MRR@10 | delivered coverage |
|---|---:|---:|---:|---:|---:|---:|
| statute | 17 | 0.32 | 0.73 | 0.78 | 0.644 | 0.73 |
| practitioner | 11 | 0.28 | 0.31 | 0.46 | 0.479 | 0.31 |

*The slice that matters. Statute vocabulary uses the regulation's own words; practitioner vocabulary is how somebody with the problem actually asks.*

## By answer type

| Slice | n | R@1 | R@5 | R@10 | MRR@10 | delivered coverage |
|---|---:|---:|---:|---:|---:|---:|
| answerable | 23 | 0.32 | 0.63 | 0.72 | 0.612 | 0.63 |
| unanswerable | 0 | — | — | — | — | — |
| untrusted_only | 5 | 0.25 | 0.25 | 0.35 | 0.429 | 0.25 |

## Unanswerable items

Recall is undefined with no gold label, so these are measured differently. Retrieval cannot abstain; the question is how much plausible material it hands the model anyway.

| n | returned nothing | mean segments returned |
|---:|---:|---:|
| 10 | 0 (0%) | 10.0 |

## Per item

| id | type | vocab | lang | first hit | R@10 | note |
|---|---|---|---|---:|---:|---|
| `def-manufacturer-de` | answerable | statute | de | 2 | 0.33 | |
| `manufacturer-obligations-en` | answerable | statute | en | 4 | 1.00 | |
| `report-exploited-vulnerability-en` | answerable | statute | en | 3 | 1.00 | |
| `report-deadline-practitioner-de` | answerable | practitioner | de | 1 | 1.00 | |
| `single-reporting-platform-en` | answerable | practitioner | en | 1 | 1.00 | |
| `authorised-representative-en` | answerable | statute | en | 1 | 1.00 | |
| `importer-obligations-de` | answerable | statute | de | 1 | 1.00 | |
| `oss-steward-duties-en` | answerable | statute | en | 1 | 0.50 | |
| `oss-steward-practitioner-de` | answerable | practitioner | de | 8 | 0.33 | |
| `security-attestation-foss-en` | answerable | statute | en | 6 | 0.50 | |
| `declaration-of-conformity-content-en` | answerable | statute | en | 1 | 1.00 | |
| `ce-marking-de` | answerable | statute | de | 1 | 1.00 | |
| `technical-documentation-en` | answerable | statute | en | 1 | 1.00 | |
| `conformity-assessment-choice-practitioner-en` | answerable | practitioner | en | not found | 0.00 | |
| `penalties-maximum-en` | answerable | statute | en | 4 | 1.00 | |
| `penalties-practitioner-de` | answerable | practitioner | de | not found | 0.00 | |
| `application-date-en` | answerable | statute | en | not found | 0.00 | |
| `scope-en` | answerable | statute | en | 3 | 0.50 | |
| `important-products-en` | answerable | statute | en | 1 | 1.00 | |
| `critical-products-de` | answerable | statute | de | 1 | 1.00 | |
| `user-information-de` | answerable | statute | de | 1 | 1.00 | |
| `sbom-practitioner-en` | answerable | practitioner | en | 1 | 1.00 | |
| `un-gdpr-dpo-en` | unanswerable | practitioner | en | 10 returned | — | |
| `un-gdpr-breach-deadline-de` | unanswerable | statute | de | 10 returned | — | |
| `un-nis2-essential-entities-en` | unanswerable | statute | en | 10 returned | — | |
| `un-nis2-management-liability-de` | unanswerable | practitioner | de | 10 returned | — | |
| `un-machinery-safety-en` | unanswerable | statute | en | 10 returned | — | |
| `un-ai-act-high-risk-en` | unanswerable | statute | en | 10 returned | — | |
| `un-product-liability-en` | unanswerable | practitioner | en | 10 returned | — | |
| `un-dora-financial-de` | unanswerable | practitioner | de | 10 returned | — | |
| `un-out-of-domain-en` | unanswerable | practitioner | en | 10 returned | — | |
| `un-out-of-domain-de` | unanswerable | practitioner | de | 10 returned | — | |
| `ut-steward-reporting-clock` | untrusted_only | practitioner | en | not found | 0.00 | |
| `ut-steward-eol-versions` | untrusted_only | practitioner | en | 1 | 0.50 | |
| `ut-unincorporated-group-steward` | untrusted_only | practitioner | en | 7 | 0.25 | |
| `ut-one-person-company-steward` | untrusted_only | practitioner | en | not found | 0.00 | |
| `ut-sponsorware-manufacturer` | untrusted_only | practitioner | en | 1 | 1.00 | |
| `early-application-dates-en` | answerable | statute | en | 9 | 0.50 | |

## How to read this

- **R@k** is true recall: the fraction of an item's gold labels appearing in the top k. An item with two gold labels cannot reach 1.00 at k=1.
- **MRR@10** is the mean reciprocal rank of the *first* gold label within the top 10.
- A low score is a finding, not a bug. This step changes no retrieval code.

