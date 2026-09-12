"""Drift detection: comparing what we fetched against what a human has approved.

``registry/pins.toml`` records, per source, the checksum someone has actually
looked at and a one-line note saying what they concluded. Verification compares
the newest observation in the manifest against that pin.

Two things this module deliberately is not:

* It is not part of fetching. Fetch records; verify judges. A source whose
  upstream changed must still download.
* It is not a gate yet. :data:`GATE_ENABLED` is ``False`` and
  :func:`exit_code_for` returns success regardless of what was found. See
  ADR-0003 for why, and for the condition that arms it.
"""

import tomllib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cra_assistant.manifest import Checksum, FetchObservation, latest_by_source
from cra_assistant.models import Source, SourceId, TrustTier
from cra_assistant.paths import DEFAULT_PINS_PATH

GATE_ENABLED = False
"""Whether drift should fail the process.

``False`` this step. A checksum over raw bytes of an EUR-Lex page drifts on
almost every fetch because of the page shell — session ids, banners, build
stamps — and not because a word of the legal text moved. A gate that cries wolf
on every run teaches people to pass ``--force``, which is worse than no gate.
This arms when the pin is over parser-extracted text rather than raw bytes.
"""


class Pin(BaseModel):
    """A checksum a human has seen, with what they concluded about it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    checksum: Checksum
    note: str = Field(
        min_length=1,
        description="Why this checksum is the approved one. Required: an "
        "acknowledgement with no reasoning attached is just a rubber stamp.",
    )


class PinFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pins: tuple[Pin, ...] = ()

    @model_validator(mode="after")
    def _source_ids_must_be_unique(self) -> Self:
        counts = Counter(pin.source_id for pin in self.pins)
        duplicates = sorted(source_id for source_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate pinned source ids: {', '.join(duplicates)}")
        return self

    def by_source(self) -> dict[str, Pin]:
        return {pin.source_id: pin for pin in self.pins}


def load_pins(path: Path = DEFAULT_PINS_PATH) -> PinFile:
    """Read and validate the pin file. A missing file pins nothing."""
    if not path.exists():
        return PinFile()
    with path.open("rb") as handle:
        return PinFile.model_validate(tomllib.load(handle))


class DriftStatus(StrEnum):
    CLEAN = "clean"
    """Newest observation matches the pin."""

    DRIFTED = "drifted"
    """Newest observation differs from the pin."""

    UNPINNED = "unpinned"
    """Fetched, but nobody has approved a checksum for it yet."""

    UNFETCHED = "unfetched"
    """Declared in the registry, absent from the manifest."""


@dataclass(frozen=True, slots=True)
class SourceVerdict:
    source_id: str
    tier: TrustTier
    status: DriftStatus
    pinned_checksum: str | None
    observed_checksum: str | None

    @property
    def needs_acknowledgement(self) -> bool:
        """Whether a human has to do something about this.

        Tier decides. Drift on a trusted source means a document we allow to
        influence the model's behaviour changed underneath us, and someone has
        to look and say so in the pin file. Drift on an untrusted source is the
        normal condition of a forum thread: recorded, not escalated.
        """
        if self.tier is not TrustTier.TRUSTED:
            return False
        return self.status in {DriftStatus.DRIFTED, DriftStatus.UNPINNED}


def verify(
    sources: Iterable[Source],
    observations: Iterable[FetchObservation],
    pin_file: PinFile,
) -> tuple[SourceVerdict, ...]:
    """Compare the newest observation per source against its pin."""
    latest = latest_by_source(observations)
    pins = pin_file.by_source()

    verdicts = []
    for source in sources:
        observation = latest.get(source.id)
        pin = pins.get(source.id)
        observed = observation.checksum if observation else None
        pinned = pin.checksum if pin else None

        if observation is None:
            status = DriftStatus.UNFETCHED
        elif pin is None:
            status = DriftStatus.UNPINNED
        elif pin.checksum == observation.checksum:
            status = DriftStatus.CLEAN
        else:
            status = DriftStatus.DRIFTED

        verdicts.append(
            SourceVerdict(
                source_id=source.id,
                tier=source.tier,
                status=status,
                pinned_checksum=pinned,
                observed_checksum=observed,
            )
        )
    return tuple(verdicts)


def exit_code_for(verdicts: Sequence[SourceVerdict]) -> int:
    """Always ``0`` while :data:`GATE_ENABLED` is false, whatever was found."""
    if not GATE_ENABLED:
        return 0
    return 1 if any(verdict.needs_acknowledgement for verdict in verdicts) else 0
