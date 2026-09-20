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
      | \([a-z]\)              # (a)  — English lettered sub-points
      | [a-z]\)                # a)   — German lettered sub-points
      | (?:Part|Teil)\s+[IVXLC]+\b        # Part II / Teil II — annex divisions
      | (?:ANNEX|Annex|ANHANG|Anhang)\s+[IVXLC]+\b
    )""",
    re.VERBOSE,
)
"""Both language editions, because the corpus is EN and DE.

The German edition writes its divisions "Teil II" and its sub-points "a)" with
no opening parenthesis. Matching only the English spellings left the whole of
the German Annex I as nine passages to the English eighteen, and — worse —
carried Part I's heading onto Part II's requirements, so a vulnerability
handling duty was delivered under the title for product properties."""

HEADING = re.compile(
    r"^\s*(?:(?:Part|Teil)\s+[IVXLC]+\b|(?:ANNEX|Annex|ANHANG|Anhang)\s+[IVXLC]+\b)"
)
SUBPOINT = re.compile(r"^\s*(?:\([a-z]\)|[a-z]\))")

CONTEXT_CHARACTERS = 600
"""How much of a governing provision is carried onto the points beneath it.

A part heading or an introduction like "On the basis of the cybersecurity risk
assessment referred to in Article 13(2) and where applicable" is two lines. The
cap is a guard against a pathological parent, not a target."""


def _level(line: str) -> int:
    """How deep in the provision's own hierarchy a marker sits.

    0 is a part or annex heading, 1 a numbered paragraph or point, 2 a lettered
    sub-point. Lines with no marker belong to whatever opened the group.
    """
    if HEADING.match(line):
        return 0
    if SUBPOINT.match(line):
        return 2
    return 1


def _clip_context(block: str) -> str:
    """At most :data:`CONTEXT_CHARACTERS` of a parent, cut at a line boundary."""
    if len(block) <= CONTEXT_CHARACTERS:
        return block
    kept: list[str] = []
    length = 0
    for line in block.splitlines():
        if kept and length + len(line) > CONTEXT_CHARACTERS:
            break
        kept.append(line)
        length += len(line) + 1
    return "\n".join(kept)


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
    context: str = ""
    """The provisions this passage hangs off: its part heading and the
    introduction that governs it, verbatim and in document order.

    Annex I point (e) reads "protect the confidentiality of stored, transmitted
    or otherwise processed data". On its own that is an unconditional
    requirement. It is not one: it is governed by "(2) On the basis of the
    cybersecurity risk assessment referred to in Article 13(2) and where
    applicable", two lines above. Delivering the point without its condition
    delivers a different rule from the one the regulation states.

    Carried into what the model is shown, deliberately **not** into what BM25
    indexes: thirteen sub-points sharing one introduction would become thirteen
    near-identical documents, and a query matching the introduction would
    retrieve all of them ahead of anything else."""

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
    def delivered_text(self) -> str:
        """Context and body, which together are what the model must read."""
        return f"{self.context}\n{self.text}" if self.context else self.text

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


@dataclass(frozen=True, slots=True)
class _Group:
    """One marker-led block of lines, with its depth in the provision."""

    level: int
    lines: list[str]

    @property
    def block(self) -> str:
        return "\n".join(self.lines)


def _grouped_lines(text: str) -> list[_Group]:
    """Lines gathered into marker-led groups, keeping the document's own order."""
    groups: list[_Group] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if MARKER.match(line) or not groups:
            groups.append(_Group(level=_level(line) if groups else 0, lines=[line]))
        else:
            groups[-1].lines.append(line)
    return groups


def _with_context(groups: list[_Group]) -> list[tuple[str, str]]:
    """Each group paired with the governing provisions above it.

    The nearest preceding group at every shallower level, in document order: a
    lettered sub-point carries the numbered introduction it hangs off and the
    part heading above that.
    """
    out: list[tuple[str, str]] = []
    open_at: dict[int, str] = {}
    for group in groups:
        context = "\n".join(
            _clip_context(open_at[level]) for level in sorted(open_at) if level < group.level
        )
        out.append((context, group.block))
        open_at[group.level] = group.block
        for deeper in [level for level in open_at if level > group.level]:
            del open_at[deeper]
    return out


def _merge_short(pairs: list[tuple[str, str]], levels: list[int]) -> list[tuple[str, str]]:
    """Merge blocks too short to rank on anything but coincidence.

    A merged block keeps the context of the first group in it, and a heading
    never merges into what precedes it: gluing "Part II" onto the tail of Part I
    would put one part's requirements under the other's title.
    """
    merged: list[tuple[str, str]] = []
    for (context, block), level in zip(pairs, levels, strict=True):
        joinable = merged and len(merged[-1][1]) < MINIMUM_PASSAGE_CHARACTERS and level != 0
        if joinable:
            merged[-1] = (merged[-1][0], f"{merged[-1][1]}\n{block}")
        else:
            merged.append((context, block))
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
    groups = _grouped_lines(segment.text)
    blocks = [
        (context, part)
        for context, block in _merge_short(_with_context(groups), [one.level for one in groups])
        for part in _cap(block)
    ]
    if len(blocks) <= 1:
        return [Passage(segment=segment, text=segment.text, ordinal=0)]
    return [
        Passage(segment=segment, text=block, ordinal=index, context=context)
        for index, (context, block) in enumerate(blocks)
    ]


def split_all(segments: list[Segment]) -> list[Passage]:
    return [passage for segment in segments for passage in split(segment)]
