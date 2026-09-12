"""A finding about a document, shared by the checks that produce them.

Lives on its own so that structural validation and ingest plausibility can both
report in the same shape without importing each other.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    """Wrong. Fails the command."""

    WARNING = "warning"
    """Suspicious, but legitimately possible. Never fails."""


@dataclass(frozen=True, slots=True)
class Problem:
    severity: Severity
    code: str
    message: str
    segment_id: str | None = None


def has_errors(problems: Iterable[Problem]) -> bool:
    return any(problem.severity is Severity.ERROR for problem in problems)
