"""Does the document contain anything? A different question from: is it stable?

The regression these tests exist for: three sources sat in the corpus for two
steps, fetched, checksummed, pinned and reported clean, containing a GitHub
navigation menu and an error banner.
"""

from pathlib import Path

import pytest

from cra_assistant.models import Parser, TrustTier
from cra_assistant.plausibility import (
    MINIMUM_SEGMENTS,
    MINIMUM_TEXT_RATIO,
    check_document,
)
from cra_assistant.problems import Severity, has_errors
from cra_assistant.segment import segment_document
from factories import make_source

FIXTURES = Path(__file__).parent / "fixtures"


def codes(problems) -> set[str]:
    return {problem.code for problem in problems}


def check(source, raw: bytes):
    return check_document(source, raw, segment_document(source, raw))


def test_a_client_rendered_page_is_rejected() -> None:
    """The exact failure. This page was in the corpus, and everything said fine."""
    source = make_source("a-github-page", citation_prefix="ghp", parser=Parser.GENERIC_HTML)
    raw = (FIXTURES / "client_rendered_page.html").read_bytes()

    problems = check(source, raw)

    assert has_errors(problems)
    assert "client-rendered" in codes(problems)
    assert any("ADR-0009" in problem.message for problem in problems), (
        "the error should say what to do instead, not just that something is wrong"
    )


@pytest.mark.parametrize(
    "marker",
    [
        "There was an error while loading",
        "You need to enable JavaScript to run this app",
        "Please enable JavaScript",
    ],
)
def test_every_client_render_marker_is_caught(marker: str) -> None:
    source = make_source("a-page", citation_prefix="pge", parser=Parser.MARKDOWN)
    raw = f"# Heading\n\n{marker}\n\n## Second\n\n{'padding text. ' * 60}\n".encode()

    assert "client-rendered" in codes(check(source, raw))


def test_markup_with_almost_no_text_is_rejected() -> None:
    """The measured signal: real EUR-Lex exports run 0.49, the broken pages 0.014."""
    source = make_source("bloated", citation_prefix="blo", parser=Parser.GENERIC_HTML)
    filler = '<div class="x" data-a="1" data-b="2" data-c="3"></div>' * 400
    raw = f"<html><body><h1>Title</h1>{filler}<p>short</p></body></html>".encode()

    problems = check(source, raw)

    assert "markup-without-text" in codes(problems)
    assert has_errors(problems)


def test_a_real_document_passes() -> None:
    source = make_source(
        "cra-en",
        citation_prefix="cra-en",
        lang="en",
        tier=TrustTier.TRUSTED,
        parser=Parser.EURLEX_HTML,
    )
    raw = (FIXTURES / "cra_excerpt_en.html").read_bytes()

    assert check(source, raw) == []


def test_a_document_with_too_few_segments_is_rejected() -> None:
    source = make_source("thin", citation_prefix="thn", parser=Parser.MARKDOWN)
    raw = b"# Only heading\n\n" + b"padding text. " * 100

    problems = check(source, raw)

    assert "too-few-segments" in codes(problems)
    assert MINIMUM_SEGMENTS > 1


def test_a_document_with_too_little_text_is_rejected() -> None:
    source = make_source("tiny", citation_prefix="tny", parser=Parser.MARKDOWN)
    raw = b"# A\n\nshort\n\n# B\n\nalso short\n\n# C\n\nstill short\n"

    assert "too-little-text" in codes(check(source, raw))


def test_the_ratio_check_does_not_apply_to_json_sources() -> None:
    """A document assembled from an API has no markup to compare text against."""
    source = make_source("issues", citation_prefix="iss", parser=Parser.GITHUB_ISSUES)
    body = "A genuine question about steward obligations. " * 20
    raw = (
        '{"kind":"github-issues","repository":"o/r","comments":[],"issues":['
        + ",".join(
            f'{{"number":{n},"title":"Question {n}","body":"{body}","state":"open",'
            f'"created_at":"","html_url":""}}'
            for n in (1, 2, 3, 4)
        )
        + "]}"
    ).encode()

    assert "markup-without-text" not in codes(check(source, raw))
    assert check(source, raw) == []


def test_an_untrusted_source_failing_is_an_error_not_a_warning() -> None:
    """A source that yields nothing usable should not be in the registry at all,
    whichever tier it claims."""
    source = make_source(
        "junk", citation_prefix="jnk", tier=TrustTier.UNTRUSTED, parser=Parser.GENERIC_HTML
    )
    raw = (FIXTURES / "client_rendered_page.html").read_bytes()

    problems = check(source, raw)

    assert all(problem.severity is Severity.ERROR for problem in problems)


def test_the_ratio_threshold_sits_between_the_measured_populations() -> None:
    """Documented so nobody tunes it without knowing what it was set from."""
    assert 0.014 < MINIMUM_TEXT_RATIO < 0.48
