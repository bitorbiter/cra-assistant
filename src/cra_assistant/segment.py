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
import json
import re
from collections.abc import Callable, Iterable, Sequence

from cra_assistant.models import (
    OPAQUE_UNTRUSTED_NUMBER,
    Parser,
    Segment,
    SegmentKind,
    Source,
    TrustTier,
)
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
    citation_override: str | None = None,
) -> Segment:
    text = "\n".join(part for part in ([title, *body] if title else list(body)) if part)
    if source.tier is TrustTier.UNTRUSTED and not OPAQUE_UNTRUSTED_NUMBER.fullmatch(number):
        raise ValueError(
            f"untrusted segment number {number!r} from {source.id} is not opaque; ids render "
            "outside the untrusted wrapper and must carry no source-chosen text (ADR-0017)"
        )
    return Segment(
        id=f"{source.citation_prefix}:{kind.value}:{number}",
        source_id=source.id,
        tier=source.tier,
        kind=kind,
        number=number,
        title=title,
        text=text,
        citation=citation_override or f"{source.short_title}, {label} {number}",
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

OPAQUE_ID_LENGTH = 12


def opaque_number(*locator: str, taken: set[str]) -> str:
    """A stable name for a section that spells none of it.

    A digest of *where* the section is — file path and heading — rather than of
    what it says. Two properties decide that:

    - **No source text.** Ids render outside the untrusted wrapper. Heading slugs
      put an attacker's words there, as lowercase ASCII but words all the same
      (ADR-0017). A digest carries nothing readable.
    - **Stability.** Positional ids renumbered on every upstream insertion. A digest
      of the body would rename a section on every typo fix, and gold labels and
      citations would rot the same way (ADR-0009). A digest of the location moves
      only when the file or heading does, exactly as the slug did.

    A repeated heading in the same file gets the next ordinal, so ids stay unique
    and the first occurrence keeps its name when a duplicate is added below it.
    """
    for ordinal in range(1000):
        material = "\x00".join([*locator, str(ordinal)]).encode("utf-8")
        candidate = hashlib.sha256(material).hexdigest()[:OPAQUE_ID_LENGTH]
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise ValueError("cannot disambiguate a section location")


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

    taken: set[str] = set()
    segments: list[Segment] = []
    for index, (title, body) in enumerate(sections):
        kept = [line for line in body if line.strip()]
        if not (title or kept):
            continue
        segments.append(
            _make_segment(
                source,
                kind=SegmentKind.SECTION,
                number=opaque_number(title, taken=taken),
                title=title,
                body=kept,
                label="section",
                order=index,
                source_sha256=source_sha256,
            )
        )
    return segments


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


def segment_github_issues(source: Source, raw: bytes) -> list[Segment]:
    """One segment per issue and per comment, named by its GitHub id.

    ``issue-137`` and ``issue-137-comment-2574583778`` are the identifiers
    GitHub itself uses, so a citation survives new issues being opened, issues
    being closed, and comments being added anywhere else in the repository.
    """
    payload = json.loads(raw.decode("utf-8"))
    source_sha256 = sha256_of_bytes(raw)
    segments: list[Segment] = []
    order = 0

    for issue in payload.get("issues", []):
        body = str(issue.get("body") or "").strip()
        title = str(issue.get("title") or "").strip()
        if not (body or title):
            continue
        segments.append(
            _make_segment(
                source,
                kind=SegmentKind.SECTION,
                number=f"issue-{issue['number']}",
                title=title,
                body=body.splitlines(),
                label=f"issue #{issue['number']}",
                order=order,
                source_sha256=source_sha256,
                citation_override=f"{source.short_title}, issue #{issue['number']}",
            )
        )
        order += 1

    for comment in payload.get("comments", []):
        body = str(comment.get("body") or "").strip()
        if not body:
            continue
        number = f"issue-{comment['issue_number']}-comment-{comment['id']}"
        segments.append(
            _make_segment(
                source,
                kind=SegmentKind.SECTION,
                number=number,
                title="",
                body=body.splitlines(),
                label="comment",
                order=order,
                source_sha256=source_sha256,
                citation_override=(
                    f"{source.short_title}, comment on issue #{comment['issue_number']}"
                ),
            )
        )
        order += 1

    return segments


def segment_github_markdown_tree(source: Source, raw: bytes) -> list[Segment]:
    """One segment per heading, named by file path plus heading slug.

    ``stewards-obligations-what-must-a-steward-do`` moves only when the file or
    the heading moves, which is what makes a citation into a community document
    worth writing down.
    """
    payload = json.loads(raw.decode("utf-8"))
    source_sha256 = sha256_of_bytes(raw)
    prefix = str(payload.get("prefix") or "")

    taken: set[str] = set()
    segments: list[Segment] = []
    order = 0

    for entry in payload.get("files", []):
        path = str(entry.get("path") or "")
        relative = path[len(prefix) :].strip("/") if path.startswith(prefix) else path

        for title, body in _markdown_sections(str(entry.get("text") or "")):
            kept = [line for line in body if line.strip()]
            if not (title or kept):
                continue
            segments.append(
                _make_segment(
                    source,
                    kind=SegmentKind.SECTION,
                    number=opaque_number(relative, title, taken=taken),
                    title=title,
                    body=kept,
                    label="section",
                    order=order,
                    source_sha256=source_sha256,
                    citation_override=f"{source.short_title}, {relative}"
                    + (f" — {title}" if title else ""),
                )
            )
            order += 1

    return segments


def _markdown_sections(text: str) -> list[tuple[str, list[str]]]:
    """Split Markdown at ATX headings, keeping any text before the first one."""
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
    return sections


Segmenter = Callable[[Source, bytes], list[Segment]]

SEGMENTERS: dict[Parser, Segmenter] = {
    Parser.EURLEX_HTML: segment_eurlex_html,
    Parser.MARKDOWN: segment_markdown,
    Parser.GENERIC_HTML: segment_generic_html,
    Parser.GITHUB_ISSUES: segment_github_issues,
    Parser.GITHUB_MARKDOWN_TREE: segment_github_markdown_tree,
}
"""Every declared parser must appear here; a test asserts it."""


def segment_document(source: Source, raw: bytes) -> list[Segment]:
    """Segment ``raw`` according to the parser the source declares."""
    return SEGMENTERS[source.parser](source, raw)
