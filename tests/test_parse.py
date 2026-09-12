"""Block extraction and language profiles. No network, no fetched data."""

import pytest

from cra_assistant.parse import (
    GERMAN,
    PROFILES,
    UnsupportedLanguageError,
    decode,
    html_to_blocks,
    html_to_lines,
    normalise,
    profile_for,
)


def test_inline_elements_do_not_break_a_line() -> None:
    """The discriminator the whole parser rests on.

    EUR-Lex renders a footnote reference as ``<a>(<span>1</span>)</a>`` inside a
    paragraph, and a recital number as ``<p>(1)</p>``. If inline tags broke
    lines, both would appear as a line reading ``(1)`` and recitals could not be
    told from footnotes.
    """
    markup = """
        <p>Having regard to the opinion of the Committee <a href="#n1">(<span>1</span>)</a>,</p>
        <p>(1)</p>
        <p>Cybersecurity is one of the key challenges.</p>
    """

    assert html_to_lines(markup) == [
        "Having regard to the opinion of the Committee (1),",
        "(1)",
        "Cybersecurity is one of the key challenges.",
    ]


def test_script_and_style_content_is_dropped() -> None:
    markup = "<p>text</p><script>var agentId = 'x';</script><style>p{color:red}</style>"

    assert html_to_lines(markup) == ["text"]


def test_normalise_collapses_the_non_breaking_spaces_eurlex_uses() -> None:
    # Named escapes, so the character under test is unmistakable in the source.
    assert normalise("Article\N{NO-BREAK SPACE}13") == "Article 13"
    assert normalise("  a \n\t b\N{FIGURE SPACE}c  ") == "a b c"


def test_blocks_record_which_element_produced_them() -> None:
    blocks = html_to_blocks("<h2>Heading</h2><p>Body</p>")

    assert [(block.tag, block.text) for block in blocks] == [("h2", "Heading"), ("p", "Body")]
    assert blocks[0].is_heading and not blocks[1].is_heading


def test_decode_prefers_the_declared_charset() -> None:
    raw = '<meta charset="iso-8859-1"><p>caf\xe9</p>'.encode("latin-1")

    assert "café" in decode(raw)


def test_decode_survives_a_bogus_charset() -> None:
    assert "ok" in decode(b'<meta charset="not-a-charset"><p>ok</p>')


def test_both_shipped_languages_have_a_profile() -> None:
    assert set(PROFILES) == {"en", "de"}
    assert profile_for("de") is GERMAN


def test_an_unsupported_language_fails_loudly() -> None:
    """Silently producing zero segments would look like an empty document."""
    with pytest.raises(UnsupportedLanguageError, match="no language profile for 'fr'"):
        profile_for("fr")
