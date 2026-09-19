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
from cra_assistant.models import Segment
from cra_assistant.prompt import build_messages
from cra_assistant.retrieve import TOP_PASSAGES_PER_SEGMENT, Retriever
from cra_assistant.telemetry import USD_PER_MILLION_TOKENS

CUTOFFS = (1, 5, 10)
MRR_CUTOFF = 10

SWEEP_DEPTHS = (8, 20)
"""Retrieval depths compared side by side in every report.

8 is the default `ask` uses; 20 is the depth at which the manufacturer-definition
question started retrieving Article 3 instead of two peripheral recitals. Having
both in the same table turns "just raise k" from a temptation into a trade with
a printed price.
"""

CHARACTERS_PER_TOKEN = 4.0
"""Rough tokens-per-character for estimating prompt size without a tokeniser.

An estimate, and labelled as one wherever it is printed. Adding a tokeniser
dependency to put a second decimal place on a number whose purpose is comparing
two rows of the same table would not be worth it.
"""


@dataclass(frozen=True, slots=True)
class ItemResult:
    """What retrieval did for one golden item."""

    item: GoldenItem
    ranked_ids: tuple[str, ...]
    segments: tuple[Segment, ...] = ()
    """What was retrieved, kept so a report can price the prompt it would build."""

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
    def delivered_ids(self) -> frozenset[str]:
        """Segments in the window the model is actually given.

        Not the same set as :attr:`ranked_ids`, and the difference is the point:
        ranking is scored over k distinct segments, while the prompt carries k
        passages, which may all belong to fewer sources.
        """
        return frozenset(one.id for one in self.segments)

    @property
    def delivered_coverage(self) -> float:
        """Fraction of the gold labels the model was actually shown.

        R@k says the ranking put the right article near the top. This says the
        answer had it to read. Article 64 ranks fifth for the maximum-penalties
        question and is in none of the eight passages the default prompt
        delivers; only this number notices.
        """
        if not self.expected:
            return 0.0
        return len(self.expected & self.delivered_ids) / len(self.expected)

    @property
    def returned_nothing(self) -> bool:
        return not self.ranked_ids


@dataclass(frozen=True, slots=True)
class Metrics:
    """Aggregated scores for one slice."""

    count: int
    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    delivered_coverage: float = 0.0
    """Mean fraction of gold labels present in the delivered window, not the
    ranking window. Paired with prompt cost, because they describe the same
    window and recall does not."""

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
        delivered_coverage=sum(result.delivered_coverage for result in scorable) / len(scorable),
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


@dataclass(frozen=True, slots=True)
class DepthResult:
    """One row of the depth sweep: what this k retrieves, and what it costs."""

    k: int
    metrics: Metrics
    restraint: RestraintMetrics
    mean_prompt_characters: float
    model: str

    @property
    def mean_prompt_tokens(self) -> int:
        return int(self.mean_prompt_characters / CHARACTERS_PER_TOKEN)

    @property
    def usd_per_question(self) -> float | None:
        prices = USD_PER_MILLION_TOKENS.get(self.model)
        if prices is None:
            return None
        return round(self.mean_prompt_tokens * prices[0] / 1_000_000, 6)


def sweep(
    items: Sequence[GoldenItem],
    retriever: Retriever,
    *,
    depths: Sequence[int] = SWEEP_DEPTHS,
    model: str,
) -> list[DepthResult]:
    """Score retrieval at each depth, with the prompt cost that depth implies.

    The cost is what the *prompt* would contain if these segments were handed to
    the model; no model is called, so this stays offline and free.
    """
    rows = []
    for depth in depths:
        results = run(items, retriever, k=depth)
        scorable = [result for result in results if result.expected]
        unanswerable = [
            result for result in results if result.item.answer_type is AnswerType.UNANSWERABLE
        ]
        characters = [
            sum(
                len(message["content"])
                for message in build_messages(result.item.question, retrieved)
            )
            for result, retrieved in ((r, r.segments) for r in results)
        ]
        rows.append(
            DepthResult(
                k=depth,
                metrics=aggregate(scorable),
                restraint=aggregate_restraint(unanswerable),
                mean_prompt_characters=sum(characters) / len(characters) if characters else 0.0,
                model=model,
            )
        )
    return rows


