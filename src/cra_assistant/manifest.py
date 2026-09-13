"""The fetch manifest: what was actually downloaded, and when.

A :class:`~cra_assistant.models.Source` says what a document is. A
:class:`FetchObservation` says what came back the one time we asked for it.
Keeping the two apart is the reason ``Source`` has no checksum field.

The manifest is append-only and lives under ``data/``, which is not committed:
it is a log of observations made on one machine at one time, not a shared fact
about the project. The committed counterpart is ``registry/pins.toml``, which
records the checksums a human has actually looked at.
"""

import json
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from cra_assistant.models import SourceId, SourceUrl

Checksum = Annotated[
    str,
    Field(
        pattern=r"^sha256:[0-9a-f]{64}$",
        description="Algorithm-prefixed digest of the raw bytes as received.",
    ),
]


class FetchObservation(BaseModel):
    """One HTTP response, recorded. Immutable, because it already happened."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    retrieved_at: datetime
    requested_url: SourceUrl
    resolved_url: SourceUrl = Field(
        description="Where the request ended up after redirects. EUR-Lex and "
        "GitHub both redirect, and the difference is worth keeping."
    )
    http_status: int = Field(ge=100, le=599)
    content_type: str | None = Field(
        default=None, description="As served. Not trusted to pick a file extension."
    )
    byte_count: int = Field(ge=0)
    checksum: Checksum
    stored_path: str = Field(
        description="Path of the stored bytes, relative to the data root, so a "
        "manifest stays readable if the data directory moves."
    )
    segment_count: int | None = Field(
        default=None,
        ge=0,
        description="Segments the stored bytes produced at ingest. None in records "
        "written before this was recorded.",
    )
    item_counts: dict[str, int] = Field(
        default_factory=dict,
        description="For documents assembled from an API: how many items of each "
        "collection arrived, e.g. {'issues': 191, 'comments': 800}. Truncation shows "
        "up here as a number — a count sitting on a page-size multiple is worth a look "
        "— rather than only as an exception on the day it happens.",
    )


def append_observation(manifest_path: Path, observation: FetchObservation) -> None:
    """Append one observation as a JSON line.

    JSONL rather than one TOML or JSON document because the manifest is only
    ever appended to: a new observation costs one line and never rewrites, so a
    crash mid-fetch cannot corrupt earlier records.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(observation.model_dump_json() + "\n")


def load_manifest(manifest_path: Path) -> tuple[FetchObservation, ...]:
    """Read every observation, oldest first. A missing manifest is empty, not an error."""
    if not manifest_path.exists():
        return ()
    return tuple(_read_lines(manifest_path))


def _read_lines(manifest_path: Path) -> Iterator[FetchObservation]:
    with manifest_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{manifest_path}:{number}: not valid JSON") from error
            yield FetchObservation.model_validate(payload)


def latest_by_source(
    observations: Iterable[FetchObservation],
) -> dict[str, FetchObservation]:
    """The most recent observation per source.

    Ordering is by ``retrieved_at`` rather than by position in the file, so a
    manifest concatenated from two machines still yields the newest record.
    """
    latest: dict[str, FetchObservation] = {}
    for observation in observations:
        current = latest.get(observation.source_id)
        if current is None or observation.retrieved_at >= current.retrieved_at:
            latest[observation.source_id] = observation
    return latest
