"""BM25 retrieval. No network, no fetched corpus."""

import pytest

from cra_assistant.models import Parser, Segment, SegmentKind, TrustTier
from cra_assistant.retrieve import Bm25Retriever, tokenise
from factories import make_source

SOURCE = make_source("a-source", citation_prefix="doc", parser=Parser.MARKDOWN)


def segment(number: str, text: str, *, title: str = "") -> Segment:
    return Segment(
        id=f"doc:section:{number}",
        source_id=SOURCE.id,
        tier=TrustTier.UNTRUSTED,
        kind=SegmentKind.SECTION,
        number=number,
        title=title,
        text=text,
        citation=f"Doc, section {number}",
        source_sha256="sha256:" + "a" * 64,
        content_sha256="sha256:" + "b" * 64,
        lang="en",
        order=int(number),
    )


def test_tokenise_casefolds_and_drops_single_characters() -> None:
    assert tokenise("Hersteller, der EU a I") == ["hersteller", "der", "eu"]


def test_tokenise_handles_german_case_folding() -> None:
    """casefold, not lower: STRASSE and straße must meet."""
    assert tokenise("STRAẞE") == tokenise("straße")


def test_the_best_match_ranks_first() -> None:
    retriever = Bm25Retriever(
        [
            segment("1", "Obligations of manufacturer placing products on the market."),
            segment("2", "The Commission shall adopt implementing acts about reporting."),
            segment("3", "Definitions: manufacturer means a natural or legal person."),
        ]
    )

    top = retriever.retrieve("definitions manufacturer person", 2)

    assert [found.number for found in top] == ["3", "1"]


def test_there_is_no_stemming_and_that_is_a_real_weakness() -> None:
    """Documenting a limitation, not asserting a feature.

    "manufacturers" does not match "manufacturer", and in German
    "Herstellerpflichten" does not match "Hersteller". Fixing it properly needs
    a language-aware analyser, and this index is disposable (ADR-0006). The
    test exists so the limitation is visible rather than discovered later.
    """
    retriever = Bm25Retriever([segment("1", "Obligations of manufacturers.")])

    assert retriever.retrieve("manufacturer", 5) == []
    assert retriever.retrieve("manufacturers", 5) != []


def test_segments_with_no_matching_term_are_not_returned() -> None:
    """A zero score is not a weak match, it is no match at all."""
    retriever = Bm25Retriever([segment("1", "entirely unrelated text about bicycles")])

    assert retriever.retrieve("cybersecurity vulnerability handling", 5) == []


def test_retrieval_is_deterministic_including_ties() -> None:
    """Evaluation is meaningless if the same question gives two answers."""
    identical = [segment(str(index), "manufacturer obligations") for index in range(1, 6)]
    retriever = Bm25Retriever(identical)

    first = [found.id for found in retriever.retrieve("manufacturer", 3)]
    second = [found.id for found in retriever.retrieve("manufacturer", 3)]

    assert first == second == ["doc:section:1", "doc:section:2", "doc:section:3"]


def test_k_caps_the_result_count() -> None:
    retriever = Bm25Retriever([segment(str(index), "manufacturer") for index in range(1, 10)])

    assert len(retriever.retrieve("manufacturer", 4)) == 4


@pytest.mark.parametrize("query", ["", "   ", "a"])
def test_an_empty_query_retrieves_nothing(query: str) -> None:
    retriever = Bm25Retriever([segment("1", "manufacturer obligations")])

    assert retriever.retrieve(query, 5) == []


def test_an_empty_index_retrieves_nothing() -> None:
    assert Bm25Retriever([]).retrieve("manufacturer", 5) == []


def test_the_title_is_searchable() -> None:
    retriever = Bm25Retriever(
        [
            segment("1", "Some body text.", title="Obligations of manufacturers"),
            segment("2", "Some other body text.", title="Penalties"),
        ]
    )

    assert retriever.retrieve("obligations of manufacturers", 1)[0].number == "1"


def test_a_depth_below_one_is_refused() -> None:
    """k=-1 used to return the whole corpus and k=0 nothing, both silently."""
    import pytest

    retriever = Bm25Retriever([segment("1", "manufacturer text")])

    for depth in (0, -1):
        with pytest.raises(ValueError, match="at least 1"):
            retriever.retrieve("manufacturer", depth)


# --- how a segment's passages add up to its rank ----------------------------


# Filler with none of the query's words, to set passage lengths precisely: BM25
# normalises by length, so the sizes here are what make the two rules disagree.
FILLER = (
    "and the documentation referred to in that provision shall be retained for the period "
    "specified therein, together with any supporting material that the assessment requires. "
)
MATCHING_LINE = "The steward shall record each reporting clock step. "


def test_a_segment_ranks_on_its_best_passage_not_the_sum_of_two() -> None:
    """The regression that cost two tier-collapse controls and Article 71.

    Scoring a segment as the sum of its best two passages let a segment with two
    mediocre passages outscore one with a single better passage. How many
    passages a segment has is a fact about its length, not its relevance, so
    summing handed long statute articles an advantage over single-paragraph
    community posts — reintroducing the length bias passages exist to remove.

    The numbers here: the article's passages score 0.381 each and sum to 0.763;
    the post scores 0.445. Summing ranks the article first, the best passage
    ranks the post first, and the post is the one that answers the question.
    """
    paragraph = MATCHING_LINE + FILLER * 3
    long_article = segment("1", "\n".join(["1.", paragraph, "2.", paragraph]))
    short_post = segment("2", MATCHING_LINE + FILLER * 2)

    top = Bm25Retriever([long_article, short_post]).retrieve("steward reporting clock", 1)

    assert [found.number for found in top] == ["2"], (
        "the single best-matching passage must win; two weaker ones must not sum past it"
    )


def test_a_segment_still_delivers_up_to_two_passages_once_it_has_won() -> None:
    """Ranking on one passage does not mean delivering only one: the cap is a
    delivery decision, so the model still reads more than the matched line."""
    # Each paragraph is over MINIMUM_PASSAGE_CHARACTERS, or the splitter merges
    # them back into one passage and there is nothing to cap.
    article = segment(
        "1",
        "\n".join(
            [
                "1.",
                "Manufacturers shall report an actively exploited vulnerability contained in "
                "the product with digital elements without undue delay, and in any event "
                "within 24 hours of becoming aware of it, to the CSIRT designated as "
                "coordinator and to ENISA in accordance with this Article.",
                "2.",
                "The report shall be submitted through the single reporting platform "
                "established for that purpose, and shall be made simultaneously available to "
                "the CSIRT designated as coordinator and to ENISA using the electronic "
                "notification end-point of that platform.",
                "3.",
                "Unrelated text about conformity assessment procedures, notified bodies and "
                "the modules to be applied, which has nothing whatever to do with the duty "
                "to report a vulnerability that is being actively exploited in the field.",
            ]
        ),
    )

    delivered = Bm25Retriever([article]).retrieve("report actively exploited vulnerability", 5)

    assert len(delivered) == 2, "capped at TOP_PASSAGES_PER_SEGMENT"
    assert all(found.id == "doc:section:1" for found in delivered)
