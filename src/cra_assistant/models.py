"""Declarative description of a corpus source.

A :class:`Source` says what a document is, where it comes from and who is
allowed to write it. It deliberately says nothing about any particular fetch of
it: checksums, retrieval timestamps and byte counts are facts about a download,
not properties of a source, and belong in the manifest written when the corpus
is actually fetched. ``extra="forbid"`` enforces that mechanically — a registry
entry that grows a ``checksum`` key fails validation rather than being quietly
ignored.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class TrustTier(StrEnum):
    """Who may write a source, and therefore what authority its text may carry.

    This describes **write access, not quality**. An untrusted source can be
    accurate, well argued and more useful than the regulation's own wording; it
    is untrusted because anyone can edit it, so its text must never be able to
    steer the system's behaviour.

    The tier is a property of the source. It is materialised onto every document
    and segment at ingest, and nothing downstream may look it up from the
    registry at query time. A source that would contain both tiers is two
    sources.
    """

    TRUSTED = "trusted"
    """Curated, writable only by authorised parties. May carry instruction
    authority in prompts."""

    UNTRUSTED = "untrusted"
    """Writable by anyone. Must be encapsulated, must never trigger tool calls,
    must never be treated as instructions."""


class Parser(StrEnum):
    """Which parser turns this source's bytes into documents.

    Naming a parser here is a declaration, not an implementation: the parsers
    themselves arrive with the segmentation step. An unknown value fails
    validation, so a typo in the registry cannot silently select a default.
    """

    EURLEX_HTML = "eurlex-html"
    """EUR-Lex HTML export, which carries the article and recital structure."""

    GENERIC_HTML = "generic-html"
    """Ordinary web page with no reliable document structure."""

    MARKDOWN = "markdown"
    """Markdown fetched as-is, e.g. a raw file from a repository."""


SourceId = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        min_length=3,
        max_length=64,
        description="Stable kebab-case identifier. Appears in citations, so it does not change.",
    ),
]

CitationPrefix = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        min_length=2,
        max_length=32,
        description="Leading component of every segment id from this source, e.g. 'cra-en'. "
        "Appears in citations and must never change: a segment id is a permanent name.",
    ),
]

LanguageCode = Annotated[
    str,
    Field(
        pattern=r"^[a-z]{2}$",
        description="ISO 639-1 two-letter code. The CRA is authentic in every EU language.",
    ),
]


class Source(BaseModel):
    """One retrievable document, declared in the registry and nowhere else."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: SourceId
    title: str = Field(min_length=1)
    short_title: str = Field(
        min_length=1,
        description="How this work is named in a citation, e.g. 'Regulation (EU) 2024/2847'.",
    )
    citation_prefix: CitationPrefix
    url: HttpUrl
    lang: LanguageCode
    tier: TrustTier
    licence: str = Field(
        min_length=1,
        description="SPDX identifier where one applies, otherwise a plain statement of the "
        "reuse terms. 'UNKNOWN' is an allowed and honest answer.",
    )
    parser: Parser


class SegmentKind(StrEnum):
    """The structural unit a segment corresponds to.

    The first three are the regulation's own divisions. ``SECTION`` is for
    sources that have no legal structure — a FAQ, a forum page — where the best
    honest answer is "a part of a document".
    """

    RECITAL = "recital"
    ARTICLE = "article"
    ANNEX = "annex"
    SECTION = "section"


class Segment(BaseModel):
    """One citable unit of text, carrying everything needed to cite and trust it.

    Trust tier and provenance are *materialised* here, not looked up from the
    registry at query time (ADR-0001). A segment that reaches prompt assembly
    either states which tier it came from or does not exist.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        pattern=r"^[a-z0-9-]+:(recital|article|annex|section):[A-Za-z0-9.-]+$",
        description="Stable name, e.g. 'cra-de:article:13'. Never encodes a version: a "
        "corrigendum changes what a segment says, never what it is called (ADR-0005).",
    )
    source_id: SourceId
    tier: TrustTier
    kind: SegmentKind
    number: str = Field(min_length=1, description="'13', '1', or 'I' for annexes.")
    title: str = ""
    text: str = Field(min_length=1)
    citation: str = Field(
        min_length=1, description="Human-readable, e.g. 'Regulation (EU) 2024/2847, Article 13'."
    )
    text_version: tuple[str, ...] = Field(
        default=(),
        description="CELEX ids of the corrigenda applied to this text, in order. Empty "
        "means the text is as originally published; corrigendum patching is not built "
        "yet (ADR-0005).",
    )
    source_sha256: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$",
        description="Digest of the raw bytes this segment was extracted from.",
    )
    content_sha256: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$",
        description="Digest of this segment's extracted text. Stable across page "
        "furniture, which is what makes a drift gate possible.",
    )
    lang: LanguageCode
    order: int = Field(ge=0, description="Position in the document.")
