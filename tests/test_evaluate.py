"""Metric arithmetic on synthetic rankings.

The point of these tests: a known-bad ranking must produce known-bad numbers.
A metric that quietly flatters the system is worse than no metric, because the
whole purpose of this step is to be told an unwelcome truth.
"""

import pytest

from cra_assistant.evaluate import (
    ItemResult,
    aggregate,
    aggregate_restraint,
    render_report,
    run,
    unknown_gold_ids,
)
from cra_assistant.golden import AnswerType, GoldenItem, Vocabulary


def item(
    item_id: str = "an-item",
    *,
    expected: tuple[str, ...] = ("cra-en:article:13",),
    answer_type: AnswerType = AnswerType.ANSWERABLE,
    vocabulary: Vocabulary = Vocabulary.STATUTE,
) -> GoldenItem:
    return GoldenItem(
        id=item_id,
        question="What are the obligations of manufacturers?",
        lang="en",
        vocabulary=vocabulary,
        answer_type=answer_type,
        expected_segment_ids=expected,
    )


def ranked(*ids: str) -> ItemResult:
    return ItemResult(item=item(), ranked_ids=ids)


def numbered(count: int, *, gold_at: int | None = None) -> tuple[str, ...]:
    """A ranking of `count` distractors, optionally with the gold label placed."""
    ids = [f"cra-en:article:{index}" for index in range(100, 100 + count)]
    if gold_at is not None:
        ids[gold_at - 1] = "cra-en:article:13"
    return tuple(ids)


# --- a perfect ranking ------------------------------------------------------


def test_a_perfect_ranking_scores_one_everywhere() -> None:
    result = ItemResult(item=item(), ranked_ids=numbered(10, gold_at=1))

    assert result.recall_at(1) == 1.0
    assert result.recall_at(5) == 1.0
    assert result.recall_at(10) == 1.0
    assert result.reciprocal_rank == 1.0
    assert result.first_hit_rank == 1


# --- a known-bad ranking ----------------------------------------------------


def test_a_ranking_that_misses_entirely_scores_zero_everywhere() -> None:
    """The failure that matters: nothing relevant in the top ten."""
    result = ItemResult(item=item(), ranked_ids=numbered(10))

    assert result.recall_at(1) == 0.0
    assert result.recall_at(10) == 0.0
    assert result.reciprocal_rank == 0.0
    assert result.first_hit_rank is None


def test_an_empty_ranking_scores_zero_and_is_not_an_error() -> None:
    result = ranked()

    assert result.recall_at(10) == 0.0
    assert result.reciprocal_rank == 0.0
    assert result.returned_nothing


@pytest.mark.parametrize(
    ("gold_at", "r1", "r5", "r10", "mrr"),
    [
        (1, 1.0, 1.0, 1.0, 1.0),
        (2, 0.0, 1.0, 1.0, 0.5),
        (5, 0.0, 1.0, 1.0, 0.2),
        (6, 0.0, 0.0, 1.0, 1 / 6),
        (10, 0.0, 0.0, 1.0, 0.1),
    ],
)
def test_scores_fall_as_the_gold_label_sinks(
    gold_at: int, r1: float, r5: float, r10: float, mrr: float
) -> None:
    result = ItemResult(item=item(), ranked_ids=numbered(10, gold_at=gold_at))

    assert result.recall_at(1) == r1
    assert result.recall_at(5) == r5
    assert result.recall_at(10) == r10
    assert result.reciprocal_rank == pytest.approx(mrr)


def test_a_gold_label_below_the_cutoff_earns_no_reciprocal_rank() -> None:
    """Found at 12 is not found, as far as MRR@10 is concerned."""
    result = ItemResult(item=item(), ranked_ids=numbered(12, gold_at=12))

    assert result.reciprocal_rank == 0.0
    assert result.first_hit_rank == 12, "but the report still shows where it was"


# --- multi-label items ------------------------------------------------------


def test_recall_is_a_fraction_of_the_gold_labels_not_a_hit_rate() -> None:
    """With two gold labels, finding one is half the answer, not all of it."""
    result = ItemResult(
        item=item(expected=("cra-en:article:13", "cra-en:annex:I")),
        ranked_ids=("cra-en:article:13", "x:article:1", "y:article:2"),
    )

    assert result.recall_at(10) == 0.5
    assert result.reciprocal_rank == 1.0, "MRR asks only about the first hit"


