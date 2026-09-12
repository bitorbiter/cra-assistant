"""CLI behaviour. `verify` needs no network; `fetch` is covered in test_fetch.py."""

from pathlib import Path

import pytest

from cra_assistant.cli import main, note_for
from cra_assistant.models import TrustTier
from cra_assistant.paths import DEFAULT_PINS_PATH, DEFAULT_REGISTRY_PATH
from cra_assistant.registry import load_registry
from cra_assistant.verify import DriftStatus, SourceVerdict, load_pins


def test_verify_on_a_fresh_clone_reports_without_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No data/ means nothing can be judged, so the armed gate must still pass."""
    exit_code = main(["--data-root", str(tmp_path), "verify"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "unfetched" in output
    assert "0 blocking" in output


def test_parse_without_fetched_bytes_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--data-root", str(tmp_path), "parse"])

    assert exit_code == 1
    assert "Run `cra-assistant fetch` first" in capsys.readouterr().err


def test_fetch_rejects_an_unknown_source_id(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="unknown source ids: no-such-source"):
        main(["--data-root", str(tmp_path), "fetch", "--source", "no-such-source"])


def test_every_declared_source_is_pinned() -> None:
    """Committed data must agree with itself: a source with no pin is an oversight."""
    declared = {source.id for source in load_registry(DEFAULT_REGISTRY_PATH).sources}
    pinned = {pin.source_id for pin in load_pins(DEFAULT_PINS_PATH).pins}

    assert declared == pinned, f"unpinned: {declared - pinned}; stale pins: {pinned - declared}"


@pytest.mark.parametrize(
    ("tier", "raw", "content", "expected"),
    [
        (TrustTier.TRUSTED, DriftStatus.CLEAN, DriftStatus.CLEAN, ""),
        (TrustTier.UNTRUSTED, DriftStatus.CLEAN, DriftStatus.CLEAN, ""),
        # Raw drift with clean content is the ordinary EUR-Lex refetch.
        (TrustTier.TRUSTED, DriftStatus.DRIFTED, DriftStatus.CLEAN, "recorded, no action needed"),
        (
            TrustTier.TRUSTED,
            DriftStatus.DRIFTED,
            DriftStatus.DRIFTED,
            "BLOCKING — acknowledge: update the pin and say why",
        ),
        (
            TrustTier.UNTRUSTED,
            DriftStatus.DRIFTED,
            DriftStatus.DRIFTED,
            "recorded, no action needed",
        ),
        (
            TrustTier.TRUSTED,
            DriftStatus.CLEAN,
            DriftStatus.UNPINNED,
            "BLOCKING — no pin yet: add one",
        ),
        # Status beats tier: an unfetched source needs fetching either way.
        (
            TrustTier.TRUSTED,
            DriftStatus.UNFETCHED,
            DriftStatus.UNFETCHED,
            "run `cra-assistant fetch`",
        ),
        (
            TrustTier.UNTRUSTED,
            DriftStatus.UNFETCHED,
            DriftStatus.UNFETCHED,
            "run `cra-assistant fetch`",
        ),
    ],
)
def test_the_report_note_matches_tier_and_status(
    tier: TrustTier, raw: DriftStatus, content: DriftStatus, expected: str
) -> None:
    verdict = SourceVerdict(
        source_id="a-source",
        tier=tier,
        status=raw,
        content_status=content,
        pinned_checksum=None,
        observed_checksum=None,
        pinned_content_checksum=None,
        observed_content_checksum=None,
    )

    assert note_for(verdict) == expected


def test_eval_refuses_unverified_items_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scoring drafted labels by default would produce a number that looks like
    a measurement and is not."""
    exit_code = main(["--data-root", str(tmp_path), "eval"])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "verified = false" in error
    assert "--include-unverified" in error


def test_eval_with_include_unverified_gets_past_the_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With an empty data root it then stops for the other reason: no corpus."""
    exit_code = main(["--data-root", str(tmp_path), "eval", "--include-unverified"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "verified = false" not in captured.err
    assert "Run `cra-assistant fetch` first" in captured.err
