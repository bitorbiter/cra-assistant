"""Drift detection: comparing what we fetched against what a human has approved.

``registry/pins.toml`` records, per source, the checksum someone has actually
looked at and a one-line note saying what they concluded. Verification compares
the newest observation in the manifest against that pin.

Two things this module deliberately is not:

* It is not part of fetching. Fetch records; verify judges. A source whose
  upstream changed must still download.
* It is not one check but two. The **raw** checksum covers the bytes as served
  and is report-only forever: two EUR-Lex responses seconds apart already differ
  inside an analytics attribute. The **content** checksum covers the extracted
  segment text, is unmoved by page furniture, and *blocks* for trusted sources.

ADR-0003 set the policy; ADR-0004 built the extraction that makes the content
checksum possible, which is what armed the gate.
"""

import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cra_assistant.manifest import Checksum, FetchObservation, latest_by_source
from cra_assistant.models import Source, SourceId, TrustTier
from cra_assistant.paths import DEFAULT_PINS_PATH
from cra_assistant.segment import document_content_checksum, segment_document

GATE_ENABLED = True
"""Whether content drift on a trusted source should fail the process.

Armed once checksums were taken over extracted segment text rather than raw
bytes. The evidence that this is now safe: the two EUR-Lex responses whose raw
digests differ produce the *same* content checksum over all 209 segments.

Raw-byte drift is never blocking, at any tier. It stays in the report because
it is the cheap signal that something upstream moved at all.
"""


class Pin(BaseModel):
    """A checksum a human has seen, with what they concluded about it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    checksum: Checksum
    """Digest of the raw bytes. Informational: drift here never blocks."""
    content_checksum: Checksum | None = None
    """Digest over extracted segment text. Drift here blocks for trusted
    sources. ``None`` means nobody has approved the content yet, which is itself
    a blocking condition for a trusted source."""
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
    """Raw-byte comparison. Report-only at every tier."""
    content_status: DriftStatus
    """Extracted-text comparison. Blocking for trusted sources."""
    pinned_checksum: str | None
    observed_checksum: str | None
    pinned_content_checksum: str | None
    observed_content_checksum: str | None

    @property
    def blocking(self) -> bool:
        """Whether this verdict should fail the process.

        Tier decides, as it does everywhere else. Content drift on a trusted
        source means a document we allow to influence the model's behaviour
        actually changed what it says — not how it was served. Someone must read
        the change and record it in the pin file. The same drift on an untrusted
        source is the normal condition of a forum thread: recorded, never
        escalated.

        ``UNFETCHED`` is never blocking: a checkout with no ``data/`` cannot
        judge anything, and failing there would only punish a fresh clone.
        """
        if self.tier is not TrustTier.TRUSTED:
            return False
        return self.content_status in {DriftStatus.DRIFTED, DriftStatus.UNPINNED}

    @property
    def needs_acknowledgement(self) -> bool:
        """A human must edit the pin file. Identical to :attr:`blocking` today,
        kept separate because "someone should look" and "CI should stop" are
        different claims that may yet diverge."""
        return self.blocking


def _compare(observed: str | None, pinned: str | None) -> DriftStatus:
    if observed is None:
        return DriftStatus.UNFETCHED
    if pinned is None:
        return DriftStatus.UNPINNED
    return DriftStatus.CLEAN if observed == pinned else DriftStatus.DRIFTED


def verify(
    sources: Iterable[Source],
    observations: Iterable[FetchObservation],
    pin_file: PinFile,
    content_checksums: Mapping[str, str] | None = None,
) -> tuple[SourceVerdict, ...]:
    """Compare the newest observation per source against its pin.

    ``content_checksums`` maps source id to the digest of its extracted text.
    Supplying it is what makes the blocking half of the report meaningful;
    without it every content status is ``UNFETCHED`` and nothing blocks. It is
    passed in rather than computed here so that this function stays pure and
    the disk access lives in one obvious place.
    """
    latest = latest_by_source(observations)
    pins = pin_file.by_source()
    content = content_checksums or {}

    verdicts = []
    for source in sources:
        observation = latest.get(source.id)
        pin = pins.get(source.id)
        observed = observation.checksum if observation else None
        pinned = pin.checksum if pin else None
        observed_content = content.get(source.id)
        pinned_content = pin.content_checksum if pin else None

        verdicts.append(
            SourceVerdict(
                source_id=source.id,
                tier=source.tier,
                status=_compare(observed, pinned),
                content_status=_compare(observed_content, pinned_content),
                pinned_checksum=pinned,
                observed_checksum=observed,
                pinned_content_checksum=pinned_content,
                observed_content_checksum=observed_content,
            )
        )
    return tuple(verdicts)


def observed_content_checksums(
    sources: Iterable[Source],
    observations: Iterable[FetchObservation],
    data_root: Path,
) -> dict[str, str]:
    """Segment the newest stored copy of each source and digest its text.

    Deliberately re-derived from the stored bytes on every run rather than
    cached in a file: an intermediate artefact could go stale against the
    segmenter, and a stale content checksum is exactly the failure this gate
    exists to catch.
    """
    latest = latest_by_source(observations)
    checksums: dict[str, str] = {}
    for source in sources:
        observation = latest.get(source.id)
        if observation is None:
            continue
        stored = data_root / observation.stored_path
        if not stored.exists():
            continue
        checksums[source.id] = document_content_checksum(
            segment_document(source, stored.read_bytes())
        )
    return checksums


def exit_code_for(verdicts: Sequence[SourceVerdict]) -> int:
    """Non-zero when a trusted source's *content* drifted from its pin."""
    if not GATE_ENABLED:
        return 0
    return 1 if any(verdict.blocking for verdict in verdicts) else 0