def test_a_two_label_item_cannot_score_one_at_k_of_one() -> None:
    result = ItemResult(
        item=item(expected=("cra-en:article:13", "cra-en:annex:I")),
        ranked_ids=("cra-en:article:13", "cra-en:annex:I"),
    )

    assert result.recall_at(1) == 0.5
    assert result.recall_at(5) == 1.0


# --- aggregation ------------------------------------------------------------


def test_aggregation_averages_over_items() -> None:
    results = [
        ItemResult(item=item("item-a"), ranked_ids=numbered(10, gold_at=1)),
        ItemResult(item=item("item-b"), ranked_ids=numbered(10)),
    ]

    metrics = aggregate(results)

    assert metrics.count == 2
    assert metrics.recall[1] == 0.5
    assert metrics.mrr == 0.5


def test_items_without_gold_labels_are_not_scored() -> None:
    """Unanswerable items have no recall. Averaging them in as zeros would
    understate retrieval; averaging them in as ones would flatter it."""
    unanswerable = ItemResult(
        item=item("item-u", expected=(), answer_type=AnswerType.UNANSWERABLE),
        ranked_ids=numbered(10),
    )

    assert aggregate([unanswerable]).count == 0
    assert not aggregate([unanswerable]).scored


def test_restraint_counts_what_retrieval_handed_over() -> None:
    results = [
        ItemResult(
            item=item("item-a", expected=(), answer_type=AnswerType.UNANSWERABLE), ranked_ids=()
        ),
        ItemResult(
            item=item("item-b", expected=(), answer_type=AnswerType.UNANSWERABLE),
            ranked_ids=numbered(10),
        ),
    ]

    restraint = aggregate_restraint(results)

    assert restraint.count == 2
    assert restraint.returned_nothing == 1
    assert restraint.empty_rate == 0.5
    assert restraint.mean_returned == 5.0


# --- broken labels ----------------------------------------------------------


def test_a_gold_label_outside_the_corpus_is_reported_not_silently_zero() -> None:
    """Otherwise a typo in the golden set looks exactly like a retrieval failure."""
    results = [ItemResult(item=item(expected=("cra-en:article:999",)), ranked_ids=numbered(3))]

    assert unknown_gold_ids(results, ["cra-en:article:13"]) == ["cra-en:article:999"]


# --- running against a retriever -------------------------------------------


class FakeRetriever:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        self.queries: list[tuple[str, int]] = []

    def retrieve(self, query: str, k: int):
        self.queries.append((query, k))
        return [type("S", (), {"id": one})() for one in self.ids[:k]]


def test_run_asks_the_retriever_once_per_item_at_the_requested_depth() -> None:
    retriever = FakeRetriever(["cra-en:article:13"])

    results = run([item("item-a"), item("item-b")], retriever, k=7)

    assert [query[1] for query in retriever.queries] == [7, 7]
    assert len(results) == 2


# --- report -----------------------------------------------------------------


def test_the_report_says_when_the_numbers_are_provisional() -> None:
    report = render_report(
        [ItemResult(item=item(), ranked_ids=numbered(10, gold_at=1))],
        corpus_size=488,
        k=10,
        included_unverified=True,
        unverified_count=40,
        broken_labels=[],
    )

    assert "provisional" in report
    assert "verified = false" in report


def test_the_report_names_broken_labels() -> None:
    report = render_report(
        [ItemResult(item=item(), ranked_ids=numbered(10))],
        corpus_size=488,
        k=10,
        included_unverified=False,
        unverified_count=0,
        broken_labels=["cra-en:article:999"],
    )

    assert "Broken gold labels" in report
    assert "cra-en:article:999" in report


def test_the_report_slices_by_vocabulary_and_answer_type() -> None:
    results = [
        ItemResult(
            item=item("item-a", vocabulary=Vocabulary.STATUTE), ranked_ids=numbered(10, gold_at=1)
        ),
        ItemResult(
            item=item("item-b", vocabulary=Vocabulary.PRACTITIONER), ranked_ids=numbered(10)
        ),
    ]

    report = render_report(
        results,
        corpus_size=488,
        k=10,
        included_unverified=False,
        unverified_count=0,
        broken_labels=[],
    )

    assert "## By vocabulary" in report
    assert "## By answer type" in report
    assert "statute" in report and "practitioner" in report
