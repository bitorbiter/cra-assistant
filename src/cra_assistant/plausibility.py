"""Does this document contain anything worth having?

A different class of check from everything before it (ADR-0009). Checksums,
pins and drift detection all answer *is this stable?* — and they answered yes,
green, for three sources that contained a GitHub navigation menu and the words
"Uh oh! There was an error while loading." Stability is not usefulness, and no
amount of stability checking would ever have noticed.

This check runs at ingest, on every source, at both tiers. A trusted source that
yields nothing usable is obviously an error. An untrusted source that yields
nothing usable is an error too: a source that cannot contribute evidence has no
business being in the registry, and leaving it there means the corpus reports
five sources while carrying three.
"""

from collections.abc import Sequence

from cra_assistant.models import Parser, Segment, Source
from cra_assistant.parse import decode, html_to_lines
from cra_assistant.problems import Problem, Severity

MINIMUM_TEXT_RATIO = 0.10
"""Extracted text as a fraction of raw bytes, for markup formats.

Measured: the EUR-Lex HTML exports run 0.49 and 0.51. The client-rendered GitHub
pages we mistakenly ingested ran 0.014. The threshold sits an order of magnitude
below the good documents and seven times above the bad ones, so it is not a
finely tuned number and does not need to be.
"""

MINIMUM_SEGMENTS = 3
"""Fewer than three segments from a whole document means segmentation found
almost no structure, whatever the byte count says."""

MINIMUM_TEXT_CHARACTERS = 500
"""A document too short to answer anything, regardless of ratio."""

CLIENT_RENDER_MARKERS = (
    "there was an error while loading",
    "you need to enable javascript",
    "please enable javascript",
    "enable javascript to run this app",
    "this page requires javascript",
)
"""Strings a page serves when its real content never arrived.

Literal markers rather than a heuristic: these are the exact phrases that were
sitting in the corpus, checksummed and pinned, while we believed we had a
community discussion archive.
"""

MARKUP_PARSERS = frozenset({Parser.EURLEX_HTML, Parser.GENERIC_HTML})
"""Parsers whose input is markup, so a text-to-markup ratio is meaningful.
JSON assembled from an API has no markup to compare against."""


def check_document(source: Source, raw: bytes, segments: Sequence[Segment]) -> list[Problem]:
    """Everything wrong with what this fetch actually produced."""
    problems: list[Problem] = []
    text = "".join(segment.text for segment in segments)

    for marker in CLIENT_RENDER_MARKERS:
        if marker in text.lower():
            problems.append(
                Problem(
                    Severity.ERROR,
                    "client-rendered",
                    f"document contains {marker!r}: the page renders its content in the "
                    "browser, so a static fetch captured chrome instead. Fetch this source "
                    "from an API or a raw file (ADR-0009).",
                )
            )
            break

    if len(segments) < MINIMUM_SEGMENTS:
        problems.append(
            Problem(
                Severity.ERROR,
                "too-few-segments",
                f"{len(segments)} segments, expected at least {MINIMUM_SEGMENTS}",
            )
        )

    if len(text) < MINIMUM_TEXT_CHARACTERS:
        problems.append(
            Problem(
                Severity.ERROR,
                "too-little-text",
                f"{len(text)} characters of extracted text, "
                f"expected at least {MINIMUM_TEXT_CHARACTERS}",
            )
        )

    if source.parser in MARKUP_PARSERS and raw:
        ratio = len(html_to_lines_text(raw)) / len(raw)
        if ratio < MINIMUM_TEXT_RATIO:
            problems.append(
                Problem(
                    Severity.ERROR,
                    "markup-without-text",
                    f"text-to-markup ratio {ratio:.4f} is below {MINIMUM_TEXT_RATIO}: "
                    f"{len(raw):,} bytes of markup yielded almost no text",
                )
            )

    return problems


def html_to_lines_text(raw: bytes) -> str:
    return "".join(html_to_lines(decode(raw)))
