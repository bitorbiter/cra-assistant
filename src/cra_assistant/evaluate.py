"""Measuring retrieval against the golden set.

Retrieval and generation are measured separately (ADR-0007). This module scores
retrieval only: given a question, did the segments that answer it come back, and
how far up. It needs no API key and no network, which is why it can run on every
push.

The overall numbers are the least interesting output. The slices — statute
versus practitioner vocabulary, answerable versus unanswerable — are what say
something about whether the system is usable by someone who does not already
know the regulation's wording.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from cra_assistant.golden import AnswerType, GoldenItem, Vocabulary
from cra_assistant.retrieve import Retriever

CUTOFFS = (1, 5, 10)
MRR_CUTOFF = 10


@dataclass(frozen=True, slots=True)
class ItemResult:
    """What retrieval did for one golden item."""

    item: GoldenItem
    ranked_ids: tuple[str, ...]

    @property
    def expected(self) -> frozenset[str]:
        return frozenset(self.item.expected_segment_ids)

    def recall_at(self, k: int) -> float:
        """Fraction of the gold labels found in the top ``k``.

        True recall, not hit rate: an item with two gold labels cannot score 1.0
        at k=1, and that is intended. It keeps a multi-label item honest instead
        of letting one lucky hit stand for both.
        """
        if not self.expected:
            return 0.0
        found = self.expected & set(self.ranked_ids[:k])
        return len(found) / len(self.expected)

    @property
    def reciprocal_rank(self) -> float:
        """1/rank of the first gold label within MRR_CUTOFF, else 0."""
        for position, segment_id in enumerate(self.ranked_ids[:MRR_CUTOFF], start=1):
            if segment_id in self.expected:
                return 1.0 / position
        return 0.0

    @property
    def first_hit_rank(self) -> int | None:
        for position, segment_id in enumerate(self.ranked_ids, start=1):
            if segment_id in self.expected:
                return position
        return None

    @property
    def returned_nothing(self) -> bool:
        return not self.ranked_ids


@dataclass(frozen=True, slots=True)
class Metrics:
    """Aggregated scores for one slice."""

    count: int
    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0

    @property
    def scored(self) -> bool:
        return self.count > 0


def aggregate(results: Sequence[ItemResult]) -> Metrics:
    """Mean of the per-item scores. Items with no gold label are not scorable."""
    scorable = [result for result in results if result.expected]
    if not scorable:
        return Metrics(count=0)
    return Metrics(
        count=len(scorable),
        recall={
            k: sum(result.recall_at(k) for result in scorable) / len(scorable) for k in CUTOFFS
        },
        mrr=sum(result.reciprocal_rank for result in scorable) / len(scorable),
    )


@dataclass(frozen=True, slots=True)
class RestraintMetrics:
    """For unanswerable items, where recall is undefined.

    Retrieval cannot 'abstain', so the honest measure is how often it returned
    nothing at all and how much it returned when it did. A retriever that always
    returns ten segments hands the model ten plausible distractors and makes
    abstention entirely the model's problem.
    """

    count: int
    returned_nothing: int
    mean_returned: float

    @property
    def empty_rate(self) -> float:
        return self.returned_nothing / self.count if self.count else 0.0


def aggregate_restraint(results: Sequence[ItemResult]) -> RestraintMetrics:
    if not results:
        return RestraintMetrics(0, 0, 0.0)
    return RestraintMetrics(
        count=len(results),
        returned_nothing=sum(1 for result in results if result.returned_nothing),
        mean_returned=sum(len(result.ranked_ids) for result in results) / len(results),
    )


def run(items: Iterable[GoldenItem], retriever: Retriever, *, k: int = 10) -> list[ItemResult]:
    """Retrieve for every item. Deterministic, offline, no model involved."""
    return [
        ItemResult(item=item, ranked_ids=tuple(s.id for s in retriever.retrieve(item.question, k)))
        for item in items
    ]


def unknown_gold_ids(results: Sequence[ItemResult], corpus_ids: Iterable[str]) -> list[str]:
    """Gold labels naming segments the corpus does not contain.

    A silent zero for an item whose label simply does not exist would look like
    a retrieval failure. It is a broken label, and it must be reported as one.
    """
    known = set(corpus_ids)
    missing: set[str] = set()
    for result in results:
        missing |= result.expected - known
    return sorted(missing)


# --- report rendering -------------------------------------------------------


def _row(label: str, metrics: Metrics) -> str:
    if not metrics.scored:
        return f"| {label} | 0 | — | — | — | — |"
    return (
        f"| {label} | {metrics.count} | "
        + " | ".join(f"{metrics.recall[k]:.2f}" for k in CUTOFFS)
        + f" | {metrics.mrr:.3f} |"
    )


def render_report(
    results: Sequence[ItemResult],
    *,
    corpus_size: int,
    k: int,
    included_unverified: bool,
    unverified_count: int,
    broken_labels: Sequence[str],
    report_date: date | None = None,
    corpus_note: str = "",
) -> str:
    """A markdown report. Committed as a baseline, so it must stand alone."""
    scorable = [result for result in results if result.expected]
    unanswerable = [
        result for result in results if result.item.answer_type is AnswerType.UNANSWERABLE
    ]

    lines = [
        f"# Retrieval baseline — {(report_date or date.today()).isoformat()}",
        "",
        f"- Items scored: **{len(scorable)}** with gold labels, "
        f"**{len(unanswerable)}** unanswerable, {len(results)} total",
        f"- Corpus: {corpus_size} segments",
        f"- Retrieval depth: k={k}",
        "- Retriever: in-memory BM25, no stemming, no stopword list (ADR-0006)",
    ]
    if corpus_note:
        lines.append(f"- {corpus_note}")

    if included_unverified:
        lines += [
            "",
            f"> **These numbers are provisional.** {unverified_count} of {len(results)} items are "
            "`verified = false`: the gold labels were drafted and have not been checked by hand. "
            "Treat this as a shape, not a measurement.",
        ]
    if broken_labels:
        lines += [
            "",
            "> **Broken gold labels** — these name segments that are not in the corpus, "
            "so their items score zero for a reason that is not retrieval's fault: "
            + ", ".join(broken_labels),
        ]

    header = "| Slice | n | R@1 | R@5 | R@10 | MRR@10 |\n|---|---:|---:|---:|---:|---:|"

    lines += ["", "## Overall", "", header, _row("all labelled items", aggregate(scorable)), ""]

    lines += ["## By vocabulary", "", header]
    for vocabulary in Vocabulary:
        subset = [r for r in scorable if r.item.vocabulary is vocabulary]
        lines.append(_row(str(vocabulary), aggregate(subset)))
    lines += [
        "",
        "*The slice that matters. Statute vocabulary uses the regulation's own words; "
        "practitioner vocabulary is how somebody with the problem actually asks.*",
        "",
    ]

    lines += ["## By answer type", "", header]
    for answer_type in AnswerType:
        subset = [r for r in scorable if r.item.answer_type is answer_type]
        lines.append(_row(str(answer_type), aggregate(subset)))
    lines.append("")

    restraint = aggregate_restraint(unanswerable)
    lines += [
        "## Unanswerable items",
        "",
        "Recall is undefined with no gold label, so these are measured differently. "
        "Retrieval cannot abstain; the question is how much plausible material it hands "
        "the model anyway.",
        "",
        "| n | returned nothing | mean segments returned |",
        "|---:|---:|---:|",
        f"| {restraint.count} | {restraint.returned_nothing} "
        f"({restraint.empty_rate:.0%}) | {restraint.mean_returned:.1f} |",
        "",
    ]

    lines += [
        "## Per item",
        "",
        "| id | type | vocab | lang | first hit | R@10 | note |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for result in results:
        if result.item.answer_type is AnswerType.UNANSWERABLE:
            hit = f"{len(result.ranked_ids)} returned"
            recall = "—"
        else:
            rank = result.first_hit_rank
            hit = str(rank) if rank else "not found"
            recall = f"{result.recall_at(10):.2f}"
        flag = "" if result.item.verified else " *(unverified)*"
        lines.append(
            f"| `{result.item.id}` | {result.item.answer_type} | {result.item.vocabulary} | "
            f"{result.item.lang} | {hit} | {recall} |{flag} |"
        )

    lines += [
        "",
        "## How to read this",
        "",
        "- **R@k** is true recall: the fraction of an item's gold labels appearing in the top k. "
        "An item with two gold labels cannot reach 1.00 at k=1.",
        "- **MRR@10** is the mean reciprocal rank of the *first* gold label within the top 10.",
        "- A low score is a finding, not a bug. This step changes no retrieval code.",
    ]
    return "\n".join(lines) + "\n"
