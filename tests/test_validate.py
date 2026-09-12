"""Structural validation. Built on fixture segments, then deliberately damaged."""

from collections.abc import Sequence

import pytest

from cra_assistant.models import Parser, Segment, SegmentKind
from cra_assistant.validate import (
    KNOWN_SHORT_SEGMENTS,
    Severity,
    has_errors,
    roman_to_int,
    validate_segments,
)
from test_segment import segments


def codes(problems: Sequence[object]) -> set[str]:
    return {problem.code for problem in problems}  # type: ignore[attr-defined]


def drop(found: list[Segment], predicate) -> list[Segment]:
    return [segment for segment in found if not predicate(segment)]


@pytest.mark.parametrize("lang", ["en", "de"])
def test_a_correctly_segmented_excerpt_has_no_errors(lang: str) -> None:
    problems = validate_segments(segments(lang), Parser.EURLEX_HTML)

    assert not has_errors(problems)


def test_a_gap_in_the_articles_is_an_error() -> None:
    """The failure this whole module exists for: a marker stopped matching and a
    third of the regulation went missing without anything complaining."""
    damaged = drop(
        segments("en"),
        lambda segment: segment.kind is SegmentKind.ARTICLE and segment.number == "1",
    )

    problems = validate_segments(damaged, Parser.EURLEX_HTML)

    assert has_errors(problems)
    assert "article-sequence" in codes(problems)
    assert any("missing [1]" in problem.message for problem in problems)


def test_a_gap_in_the_recitals_is_an_error() -> None:
    damaged = drop(
        segments("en"),
        lambda segment: segment.kind is SegmentKind.RECITAL and segment.number == "2",
    )

    assert "recital-sequence" in codes(validate_segments(damaged, Parser.EURLEX_HTML))


def test_losing_a_whole_division_is_an_error() -> None:
    damaged = drop(segments("en"), lambda segment: segment.kind is SegmentKind.ANNEX)

    assert "no-annexes" in codes(validate_segments(damaged, Parser.EURLEX_HTML))


def test_annexes_out_of_order_are_an_error() -> None:
    found = segments("en")
    annexes = [segment for segment in found if segment.kind is SegmentKind.ANNEX]
    others = [segment for segment in found if segment.kind is not SegmentKind.ANNEX]

    problems = validate_segments([*others, *reversed(annexes)], Parser.EURLEX_HTML)

    assert "annex-order" in codes(problems)


def test_duplicate_segment_ids_are_an_error() -> None:
    found = segments("en")

    assert "duplicate-id" in codes(validate_segments([*found, found[0]], Parser.EURLEX_HTML))


def test_no_segments_at_all_is_an_error() -> None:
    assert "empty" in codes(validate_segments([], Parser.EURLEX_HTML))


def short_article(number: str) -> Segment:
    """A one-sentence article, numbered so the sequence stays contiguous."""
    return segments("en")[0].model_copy(
        update={
            "text": "Short.",
            "id": f"cra-en:article:{number}",
            "kind": SegmentKind.ARTICLE,
            "number": number,
        }
    )


def test_a_known_short_article_is_a_warning_not_an_error() -> None:
    """Five CRA articles genuinely are one sentence, verified in both language
    versions. Naming them keeps the check from being permanently noisy.

    Article 29 is out of sequence for this two-article excerpt, so the result
    also carries a sequence error; this test asserts only on the length verdict.
    """
    assert "cra-en:article:29" in KNOWN_SHORT_SEGMENTS

    problems = validate_segments([*segments("en"), short_article("29")], Parser.EURLEX_HTML)
    verdict = next(problem for problem in problems if problem.segment_id == "cra-en:article:29")

    assert verdict.severity is Severity.WARNING
    assert verdict.code == "known-short-segment"


def test_an_unexpected_short_article_is_an_error() -> None:
    """The tightening: anything short that is not on the allowlist means a
    marker stopped matching and truncated real text."""
    found = [*segments("en"), short_article("3")]

    problems = validate_segments(found, Parser.EURLEX_HTML)

    assert has_errors(problems)
    assert any(
        problem.code == "short-segment"
        and problem.severity is Severity.ERROR
        and problem.segment_id == "cra-en:article:3"
        for problem in problems
    )


def test_short_sections_of_an_unstructured_document_stay_advisory() -> None:
    """A FAQ heading with two lines under it is not a truncation."""
    tiny = segments("en")[0].model_copy(
        update={"text": "Short.", "id": "faq:section:1", "kind": SegmentKind.SECTION, "number": "1"}
    )

    problems = validate_segments([tiny], Parser.MARKDOWN)

    assert not has_errors(problems)


def test_a_document_without_legal_structure_is_not_asked_for_articles() -> None:
    """A community FAQ has no recitals, and saying so is not a finding."""
    faq = segments("en")[0].model_copy(update={"kind": SegmentKind.SECTION, "number": "1"})

    problems = validate_segments([faq], Parser.MARKDOWN)

    assert not has_errors(problems)
    assert "no-recitals" not in codes(problems)


@pytest.mark.parametrize(
    ("numeral", "expected"),
    [("I", 1), ("IV", 4), ("VIII", 8), ("XIV", 14), ("", None), ("ABC", None), ("1", None)],
)
def test_roman_numerals(numeral: str, expected: int | None) -> None:
    assert roman_to_int(numeral) == expected
