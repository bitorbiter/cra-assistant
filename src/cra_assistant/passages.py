"""Splitting a segment into the passages that retrieval actually scores.

A segment is a citable unit: an article, a recital, an annex, a community post.
That is the right unit for a citation and the wrong unit for BM25. Article 13 is
15,386 characters, so length normalisation buried it at rank 223 for a question
that is its own title, and prompt assembly could only deliver its first 4,000
characters anyway.

A passage is a numbered paragraph of an article, a point of an annex, or a whole
recital, and it knows the segment it came from. Retrieval scores passages; the
answer still cites the article (ADR-0018). Splitting follows the document's own
markers, never a fixed-size window, for the reason ADR-0004 gives: a window has
no name a reader can verify.
"""

import re
from dataclasses import dataclass

from cra_assistant.models import Segment, SegmentKind, TrustTier

MINIMUM_PASSAGE_CHARACTERS = 200
"""Below this a passage is merged into the one before it.

Annexes put their markers on their own line — "(a)" alone — and a list of
one-line points would otherwise become a list of passages too short to rank on
anything but coincidence."""

MAXIMUM_PASSAGE_CHARACTERS = 2000
"""Above this a passage is cut at a line boundary.

A cap rather than a target: the point is that no passage approaches the prompt's
4,000-character clip, so what retrieval scored is what the model reads."""

MARKER = re.compile(
    r"""^\s*(?:
        \(\d{1,3}\)            # (1)  — German articles, annex points
      | \d{1,3}\.(?:\s|$)      # 1.   — English articles, annex points
      | \([a-z]\)              # (a)  — lettered sub-points
      | Part\s+[IVXLC]+\b      # Part II — annex divisions
      | (?:ANNEX|Annex)\s+[IVXLC]+\b
    )""",
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class Passage:
    """One scored unit of retrieval, and the segment it is part of.

    Exposes the identifying fields of its segment, so everything downstream —
    citation enforcement, the judge, the evaluator, the report — keeps naming
    articles rather than fragments of them.
    """

    segment: Segment
    text: str
    ordinal: int
    """Position within the segment, from 0. Not part of any citation."""

    @property
    def id(self) -> str:
        return self.segment.id

    @property
    def source_id(self) -> str:
        return self.segment.source_id

    @property
    def tier(self) -> TrustTier:
        return self.segment.tier

    @property
    def citation(self) -> str:
        return self.segment.citation

    @property
    def title(self) -> str:
        return self.segment.title

    @property
    def lang(self) -> str:
        return self.segment.lang

    @property
    def number(self) -> str:
        return self.segment.number

    @property
    def kind(self) -> SegmentKind:
        return self.segment.kind

    @property
    def whole_segment(self) -> bool:
        return self.text == self.segment.text

    @property
    def full_text(self) -> str:
        """The whole segment this passage came from.

        Used to tell "quoted text the model was never shown" from "quoted text
        that is nowhere in the source" — the first is a clipping or passage
        boundary, the second is an invention."""
        return self.segment.text


def whole(segment: Segment) -> Passage:
    """A segment as a single passage, for callers that hold a segment."""
    return Passage(segment=segment, text=segment.text, ordinal=0)


def _grouped_lines(text: str) -> list[list[str]]:
    """Lines gathered into marker-led groups, keeping the document's own order."""
    groups: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if MARKER.match(line) or not groups:
            groups.append([line])
        else:
            groups[-1].append(line)
    return groups


def _merge_short(groups: list[list[str]]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        block = "\n".join(group)
        if merged and len(merged[-1]) < MINIMUM_PASSAGE_CHARACTERS:
            merged[-1] = f"{merged[-1]}\n{block}"
        else:
            merged.append(block)
    return merged


def _cap(block: str) -> list[str]:
    """Cut an over-long block at line boundaries, never mid-line."""
    if len(block) <= MAXIMUM_PASSAGE_CHARACTERS:
        return [block]
    out: list[str] = []
    current: list[str] = []
    length = 0
    for line in block.splitlines():
        if current and length + len(line) > MAXIMUM_PASSAGE_CHARACTERS:
            out.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        out.append("\n".join(current))
    return out


def split(segment: Segment) -> list[Passage]:
    """The passages of one segment, in document order.

    A segment with no internal markers — most recitals, most community posts —
    yields itself, so short sources are unaffected by this machinery.
    """
    blocks = [part for block in _merge_short(_grouped_lines(segment.text)) for part in _cap(block)]
    if not blocks:
        return [Passage(segment=segment, text=segment.text, ordinal=0)]
    if len(blocks) == 1:
        return [Passage(segment=segment, text=segment.text, ordinal=0)]
    return [
        Passage(segment=segment, text=block, ordinal=index) for index, block in enumerate(blocks)
    ]


def split_all(segments: list[Segment]) -> list[Passage]:
    return [passage for segment in segments for passage in split(segment)]
