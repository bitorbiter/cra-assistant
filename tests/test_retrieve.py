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
