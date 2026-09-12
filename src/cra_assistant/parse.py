"""Turning fetched bytes into text blocks, and text blocks into legal structure.

Two deliberate choices, both recorded in ADR-0004.

**Structure comes from text markers, not from HTML classes.** EUR-Lex marks
recitals with ``class="eli-subdivision" id="rct_1"`` and articles with
``class="oj-ti-art"``, which would be easier to match and is exactly why we do
not: those class names belong to one Official Journal generation and have
changed before. The line ``Article 13`` is the regulation's own wording and will
outlive the markup.

**Blocks come from block-level elements only.** Inline elements must not break a
line, because EUR-Lex renders footnote references as
``<a>(<span>1</span>)</a>`` inside a paragraph. Splitting on every tag would
turn that into a line reading ``(1)`` — indistinguishable from a recital number.
"""

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)  # fmt: skip
"""Elements that end the current line. Everything else is inline and does not."""

HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

IGNORED_CONTENT_TAGS = frozenset({"script", "style", "noscript", "template"})

VOID_TAGS = frozenset({"br", "hr", "img", "input", "link", "meta", "col"})


@dataclass(frozen=True, slots=True)
class Block:
    """One block-level run of text, with the element that produced it."""

    tag: str
    text: str

    @property
    def is_heading(self) -> bool:
        return self.tag in HEADING_TAGS


def normalise(text: str) -> str:
    """Collapse whitespace, including the non-breaking spaces EUR-Lex is full of.

    EUR-Lex writes "Article", U+00A0, "13". That must compare equal to plain
    ``Article 13``, or every marker pattern would need to spell out the
    alternative.
    """
    without_nbsp = "".join(
        " " if unicodedata.category(character) == "Zs" else character for character in text
    )
    return re.sub(r"\s+", " ", without_nbsp).strip()


class _BlockExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._buffer: list[str] = []
        self._open_blocks: list[str] = ["body"]
        self._ignore_depth = 0

    def _flush(self, tag: str) -> None:
        text = normalise("".join(self._buffer))
        self._buffer.clear()
        if text:
            self.blocks.append(Block(tag=tag, text=text))

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in IGNORED_CONTENT_TAGS:
            self._ignore_depth += 1
            return
        if tag in BLOCK_TAGS:
            self._flush(self._open_blocks[-1])
            if tag not in VOID_TAGS:
                self._open_blocks.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in IGNORED_CONTENT_TAGS:
            self._ignore_depth = max(0, self._ignore_depth - 1)
            return
        if tag in BLOCK_TAGS and tag not in VOID_TAGS:
            self._flush(tag)
            if tag in self._open_blocks:
                # Close back to the matching tag; EUR-Lex nests tables deeply and
                # unclosed <td> elements are common enough to matter.
                while self._open_blocks[-1] != tag and len(self._open_blocks) > 1:
                    self._open_blocks.pop()
                if len(self._open_blocks) > 1:
                    self._open_blocks.pop()

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0:
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush(self._open_blocks[-1])


def html_to_blocks(markup: str) -> list[Block]:
    """Text blocks in document order, tagged with the element that produced them."""
    parser = _BlockExtractor()
    parser.feed(markup)
    parser.close()
    return parser.blocks


def html_to_lines(markup: str) -> list[str]:
    """Text of each block, in document order. Structure detection works on these."""
    return [block.text for block in html_to_blocks(markup)]


def decode(raw: bytes) -> str:
    """Decode fetched bytes, preferring the charset the document declares."""
    match = re.search(rb'charset=["\']?([\w-]+)', raw[:4096], re.IGNORECASE)
    if match:
        try:
            return raw.decode(match.group(1).decode("ascii"), errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """The words one language uses for the structures we segment on.

    Everything here is the regulation's own vocabulary. Adding a language means
    adding a profile, not touching the segmentation logic.
    """

    lang: str
    recitals_begin: tuple[str, ...]
    """Line that introduces the recitals, lowercased, matched exactly."""
    enacting_begin: tuple[str, ...]
    """Line that ends the recitals and begins the enacting terms, lowercased prefix."""
    signature_begin: tuple[str, ...]
    """Line that closes the enacting terms. Everything from here to the first
    annex is signatures and the footnote apparatus, not article text — without
    it all 38 footnotes are swept into the last article."""
    document_end: tuple[str, ...]
    """Where the Official Journal's own furniture starts: the ELI link, the ISSN
    line and the editorial statement. Without it they land in the last annex."""
    article: re.Pattern[str]
    annex: re.Pattern[str]
    recital_label: str
    article_label: str
    annex_label: str


ROMAN = "IVXLCDM"

ENGLISH = LanguageProfile(
    lang="en",
    recitals_begin=("whereas:",),
    enacting_begin=("have adopted this regulation",),
    signature_begin=("done at ",),
    document_end=("a statement has been made", "eli: ", "issn "),
    article=re.compile(r"^Article\s+(\d+[a-z]?)$"),
    annex=re.compile(rf"^ANNEX\s+([{ROMAN}]+)$"),
    recital_label="Recital",
    article_label="Article",
    annex_label="Annex",
)

GERMAN = LanguageProfile(
    lang="de",
    recitals_begin=("in erwägung nachstehender gründe:",),
    enacting_begin=("haben folgende verordnung erlassen",),
    signature_begin=("geschehen zu ",),
    document_end=("zu diesem rechtsakt wurde", "eli: ", "issn "),
    article=re.compile(r"^Artikel\s+(\d+[a-z]?)$"),
    annex=re.compile(rf"^ANHANG\s+([{ROMAN}]+)$"),
    recital_label="Erwägungsgrund",
    article_label="Artikel",
    annex_label="Anhang",
)

PROFILES: dict[str, LanguageProfile] = {profile.lang: profile for profile in (ENGLISH, GERMAN)}

RECITAL_NUMBER = re.compile(r"^\((\d+)\)$")
"""A recital number occupies its own block. Inside an article the same shape is
a footnote reference, which is why this is only applied in the recitals region.
"""


class UnsupportedLanguageError(LookupError):
    """No profile for this language. Better than silently segmenting nothing."""


def profile_for(lang: str) -> LanguageProfile:
    try:
        return PROFILES[lang]
    except KeyError:
        supported = ", ".join(sorted(PROFILES))
        raise UnsupportedLanguageError(
            f"no language profile for {lang!r}; have {supported}"
        ) from None
