"""Drift detection. No network and no fetching: observations are constructed directly."""

import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from cra_assistant.manifest import FetchObservation
from cra_assistant.models import Parser, Source, TrustTier
from cra_assistant.verify import (
    GATE_ENABLED,
    DriftStatus,
    PinFile,
    exit_code_for,
    load_pins,
    verify,
)

PINNED = "sha256:" + "a" * 64
OBSERVED_SAME = PINNED
OBSERVED_OTHER = "sha256:" + "b" * 64


def make_source(source_id: str, tier: TrustTier) -> Source:
    return Source.model_validate(
        {
            "id": source_id,
            "title": "Example",
            "url": f"https://example.org/{source_id}",
            "lang": "en",
            "tier": tier,
            "licence": "UNKNOWN",
            "parser": Parser.GENERIC_HTML,
        }
    )


def make_observation(
    source_id: str, checksum: str, *, age: timedelta = timedelta()
) -> FetchObservation:
    return FetchObservation(
        source_id=source_id,
        retrieved_at=datetime.now(UTC) - age,
        requested_url=f"https://example.org/{source_id}",
        resolved_url=f"https://example.org/{source_id}",
        http_status=200,
        content_type="text/html",
        byte_count=4,
        checksum=checksum,
        stored_path=f"raw/{source_id}/abc123abc123.html",
    )


def pin_file(source_id: str, checksum: str = PINNED) -> PinFile:
    return PinFile.model_validate(
        {"pins": [{"source_id": source_id, "checksum": checksum, "note": "approved in a test"}]}
    )


def test_an_unchanged_source_is_clean() -> None:
    source = make_source("trusted-doc", TrustTier.TRUSTED)

    (verdict,) = verify(
        [source], [make_observation("trusted-doc", OBSERVED_SAME)], pin_file("trusted-doc")
    )

    assert verdict.status is DriftStatus.CLEAN
    assert not verdict.needs_acknowledgement


def test_drift_on_a_trusted_source_needs_acknowledgement() -> None:
    source = make_source("trusted-doc", TrustTier.TRUSTED)

    (verdict,) = verify(
        [source], [make_observation("trusted-doc", OBSERVED_OTHER)], pin_file("trusted-doc")
    )

    assert verdict.status is DriftStatus.DRIFTED
    assert verdict.needs_acknowledgement
    assert verdict.pinned_checksum == PINNED
    assert verdict.observed_checksum == OBSERVED_OTHER


def test_drift_on_an_untrusted_source_is_recorded_but_not_escalated() -> None:
    source = make_source("forum-thread", TrustTier.UNTRUSTED)

    (verdict,) = verify(
        [source], [make_observation("forum-thread", OBSERVED_OTHER)], pin_file("forum-thread")
    )

    assert verdict.status is DriftStatus.DRIFTED, "drift is still detected and reported"
    assert not verdict.needs_acknowledgement, "a forum thread changing is not an event"


def test_an_unpinned_trusted_source_needs_acknowledgement() -> None:
    source = make_source("trusted-doc", TrustTier.TRUSTED)

    (verdict,) = verify([source], [make_observation("trusted-doc", OBSERVED_SAME)], PinFile())

    assert verdict.status is DriftStatus.UNPINNED
    assert verdict.needs_acknowledgement, "nobody has approved these bytes yet"


def test_an_unpinned_untrusted_source_does_not() -> None:
    source = make_source("forum-thread", TrustTier.UNTRUSTED)

    (verdict,) = verify([source], [make_observation("forum-thread", OBSERVED_SAME)], PinFile())

    assert verdict.status is DriftStatus.UNPINNED
    assert not verdict.needs_acknowledgement


def test_a_declared_but_unfetched_source_is_reported() -> None:
    source = make_source("trusted-doc", TrustTier.TRUSTED)

    (verdict,) = verify([source], [], PinFile())

    assert verdict.status is DriftStatus.UNFETCHED
    assert verdict.observed_checksum is None


def test_the_newest_observation_wins() -> None:
    """The manifest is append-only, so a source has a history. Only the latest counts."""
    source = make_source("trusted-doc", TrustTier.TRUSTED)
    observations = [
        make_observation("trusted-doc", OBSERVED_OTHER),
        make_observation("trusted-doc", OBSERVED_SAME, age=timedelta(days=1)),
    ]

    (verdict,) = verify([source], observations, pin_file("trusted-doc"))

    assert verdict.status is DriftStatus.DRIFTED
    assert verdict.observed_checksum == OBSERVED_OTHER


def test_the_gate_is_disabled_so_drift_does_not_fail_the_process() -> None:
    source = make_source("trusted-doc", TrustTier.TRUSTED)
    verdicts = verify(
        [source], [make_observation("trusted-doc", OBSERVED_OTHER)], pin_file("trusted-doc")
    )

    assert verdicts[0].needs_acknowledgement
    assert not GATE_ENABLED, "arming this is a step-3 change, with pins over extracted text"
    assert exit_code_for(verdicts) == 0


def test_a_pin_without_a_note_is_rejected() -> None:
    """An acknowledgement with no reasoning attached is a rubber stamp."""
    with pytest.raises(ValidationError, match="note"):
        PinFile.model_validate({"pins": [{"source_id": "trusted-doc", "checksum": PINNED}]})


def test_duplicate_pins_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate pinned source ids: trusted-doc"):
        PinFile.model_validate(
            {
                "pins": [
                    {"source_id": "trusted-doc", "checksum": PINNED, "note": "first"},
                    {"source_id": "trusted-doc", "checksum": OBSERVED_OTHER, "note": "second"},
                ]
            }
        )


def test_a_malformed_checksum_is_rejected() -> None:
    with pytest.raises(ValidationError, match="checksum"):
        PinFile.model_validate(
            {"pins": [{"source_id": "trusted-doc", "checksum": "deadbeef", "note": "no prefix"}]}
        )


def test_a_missing_pin_file_pins_nothing(tmp_path: Path) -> None:
    assert load_pins(tmp_path / "absent.toml").pins == ()


def test_the_committed_pin_file_is_valid() -> None:
    assert load_pins().pins, "the repository ships approved checksums"


def test_pins_are_loaded_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "pins.toml"
    path.write_text(
        textwrap.dedent(f"""
            [[pins]]
            source_id = "trusted-doc"
            checksum = "{PINNED}"
            note = "approved on a Tuesday"
        """),
        encoding="utf-8",
    )

    (pin,) = load_pins(path).pins
    assert (pin.source_id, pin.checksum, pin.note) == (
        "trusted-doc",
        PINNED,
        "approved on a Tuesday",
    )
