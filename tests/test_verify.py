"""Drift detection. No network and no fetching: observations are constructed directly."""

import textwrap
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from cra_assistant.models import TrustTier
from cra_assistant.verify import (
    GATE_ENABLED,
    DriftStatus,
    PinFile,
    exit_code_for,
    load_pins,
    verify,
)
from factories import make_observation, make_source

PINNED = "sha256:" + "a" * 64
OBSERVED_SAME = PINNED
OBSERVED_OTHER = "sha256:" + "b" * 64
CONTENT_PINNED = "sha256:" + "c" * 64
CONTENT_OTHER = "sha256:" + "d" * 64


def verdicts_for(
    tier: TrustTier,
    *,
    observed_raw: str = OBSERVED_SAME,
    pinned_raw: str = PINNED,
    observed_content: str | None = CONTENT_PINNED,
    pinned_content: str | None = CONTENT_PINNED,
    fetched: bool = True,
):
    source = make_source("a-source", tier=tier)
    observations = [make_observation("a-source", observed_raw)] if fetched else []
    pins = PinFile.model_validate(
        {
            "pins": [
                {
                    "source_id": "a-source",
                    "checksum": pinned_raw,
                    "content_checksum": pinned_content,
                    "note": "approved in a test",
                }
            ]
        }
    )
    content = {"a-source": observed_content} if observed_content else {}
    return verify([source], observations, pins, content)


def test_an_unchanged_source_is_clean() -> None:
    (verdict,) = verdicts_for(TrustTier.TRUSTED)

    assert verdict.status is DriftStatus.CLEAN
    assert verdict.content_status is DriftStatus.CLEAN
    assert not verdict.blocking


def test_raw_drift_alone_never_blocks_even_for_a_trusted_source() -> None:
    """The EUR-Lex case: the bytes moved, the text did not.

    Two responses seconds apart differ inside an analytics attribute. If this
    blocked, the gate would fire on nearly every run and be overridden into
    uselessness.
    """
    (verdict,) = verdicts_for(TrustTier.TRUSTED, observed_raw=OBSERVED_OTHER)

    assert verdict.status is DriftStatus.DRIFTED
    assert verdict.content_status is DriftStatus.CLEAN
    assert not verdict.blocking
    assert exit_code_for([verdict]) == 0


def test_content_drift_on_a_trusted_source_blocks() -> None:
    (verdict,) = verdicts_for(TrustTier.TRUSTED, observed_content=CONTENT_OTHER)

    assert verdict.content_status is DriftStatus.DRIFTED
    assert verdict.blocking
    assert verdict.needs_acknowledgement
    assert exit_code_for([verdict]) == 1


def test_content_drift_on_an_untrusted_source_is_recorded_but_not_escalated() -> None:
    (verdict,) = verdicts_for(TrustTier.UNTRUSTED, observed_content=CONTENT_OTHER)

    assert verdict.content_status is DriftStatus.DRIFTED, "still detected and reported"
    assert not verdict.blocking, "a forum thread changing is not an event"
    assert exit_code_for([verdict]) == 0


def test_an_unpinned_trusted_content_checksum_blocks() -> None:
    """Nobody has approved this text, so it must not pass silently."""
    (verdict,) = verdicts_for(TrustTier.TRUSTED, pinned_content=None)

    assert verdict.content_status is DriftStatus.UNPINNED
    assert verdict.blocking


def test_an_unpinned_untrusted_content_checksum_does_not_block() -> None:
    (verdict,) = verdicts_for(TrustTier.UNTRUSTED, pinned_content=None)

    assert verdict.content_status is DriftStatus.UNPINNED
    assert not verdict.blocking


def test_an_unfetched_source_never_blocks() -> None:
    """A fresh clone has no data/ at all. Failing there would punish cloning."""
    (verdict,) = verdicts_for(TrustTier.TRUSTED, fetched=False, observed_content=None)

    assert verdict.status is DriftStatus.UNFETCHED
    assert verdict.content_status is DriftStatus.UNFETCHED
    assert not verdict.blocking
    assert exit_code_for([verdict]) == 0


def test_the_newest_observation_wins() -> None:
    """The manifest is append-only, so a source has a history. Only the latest counts."""
    source = make_source("a-source", tier=TrustTier.TRUSTED)
    observations = [
        make_observation("a-source", OBSERVED_OTHER),
        make_observation("a-source", OBSERVED_SAME, age=timedelta(days=1)),
    ]
    pins = PinFile.model_validate(
        {"pins": [{"source_id": "a-source", "checksum": PINNED, "note": "n"}]}
    )

    (verdict,) = verify([source], observations, pins)

    assert verdict.status is DriftStatus.DRIFTED
    assert verdict.observed_checksum == OBSERVED_OTHER


def test_the_gate_is_armed() -> None:
    assert GATE_ENABLED, "content checksums exist now, so drift can block honestly"


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