def distinct_ids(units: Iterable[Segment]) -> tuple[str, ...]:
    """Segment ids in the order they first appear, deduplicated.

    Retrieval scores passages and several may share a parent (ADR-0018); a gold
    label names the parent.
    """
    seen: set[str] = set()
    return tuple(one.id for one in units if not (one.id in seen or seen.add(one.id)))


def run(items: Iterable[GoldenItem], retriever: Retriever, *, k: int = 10) -> list[ItemResult]:
    """Retrieve for every item. Deterministic, offline, no model involved.

    ``k`` counts *segments*, as it did before passages existed, so R@k and MRR@10
    keep their meaning and stay comparable with the earlier baselines. Since one
    segment can contribute up to :data:`TOP_PASSAGES_PER_SEGMENT` passages, the
    ranking window asks for that many times k passages to be sure of seeing k
    distinct segments. The delivered window is separate and stays k passages:
    that is what ``ask`` sends, and what prompt cost must be measured on.
    """
    results = []
    for item in items:
        delivered = tuple(retriever.retrieve(item.question, k))
        ranked = retriever.retrieve(item.question, k * TOP_PASSAGES_PER_SEGMENT)
        results.append(
            ItemResult(
                item=item,
                ranked_ids=distinct_ids(ranked)[:k],
                segments=delivered,
            )
        )
    return results


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
        return f"| {label} | 0 | — | — | — | — | — |"
    return (
        f"| {label} | {metrics.count} | "
        + " | ".join(f"{metrics.recall[k]:.2f}" for k in CUTOFFS)
        + f" | {metrics.mrr:.3f} | {metrics.delivered_coverage:.2f} |"
    )


def render_sweep(rows: Sequence[DepthResult]) -> list[str]:
    """The depth comparison, so "raise k" is a trade with a printed price."""
    if not rows:
        return []
    model = rows[0].model
    lines = [
        "## Retrieval depth",
        "",
        f"| k | R@1 | R@5 | R@10 | MRR@10 | delivered coverage | mean prompt tokens "
        f"| est. $/question ({model}) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        recall = (
            " | ".join(f"{row.metrics.recall[cut]:.2f}" for cut in CUTOFFS)
            if row.metrics.scored
            else " | ".join(["—"] * len(CUTOFFS))
        )
        cost = f"{row.usd_per_question:.6f}" if row.usd_per_question is not None else "—"
        lines.append(
            f"| {row.k} | {recall} | {row.metrics.mrr:.3f} "
            f"| {row.metrics.delivered_coverage:.2f} | {row.mean_prompt_tokens:,} | {cost} |"
        )
    lines += [
        "",
        "*The recall columns and MRR@10 are **ranking** measures, scored over k distinct "
        "segments. **Delivered coverage** is the fraction of gold labels inside the k "
        "passages the prompt actually carries, and it is the column that belongs beside "
        "the cost: they describe the same window. The two diverge when several passages "
        "of one article fill the prompt — Article 64 ranks fifth for the maximum-penalties "
        "question and appears in none of the eight passages delivered at the default depth.*",
        "",
        f"*Recall cutoffs are fixed at {CUTOFFS}, so R@10 is unchanged by a k below 10 "
        "and identical across rows once k exceeds it; MRR@10 likewise. What the sweep "
        "shows is what each depth costs and how much of the gold set it puts in the "
        "window at all.*",
        "",
        f"*Token counts are estimated at {CHARACTERS_PER_TOKEN} characters per token, "
        "not measured with a tokeniser. Prompt tokens only; completions are extra. "
        "No model was called to produce this table.*",
        "",
    ]
    return lines


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
    depth_rows: Sequence[DepthResult] = (),
    model: str = "",
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
        f"- Retrieval depth: k={k} — **ranking** measures are scored over {k} distinct "
        f"segments, while the prompt delivers {k} passages, which may come from fewer "
        "sources (ADR-0018)",
        "- Retriever: in-memory BM25, no stemming, no stopword list (ADR-0006)",
    ]
    if model:
        # A baseline without a model id is not reproducible, and the cost column
        # below is meaningless without knowing what it was priced against.
        lines.append(f"- Model for cost estimates: `{model}` (ADR-0010)")
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

    header = (
        "| Slice | n | R@1 | R@5 | R@10 | MRR@10 | delivered coverage |\n"
        "|---|---:|---:|---:|---:|---:|---:|"
    )

    lines += ["", "## Overall", "", header, _row("all labelled items", aggregate(scorable)), ""]
    lines += render_sweep(depth_rows)

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
