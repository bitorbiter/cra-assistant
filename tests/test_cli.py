"""CLI behaviour. `verify` needs no network; `fetch` is covered in test_fetch.py."""

import argparse
import hashlib
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cra_assistant.cli import main, note_for
from cra_assistant.fetch import MANIFEST_FILENAME
from cra_assistant.manifest import FetchObservation, append_observation
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


# --- end to end against a real corpus, offline -------------------------------
#
# Every CLI test above runs against an empty data root, so `segments_for`
# returns nothing and each command exits early. That gap let a genuine bug ship:
# `build_retriever` still unpacked the two-tuple `segments_for` returned before
# it grew a `raw` element, and `ask` crashed with "too many values to unpack" on
# any real corpus. These tests build a small corpus so the commands actually run.


@pytest.fixture
def corpus(tmp_path: Path) -> argparse.Namespace:
    """A data root and registry holding one real (excerpted) document."""
    excerpt = (Path(__file__).parent / "fixtures" / "cra_excerpt_en.html").read_bytes()
    registry = tmp_path / "sources.toml"
    registry.write_text(
        textwrap.dedent("""
            [[sources]]
            id = "cra-eurlex-en"
            citation_prefix = "cra-en"
            short_title = "Regulation (EU) 2024/2847"
            title = "Cyber Resilience Act excerpt"
            url = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32024R2847"
            lang = "en"
            tier = "trusted"
            licence = "UNKNOWN"
            parser = "eurlex-html"
        """),
        encoding="utf-8",
    )

    data_root = tmp_path / "data"
    digest = hashlib.sha256(excerpt).hexdigest()
    stored = Path("raw") / "cra-eurlex-en" / f"{digest[:12]}.html"
    (data_root / stored).parent.mkdir(parents=True)
    (data_root / stored).write_bytes(excerpt)

    append_observation(
        data_root / MANIFEST_FILENAME,
        FetchObservation(
            source_id="cra-eurlex-en",
            retrieved_at=datetime.now(UTC),
            requested_url="https://eur-lex.europa.eu/x",
            resolved_url="https://eur-lex.europa.eu/x",
            http_status=200,
            content_type="text/html",
            byte_count=len(excerpt),
            checksum=f"sha256:{digest}",
            stored_path=stored.as_posix(),
        ),
    )
    return argparse.Namespace(registry=registry, data_root=data_root)


def run(corpus: argparse.Namespace, *arguments: str) -> int:
    return main(
        ["--registry", str(corpus.registry), "--data-root", str(corpus.data_root), *arguments]
    )


def test_ask_builds_a_retriever_over_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression: this crashed with "too many values to unpack"."""
    exit_code = run(corpus, "ask", "--show-prompt", "-k", "2", "What is the subject matter?")

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "CONTEXT" in output
    assert "cra-en:article:" in output


def test_parse_writes_segments_for_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(corpus, "parse") == 0
    assert "recitals" in capsys.readouterr().out
    assert (corpus.data_root / "segments" / "cra-eurlex-en.jsonl").exists()


def test_validate_runs_over_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(corpus, "validate") == 0
    assert "0 errors" in capsys.readouterr().out


def test_eval_scores_against_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(corpus, "eval", "--include-unverified") == 0
    assert "Drift report" not in capsys.readouterr().out


def test_verify_blocks_end_to_end_when_trusted_content_drifts(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole gate, exercised for real.

    The fixture corpus holds an excerpt of the regulation while the committed
    pin covers the full document, so the content checksums genuinely differ.
    That is exactly the condition the gate exists for, and it must exit 1.
    """
    exit_code = run(corpus, "verify")

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "BLOCKING" in output
    assert "1 blocking" in output
