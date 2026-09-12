"""Cutting a document into citable segments along its own structure.

One segmenter per declared :class:`~cra_assistant.models.Parser`, registered in
:data:`SEGMENTERS`. Every segmenter takes the same arguments — a source and the
raw bytes — and returns segments carrying the source's trust tier. Nothing
downstream looks a tier up (ADR-0001).

That uniform signature is also the seam for local files: a source whose bytes
came off disk rather than off the network segments through exactly this path,
because nothing here knows or asks where the bytes came from.
"""

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence

from cra_assistant.models import Parser, Segment, SegmentKind, Source
from cra_assistant.parse import (
    RECITAL_NUMBER,
    LanguageProfile,
    decode,
    html_to_blocks,
    profile_for,
)

MINIMUM_INTERESTING_LENGTH = 200
"""Below this many characters a segment is suspicious rather than wrong. Some
articles genuinely are one sentence, so this flags for review, never rejects."""


def sha256_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def document_content_checksum(segments: Iterable[Segment]) -> str:
    """A digest of what a document *says*, independent of how it was served.

    Built from segment ids and their content digests rather than from the raw
    response, so it is unmoved by the analytics attributes and session ids that
    make a raw-byte checksum useless for change detection (ADR-0003). Ids are
    included, not just text, so that renaming or reordering a segment counts as
    a change.
    """
    joined = "\n".join(f"{segment.id} {segment.content_sha256}" for segment in segments)
    return sha256_of(joined)


def _make_segment(
    source: Source,
    *,
    kind: SegmentKind,
    number: str,
    title: str,
    body: Sequence[str],
    label: str,
    order: int,
    source_sha256: str,
) -> Segment:
    text = "\n".join(part for part in ([title, *body] if title else list(body)) if part)
    return Segment(
        id=f"{source.citation_prefix}:{kind.value}:{number}",
        source_id=source.id,
        tier=source.tier,
        kind=kind,
        number=number,
        title=title,
        text=text,
        citation=f"{source.short_title}, {label} {number}",
        text_version=(),
        source_sha256=source_sha256,
        content_sha256=sha256_of(text),
        lang=source.lang,
        order=order,
    )


def _is_structural(line: str, profile: LanguageProfile) -> bool:
    return bool(profile.article.match(line) or profile.annex.match(line))


def segment_eurlex_html(source: Source, raw: bytes) -> list[Segment]:
    """Segment an Official Journal HTML export into recitals, articles and annexes.

    Boundaries are the regulation's own headings. Recital numbers are only
    recognised between the ``Whereas:`` line and the first article, because the
    same ``(1)`` shape is a footnote reference everywhere else.
    """
    profile = profile_for(source.lang)
    lines = [block.text for block in html_to_blocks(decode(raw))]
    source_sha256 = sha256_of_bytes(raw)

    recitals_start = _find_line(lines, profile.recitals_begin)
    first_article = next(
        (index for index, line in enumerate(lines) if profile.article.match(line)), len(lines)
    )
    first_annex = next(
        (index for index, line in enumerate(lines) if profile.annex.match(line)), len(lines)
    )
    # The closing formula ends the articles. Past it lie signatures and the
    # footnote apparatus, which would otherwise all land in the last article.
    signature = _find_prefix(lines, profile.signature_begin, start=first_article)
    articles_end = min(first_annex, signature if signature is not None else len(lines))
    # Likewise the OJ footer, which would otherwise land in the last annex.
    footer = _find_prefix(lines, profile.document_end, start=first_annex)
    annexes_end = footer if footer is not None else len(lines)

    segments: list[Segment] = []
    order = 0

    if recitals_start is not None:
        for number, body in _split_recitals(lines[recitals_start + 1 : first_article]):
            segments.append(
                _make_segment(
                    source,
                    kind=SegmentKind.RECITAL,
                    number=number,
                    title="",
                    body=body,
                    label=profile.recital_label,
                    order=order,
                    source_sha256=source_sha256,
                )
            )
            order += 1

    for number, title, body in _split_headed(
        lines[first_article:articles_end], profile.article, profile
    ):
        segments.append(
            _make_segment(
                source,
                kind=SegmentKind.ARTICLE,
                number=number,
                title=title,
                body=body,
                label=profile.article_label,
                order=order,
                source_sha256=source_sha256,
            )
        )
        order += 1

    for number, title, body in _split_headed(
        lines[first_annex:annexes_end], profile.annex, profile
    ):
        segments.append(
            _make_segment(
                source,
                kind=SegmentKind.ANNEX,
                number=number,
                title=title,
                body=body,
                label=profile.annex_label,
                order=order,
                source_sha256=source_sha256,
            )
        )
        order += 1

    return segments


