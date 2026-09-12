"""The golden set: what "correct" means, as committed data.

Same shape of decision as the source registry (ADR-0002). A gold label defines
what a right answer is, so changing one must be as visible in a pull request as
changing a trusted source, and as strictly validated.

Nothing here knows about retrieval. Loading and validating the expectations is
separate from measuring anything against them.
"""

import re
import tomllib
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cra_assistant.paths import REPO_ROOT

DEFAULT_GOLDEN_PATH = REPO_ROOT / "eval" / "golden.toml"

SEGMENT_ID_PATTERN = r"^[a-z0-9-]+:(recital|article|annex|section):[A-Za-z0-9.-]+$"


class Vocabulary(StrEnum):
    """How the question is phrased.

    The slice that matters most: a system that only answers questions asked in
    the regulation's own words is a search box for people who already know the
    answer.
    """

    STATUTE = "statute"
    PRACTITIONER = "practitioner"


class AnswerType(StrEnum):
    ANSWERABLE = "answerable"
    """The trusted corpus answers it."""

    UNANSWERABLE = "unanswerable"
    """Nothing answers it. Abstention is the correct outcome, not a failure."""

    UNTRUSTED_ONLY = "untrusted_only"
    """Only an untrusted source answers it. Retrievable, quotable, never
    authoritative."""


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=3, max_length=64)
    question: str = Field(min_length=5)
    lang: str = Field(pattern=r"^[a-z]{2}$")
    vocabulary: Vocabulary
    answer_type: AnswerType
    expected_segment_ids: tuple[str, ...] = ()
    note: str = ""
    verified: bool = False
    """A human has checked by hand that the labels answer the question.

    Defaults to false so that drafting cannot silently produce authority. The
    evaluator refuses unverified items unless explicitly told otherwise.
    """

    @model_validator(mode="after")
    def _labels_must_match_the_answer_type(self) -> Self:
        if self.answer_type is AnswerType.UNANSWERABLE and self.expected_segment_ids:
            raise ValueError("an unanswerable item must have no expected_segment_ids")
        if self.answer_type is not AnswerType.UNANSWERABLE and not self.expected_segment_ids:
            raise ValueError(f"a {self.answer_type} item must name at least one expected segment")
        return self

    @model_validator(mode="after")
    def _expected_ids_must_look_like_segment_ids(self) -> Self:
        bad = [one for one in self.expected_segment_ids if not re.match(SEGMENT_ID_PATTERN, one)]
        if bad:
            raise ValueError(f"not segment ids: {', '.join(bad)}")
        return self


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[GoldenItem, ...]

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if not self.items:
            raise ValueError("the golden set is empty")
        counts = Counter(item.id for item in self.items)
        duplicates = sorted(item_id for item_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate item ids: {', '.join(duplicates)}")
        return self

    @property
    def verified(self) -> tuple[GoldenItem, ...]:
        return tuple(item for item in self.items if item.verified)

    @property
    def unverified(self) -> tuple[GoldenItem, ...]:
        return tuple(item for item in self.items if not item.verified)


def load_golden_set(path: Path = DEFAULT_GOLDEN_PATH) -> GoldenSet:
    """Read and validate the golden set. Errors name the offending item."""
    with path.open("rb") as handle:
        return GoldenSet.model_validate(tomllib.load(handle))
