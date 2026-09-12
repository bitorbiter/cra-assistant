"""CLI behaviour. `verify` needs no network; `fetch` is covered in test_fetch.py."""

from pathlib import Path

import pytest

from cra_assistant.cli import main
from cra_assistant.paths import DEFAULT_PINS_PATH, DEFAULT_REGISTRY_PATH
from cra_assistant.registry import load_registry
from cra_assistant.verify import load_pins


def test_verify_reports_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With an empty data root nothing is fetched, which verify reports without failing."""
    exit_code = main(["--data-root", str(tmp_path), "verify"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "unfetched" in output
    assert "GATE DISABLED" in output


def test_fetch_rejects_an_unknown_source_id(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="unknown source ids: no-such-source"):
        main(["--data-root", str(tmp_path), "fetch", "--source", "no-such-source"])


def test_every_declared_source_is_pinned() -> None:
    """Committed data must agree with itself: a source with no pin is an oversight."""
    declared = {source.id for source in load_registry(DEFAULT_REGISTRY_PATH).sources}
    pinned = {pin.source_id for pin in load_pins(DEFAULT_PINS_PATH).pins}

    assert declared == pinned, f"unpinned: {declared - pinned}; stale pins: {pinned - declared}"