def _find_line(lines: Sequence[str], candidates: Sequence[str]) -> int | None:
    wanted = {candidate.lower() for candidate in candidates}
    for index, line in enumerate(lines):
        if line.lower() in wanted:
            return index
    return None


def _find_prefix(lines: Sequence[str], prefixes: Sequence[str], *, start: int = 0) -> int | None:
    lowered = tuple(prefix.lower() for prefix in prefixes)
    for index in range(start, len(lines)):
        if lines[index].lower().startswith(lowered):
            return index
    return None


def _split_recitals(lines: Sequence[str]) -> list[tuple[str, list[str]]]:
    recitals: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        match = RECITAL_NUMBER.match(line)
        if match:
            current = []
            recitals.append((match.group(1), current))
        elif current is not None:
            current.append(line)
    return [(number, body) for number, body in recitals if body]


def _split_headed(
    lines: Sequence[str], heading: re.Pattern[str], profile: LanguageProfile
) -> list[tuple[str, str, list[str]]]:
    """Split at headings matching ``heading``; the line after a heading is its title."""
    sections: list[tuple[str, str, list[str]]] = []
    current: list[str] | None = None
    expecting_title = False

    for line in lines:
        match = heading.match(line)
        if match:
            current = []
            sections.append((match.group(1), "", current))
            expecting_title = True
            continue
        if current is None:
            continue
        if expecting_title:
            expecting_title = False
            # A title is a short line that is not itself a heading of another kind.
            if not _is_structural(line, profile):
                number, _, body = sections[-1]
                sections[-1] = (number, line, body)
                continue
        current.append(line)

    return [(number, title, body) for number, title, body in sections if body or title]


HEADING_MARKDOWN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def segment_markdown(source: Source, raw: bytes) -> list[Segment]:
    """Split Markdown at ATX headings.

    Deliberately shallow. A community FAQ has headings and no legal structure,
    so the honest unit is "the part under this heading".
    """
    text = raw.decode("utf-8", errors="replace")
    source_sha256 = sha256_of_bytes(raw)

    sections: list[tuple[str, list[str]]] = []
    preamble: list[str] = []
    for line in text.splitlines():
        match = HEADING_MARKDOWN.match(line)
        if match:
            sections.append((match.group(2), []))
        elif sections:
            sections[-1][1].append(line.rstrip())
        elif line.strip():
            preamble.append(line.rstrip())

    if preamble:
        sections.insert(0, ("", preamble))

    return [
        _make_segment(
            source,
            kind=SegmentKind.SECTION,
            number=str(index + 1),
            title=title,
            body=[line for line in body if line.strip()],
            label="section",
            order=index,
            source_sha256=source_sha256,
        )
        for index, (title, body) in enumerate(sections)
        if title or any(line.strip() for line in body)
    ]


def segment_generic_html(source: Source, raw: bytes) -> list[Segment]:
    """Split an ordinary web page at its HTML headings.

    Also deliberately shallow, and weakest where the page has no headings: then
    the whole document is one segment. That is honest rather than good, and it
    is a real limitation for untrusted retrieval (ADR-0004). Inventing
    boundaries where the document has none is the thing structure-based
    segmentation exists to avoid.
    """
    blocks = html_to_blocks(decode(raw))
    source_sha256 = sha256_of_bytes(raw)

    sections: list[tuple[str, list[str]]] = []
    for block in blocks:
        if block.is_heading:
            sections.append((block.text, []))
        elif sections:
            sections[-1][1].append(block.text)
        else:
            sections.append(("", [block.text]))
            sections[-1][1].pop()
            sections[-1] = ("", [block.text])

    return [
        _make_segment(
            source,
            kind=SegmentKind.SECTION,
            number=str(index + 1),
            title=title,
            body=body,
            label="section",
            order=index,
            source_sha256=source_sha256,
        )
        for index, (title, body) in enumerate(_merge_untitled(sections))
        if title or body
    ]


def _merge_untitled(sections: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    """Fold consecutive heading-less runs into one segment each."""
    merged: list[tuple[str, list[str]]] = []
    for title, body in sections:
        if not title and merged and not merged[-1][0]:
            merged[-1][1].extend(body)
        else:
            merged.append((title, list(body)))
    return merged


Segmenter = Callable[[Source, bytes], list[Segment]]

SEGMENTERS: dict[Parser, Segmenter] = {
    Parser.EURLEX_HTML: segment_eurlex_html,
    Parser.MARKDOWN: segment_markdown,
    Parser.GENERIC_HTML: segment_generic_html,
}
"""Every declared parser must appear here; a test asserts it."""


def segment_document(source: Source, raw: bytes) -> list[Segment]:
    """Segment ``raw`` according to the parser the source declares."""
    return SEGMENTERS[source.parser](source, raw)
