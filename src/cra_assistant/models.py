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
    url: HttpUrl
    lang: LanguageCode
    tier: TrustTier
    licence: str = Field(
        min_length=1,
        description="SPDX identifier where one applies, otherwise a plain statement of the "
        "reuse terms. 'UNKNOWN' is an allowed and honest answer.",
    )
    parser: Parser
