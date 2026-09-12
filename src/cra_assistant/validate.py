"""Checking that a segmented document looks like the document it claims to be.

These are structural checks, not quality judgements. They exist to catch the
failure mode that matters most for a citation system: markers that stopped
matching, so that a third of the regulation quietly went missing. A retrieval
service that has never heard of Article 47 will not say so — it will answer
from Article 46 instead.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cra_assistant.models import Parser, Segment, SegmentKind
from cra_assistant.segment import MINIMUM_INTERESTING_LENGTH

LEGAL_STRUCTURE_PARSERS = frozenset({Parser.EURLEX_HTML})
"""Parsers whose output must contain recitals, articles and annexes.

Which checks apply is a property of the document, not of segmentation in
general: demanding recitals from a community FAQ would report three errors
about a file that is perfectly fine.
"""

ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(numeral: str) -> int | None:
    """Parse a Roman numeral, or ``None`` if it is not one."""
    if not numeral or any(character not in ROMAN_VALUES for character in numeral):
        return None
    total = 0
    previous = 0
    for character in reversed(numeral):
        value = ROMAN_VALUES[character]
        total += -value if value < previous else value
        previous = max(previous, value)
    return total


class Severity(StrEnum):
    ERROR = "error"
    """Structurally wrong: something is missing, duplicated or out of order."""

    WARNING = "warning"
    """Suspicious, but legitimately possible. Never fails the command."""


@dataclass(frozen=True, slots=True)
class Problem:
    severity: Severity
    code: str
    message: str
    segment_id: str | None = None


def validate_segments(segments: Sequence[Segment], parser: Parser) -> list[Problem]:
    """Every structural complaint about one document's segments.

    ``parser`` selects which checks apply: only a legal instrument is required
    to have recitals, articles and annexes.
    """
    problems: list[Problem] = []
    if not segments:
        return [Problem(Severity.ERROR, "empty", "no segments were produced")]

    problems.extend(_duplicate_ids(segments))
    if parser in LEGAL_STRUCTURE_PARSERS:
        problems.extend(_contiguous(segments, SegmentKind.RECITAL))
        problems.extend(_contiguous(segments, SegmentKind.ARTICLE))
        problems.extend(_annexes_ordered(segments))
    problems.extend(_short_segments(segments))
    return problems


def _duplicate_ids(segments: Iterable[Segment]) -> list[Problem]:
    counts = Counter(segment.id for segment in segments)
    return [
        Problem(Severity.ERROR, "duplicate-id", f"segment id appears {count} times", segment_id)
        for segment_id, count in sorted(counts.items())
        if count > 1
    ]


def _contiguous(segments: Sequence[Segment], kind: SegmentKind) -> list[Problem]:
    """Numbered divisions must run 1, 2, 3 … with nothing missing.

    A gap means a marker stopped matching. Reporting the gap is the point; the
    fix is always the marker, never a looser check.
    """
    numbers = [segment.number for segment in segments if segment.kind is kind]
    if not numbers:
        return [Problem(Severity.ERROR, f"no-{kind.value}s", f"no {kind.value} segments found")]

    parsed: list[int] = []
    problems: list[Problem] = []
    for number in numbers:
        if number.isdigit():
            parsed.append(int(number))
        else:
            problems.append(
                Problem(
                    Severity.ERROR, "non-numeric", f"{kind.value} number {number!r} is not a number"
                )
            )
    if not parsed:
        return problems

    expected = list(range(1, len(parsed) + 1))
    if parsed != expected:
        missing = sorted(set(expected) - set(parsed))
        unexpected = sorted(set(parsed) - set(expected))
        problems.append(
            Problem(
                Severity.ERROR,
                f"{kind.value}-sequence",
                f"{len(parsed)} {kind.value}s do not run 1..{len(parsed)}: "
                f"missing {missing or 'none'}, unexpected {unexpected or 'none'}",
            )
        )
    return problems


def _annexes_ordered(segments: Sequence[Segment]) -> list[Problem]:
    annexes = [segment for segment in segments if segment.kind is SegmentKind.ANNEX]
    if not annexes:
        return [Problem(Severity.ERROR, "no-annexes", "no annex segments found")]

    problems: list[Problem] = []
    values: list[int] = []
    for annex in annexes:
        value = roman_to_int(annex.number)
        if value is None:
            problems.append(
                Problem(
                    Severity.ERROR,
                    "annex-numeral",
                    f"{annex.number!r} is not a Roman numeral",
                    annex.id,
                )
            )
        else:
            values.append(value)

    if values and values != sorted(values):
        problems.append(
            Problem(Severity.ERROR, "annex-order", f"annexes are out of order: {values}")
        )
    if values and values != list(range(1, len(values) + 1)):
        problems.append(
            Problem(
                Severity.ERROR, "annex-sequence", f"annexes do not run I..{len(values)}: {values}"
            )
        )
    return problems


def _short_segments(segments: Iterable[Segment]) -> list[Problem]:
    """Flag, never reject.

    Four articles of the CRA genuinely are a single sentence, so a length floor
    cannot be a hard rule without being wrong about real text.
    """
    return [
        Problem(
            Severity.WARNING,
            "short-segment",
            f"only {len(segment.text)} characters; check the markers did not truncate it",
            segment.id,
        )
        for segment in segments
        if len(segment.text) < MINIMUM_INTERESTING_LENGTH
    ]


def has_errors(problems: Iterable[Problem]) -> bool:
    return any(problem.severity is Severity.ERROR for problem in problems)
